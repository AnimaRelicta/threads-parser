"""Таблица instagram.profiles: занос из источников, список, статистика."""

from collections import Counter
from dataclasses import dataclass, field

import asyncpg

from ig.db.sources import Candidate, Update, plan_update

CHUNK = 1000


@dataclass
class ImportReport:
    """Итог занесения: сколько нового, сколько уже было, что поменялось у существующих."""

    read: int = 0
    added: int = 0
    existed: int = 0
    sources_extended: int = 0
    confidence_raised: int = 0
    by_source: dict[str, Counter] = field(default_factory=dict)  # источник -> added/existed

    def count(self, source: str, key: str) -> None:
        self.by_source.setdefault(source, Counter())[key] += 1


async def fetch_existing(conn: asyncpg.Connection, nicks: list[str], lock: bool) -> dict[str, dict]:
    """Строки profiles по нижнему регистру ника; lock=True блокирует их до конца транзакции."""
    sql = (
        "SELECT id, lower(username) AS nick, source, sources, link_confidence, link_source, "
        "threads_profile_id, threads_username FROM profiles WHERE lower(username) = ANY($1::text[])"
    )
    if lock:
        sql += " FOR UPDATE"
    out: dict[str, dict] = {}
    for i in range(0, len(nicks), CHUNK):
        for r in await conn.fetch(sql, nicks[i : i + CHUNK]):
            out[r["nick"]] = dict(r)
    return out


async def _insert_new(conn: asyncpg.Connection, items: list[Candidate]) -> set[str]:
    """Вставляет новые профили; возвращает ники, реально вставленные (гонка — пропуск)."""
    done: set[str] = set()
    for i in range(0, len(items), CHUNK):
        part = items[i : i + CHUNK]
        rows = await conn.fetch(
            """
            INSERT INTO profiles (username, source, sources, source_ref, threads_profile_id,
                                  threads_username, link_confidence, link_source)
            SELECT u.username, u.source, string_to_array(u.sources, ','), u.source_ref,
                   u.threads_profile_id, u.threads_username, u.link_confidence, u.link_source
            FROM unnest($1::text[], $2::text[], $3::text[], $4::text[], $5::bigint[],
                        $6::text[], $7::text[], $8::text[])
                 AS u(username, source, sources, source_ref, threads_profile_id,
                      threads_username, link_confidence, link_source)
            ON CONFLICT DO NOTHING
            RETURNING lower(username) AS nick
            """,
            [c.username for c in part],
            [c.source for c in part],
            [",".join(c.sources) for c in part],
            [c.source_ref for c in part],
            [c.threads_profile_id for c in part],
            [c.threads_username for c in part],
            [c.link_confidence for c in part],
            [c.link_source for c in part],
        )
        done.update(r["nick"] for r in rows)
    return done


async def _apply_updates(conn: asyncpg.Connection, updates: list[tuple[int, Update]]) -> None:
    await conn.executemany(
        "UPDATE profiles SET sources = $2, link_confidence = $3, link_source = $4, "
        "threads_profile_id = $5, threads_username = $6 WHERE id = $1",
        [
            (
                pid,
                u.values["sources"],
                u.values["link_confidence"],
                u.values["link_source"],
                u.values["threads_profile_id"],
                u.values["threads_username"],
            )
            for pid, u in updates
        ],
    )


async def add_candidates(
    conn: asyncpg.Connection, cands: list[Candidate], dry_run: bool = False
) -> ImportReport:
    """Заносит кандидатов (ники уже свёрнуты collapse): новые вставляет, у старых — слияние.

    dry_run: только читает и считает, в БД ничего не пишет."""
    rep = ImportReport(read=len(cands))
    if not cands:
        return rep
    async with conn.transaction():
        existing = await fetch_existing(conn, [c.username for c in cands], lock=not dry_run)
        fresh = [c for c in cands if c.username not in existing]
        inserted = {c.username for c in fresh} if dry_run else await _insert_new(conn, fresh)
        updates: list[tuple[int, Update]] = []
        for c in cands:
            row = existing.get(c.username)
            if row is None and c.username in inserted:
                rep.added += 1
                rep.count(c.source, "added")
                continue
            rep.existed += 1
            rep.count(c.source, "existed")
            upd = plan_update(row, c) if row else None
            if upd:
                rep.sources_extended += upd.sources_added
                rep.confidence_raised += upd.link_raised
                updates.append((row["id"], upd))
        if not dry_run:
            await _apply_updates(conn, updates)
    return rep


async def list_profiles(
    conn: asyncpg.Connection, source: str | None, status: str | None, limit: int
) -> list[asyncpg.Record]:
    """Профили для `ig profiles list`: свежие сверху; source ищется среди всех источников."""
    return await conn.fetch(
        """
        SELECT id, username, source, sources, status, processed_mode, link_confidence,
               followers_count, added_at
        FROM profiles
        WHERE ($1::text IS NULL OR $1 = ANY(sources)) AND ($2::text IS NULL OR status = $2)
        ORDER BY id DESC LIMIT $3
        """,
        source,
        status,
        limit,
    )


async def profile_stats(conn: asyncpg.Connection) -> dict[str, list[tuple[str, int]]]:
    """Счётчики для `ig profiles stats`: источники, статусы, processed_mode, связь с threads."""
    out: dict[str, list[tuple[str, int]]] = {}
    out["total"] = [("всего", await conn.fetchval("SELECT count(*) FROM profiles"))]
    queries = {
        "первый источник": "SELECT source AS k, count(*) AS n FROM profiles GROUP BY 1",
        "встречались в источнике": (
            "SELECT s AS k, count(*) AS n FROM profiles, unnest(sources) s GROUP BY 1"
        ),
        "статус": "SELECT status AS k, count(*) AS n FROM profiles GROUP BY 1",
        "обработка (processed_mode)": (
            "SELECT coalesce(processed_mode, 'не обработан') AS k, count(*) AS n "
            "FROM profiles GROUP BY 1"
        ),
        "связь с threads": (
            "SELECT coalesce(link_confidence, 'нет связи') AS k, count(*) AS n "
            "FROM profiles GROUP BY 1"
        ),
    }
    for title, sql in queries.items():
        rows = await conn.fetch(sql + " ORDER BY n DESC, k")
        out[title] = [(r["k"], r["n"]) for r in rows]
    return out


# ---------- выбор профилей для парсинга ----------
MODE_RANK = {"profile": 1, "posts": 2, "full": 3}
_RANK_SQL = (
    "CASE processed_mode WHEN 'profile' THEN 1 WHEN 'posts' THEN 2 WHEN 'full' THEN 3 ELSE 0 END"
)
META_COLS = (
    "ig_user_id",
    "full_name",
    "biography",
    "bio_links",
    "external_url",
    "category",
    "followers_count",
    "following_count",
    "posts_count",
    "is_private",
    "is_verified",
    "is_business",
    "profile_pic_url",
)


async def list_targets(conn: asyncpg.Connection, spec: str) -> list[tuple[int, str]]:
    """Профили с is_target: spec = all | new (ещё без постов) | ник,ник (только существующие)."""
    spec = spec.strip()
    if spec == "all":
        rows = await conn.fetch("SELECT id, username FROM profiles WHERE is_target ORDER BY id")
    elif spec == "new":
        rows = await conn.fetch(
            "SELECT id, username FROM profiles p WHERE is_target "
            "AND NOT EXISTS (SELECT 1 FROM posts WHERE profile_id = p.id) ORDER BY id"
        )
    else:
        nicks = [n.strip().lstrip("@").lower() for n in spec.split(",") if n.strip()]
        rows = await conn.fetch(
            "SELECT id, username FROM profiles WHERE is_target AND lower(username) = ANY($1) "
            "ORDER BY id",
            nicks,
        )
    return [(r["id"], r["username"]) for r in rows]


async def drop_processed(conn: asyncpg.Connection, ids: list[int], mode: str) -> list[int]:
    """Оставляет профили с обработкой слабее запрошенной (глубокий режим покрывает мелкий)."""
    rows = await conn.fetch(
        f"SELECT id FROM profiles WHERE id = ANY($1) AND {_RANK_SQL} < $2 ORDER BY id",
        ids,
        MODE_RANK[mode],
    )
    return [r["id"] for r in rows]


# ---------- запись результата ----------
async def get_ig_user_id(conn: asyncpg.Connection, pid: int) -> str | None:
    return await conn.fetchval("SELECT ig_user_id FROM profiles WHERE id = $1", pid)


async def save_meta(
    conn: asyncpg.Connection, pid: int, status: str, meta: dict | None, raw: dict | None
) -> None:
    """Статус профиля и метаданные; None-поля старое значение не затирают."""
    sets = [
        "status = $2",
        "meta_fetched_at = now()",
        "first_seen_at = coalesce(first_seen_at, now())",
    ]
    args: list = [pid, status]
    for col in META_COLS:
        v = (meta or {}).get(col)
        if v is not None:
            args.append(v)
            sets.append(f"{col} = ${len(args)}")
    if raw is not None:
        args.append(raw)
        sets.append(f"meta_raw = ${len(args)}")
    await conn.execute(f"UPDATE profiles SET {', '.join(sets)} WHERE id = $1", *args)


async def set_status(conn: asyncpg.Connection, pid: int, status: str) -> None:
    await conn.execute("UPDATE profiles SET status = $2 WHERE id = $1", pid, status)


async def rename_profile(conn: asyncpg.Connection, pid: int, username: str) -> None:
    await conn.execute("UPDATE profiles SET username = $2 WHERE id = $1", pid, username)


async def mark_processed(conn: asyncpg.Connection, pid: int, mode: str) -> None:
    """Профиль обработан в режиме mode; более глубокая отметка не понижается."""
    await conn.execute(
        f"UPDATE profiles SET last_scraped_at = now(), processed_mode = CASE WHEN $2 > {_RANK_SQL} "
        "THEN $3 ELSE processed_mode END WHERE id = $1",
        pid,
        MODE_RANK[mode],
        mode,
    )


# ---------- окно «Об аккаунте» ----------
ABOUT_COLS = (
    "joined_text",
    "joined_date",
    "account_location",
    "verified_text",
    "verified_date",
)


async def about_fetched(conn: asyncpg.Connection, pid: int) -> bool:
    return bool(
        await conn.fetchval("SELECT about_fetched_at IS NOT NULL FROM profiles WHERE id = $1", pid)
    )


async def save_about(conn: asyncpg.Connection, pid: int, fields: dict, raw: dict) -> None:
    """Поля окна «Об аккаунте» и всё окно в about_raw; None-поля старое значение не затирают.

    Не Казахстан (is_kz=False): is_target=false и только about_raw.
    «Аккаунт подтверждён» в окне поднимает is_verified в true (обратно в false не сбрасывает)."""
    sets = ["about_raw = $2", "about_fetched_at = now()"]
    args: list = [pid, raw]
    if fields.get("is_kz") is False:  # не Казахстан: не целевой, колонки окна пустые
        sets.append("is_target = false")
        await conn.execute(f"UPDATE profiles SET {', '.join(sets)} WHERE id = $1", *args)
        return
    for col in ABOUT_COLS:
        if fields.get(col) is not None:
            args.append(fields[col])
            sets.append(f"{col} = ${len(args)}")
    if fields.get("is_verified"):
        sets.append("is_verified = true")
    await conn.execute(f"UPDATE profiles SET {', '.join(sets)} WHERE id = $1", *args)
