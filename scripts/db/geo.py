"""Локации профилей и постов (location_mentions): запись при сборе, пересчёт, выборки для CLI."""

import json
from dataclasses import dataclass, field

import asyncpg

from core.geo import (
    DEFAULT_TEXT_SOURCES,
    DETECTOR_VERSION,
    OTHER,
    Resolved,
    Signal,
    about_country,
    post_signals,
    profile_signals,
    resolve,
    text_location,
)
from core.textfix import fix_text

BATCH = 500
INSERT_SQL = """
INSERT INTO location_mentions
    (entity_type, entity_id, profile_id, source, country, city, matched_text, detector_version)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT DO NOTHING
"""  # без цели: любое нарушение location_mentions_dedup = «уже есть»
PROFILE_COLS = "id, title, og_description, bio, about_name, about_location, links, topics"


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _signals_of(row) -> list[Signal]:
    return profile_signals(
        about_location=row["about_location"],
        links=_json(row["links"]),
        bio=row["bio"],
        about_name=row["about_name"],
        topics=_json(row["topics"]),
    )


def _rows(entity_type: str, entity_id: str, profile_id: int | None, signals: list[Signal]):
    # OTHER в location_mentions не пишется (CHECK KZ|UZ): он лежит в profiles.about_country
    return [
        (entity_type, entity_id, profile_id, s.source, s.country, s.city, s.matched_text,
         DETECTOR_VERSION)
        for s in signals
        if s.country != OTHER
    ]  # fmt: skip


async def _thread_places(conn: asyncpg.Connection, profile_id: int) -> list[tuple]:
    """Самые частые места в ветках профиля (post_text): [(страна, город, веток)]."""
    rows = await conn.fetch(
        "SELECT country, city, count(DISTINCT entity_id) AS n FROM location_mentions "
        "WHERE entity_type = 'post' AND profile_id = $1 GROUP BY 1, 2 ORDER BY n DESC, 1, 2",
        profile_id,
    )
    return [(r["country"], r["city"], r["n"]) for r in rows]


async def _text_location(conn, row, sources: tuple[str, ...]) -> tuple[str | None, str | None]:
    """Колонки profiles.text_location / text_location_src по строке профиля и набору источников."""
    places = await _thread_places(conn, row["id"]) if "threads" in sources else None
    return text_location(
        title=row["title"],
        about_name=row["about_name"],
        bio=row["bio"],
        og_description=row["og_description"],
        thread_places=places,
        sources=sources,
    )


async def _write_profile(
    conn: asyncpg.Connection,
    row,
    replace: bool,
    text_sources: tuple[str, ...] = DEFAULT_TEXT_SOURCES,
) -> int:
    signals = _signals_of(row)
    loc, loc_src = await _text_location(conn, row, text_sources)
    pid = row["id"]
    async with conn.transaction():
        if replace:
            await conn.execute(
                "DELETE FROM location_mentions WHERE entity_type = 'profile' AND entity_id = $1",
                str(pid),
            )
        rows = _rows("profile", str(pid), pid, signals)
        if rows:
            await conn.executemany(INSERT_SQL, rows)
        await conn.execute(
            "UPDATE profiles SET about_country = $2, geo_version = $3, "
            "text_location = $4, text_location_src = $5 WHERE id = $1",
            pid,
            about_country(signals),
            DETECTOR_VERSION,
            loc,
            loc_src,
        )
    return len(rows)


async def save_geo(
    conn: asyncpg.Connection, row, text_sources: tuple[str, ...] = DEFAULT_TEXT_SOURCES
) -> Resolved:
    """Геосигналы профиля по строке с колонками PROFILE_COLS (bio, about, шапка).

    Без запросов к Threads и без повторного чтения профиля: строку отдаёт UPDATE ... RETURNING
    в `db/profile_data.py`."""
    await _write_profile(conn, row, replace=False, text_sources=text_sources)
    return resolve(_signals_of(row))


async def save_post_mentions(
    conn: asyncpg.Connection, profile_id: int | None, code: str, text: str | None
) -> int:
    """Упоминания городов в тексте ветки (source='post_text'). Вызывать внутри транзакции поста."""
    rows = _rows("post", code, profile_id, post_signals(text))
    if rows:
        await conn.executemany(INSERT_SQL, rows)
    await conn.execute("UPDATE posts SET geo_version = $2 WHERE code = $1", code, DETECTOR_VERSION)
    return len(rows)


@dataclass
class RescanResult:
    profiles: int = 0
    posts: int = 0
    mentions: int = 0
    errors: list[str] = field(default_factory=list)


async def rescan(
    conn: asyncpg.Connection,
    force: bool,
    log=print,
    text_sources: tuple[str, ...] = DEFAULT_TEXT_SOURCES,
) -> RescanResult:
    """Пересчёт по накопленным данным. Строки сущности заменяются целиком (уходят и ложные
    срабатывания старой версии справочника). Без force: geo_version пуст или меньше текущей."""
    res = RescanResult()
    last = 0
    while True:
        rows = await conn.fetch(
            f"SELECT {PROFILE_COLS} FROM profiles WHERE id > $1 "
            "AND ($2 OR geo_version IS NULL OR geo_version < $3) ORDER BY id LIMIT $4",
            last, force, DETECTOR_VERSION, BATCH,
        )  # fmt: skip
        if not rows:
            break
        for r in rows:
            try:
                res.mentions += await _write_profile(conn, r, replace=True, text_sources=text_sources)
                res.profiles += 1
            except Exception as e:  # один плохой профиль не останавливает пересчёт
                res.errors.append(f"profile {r['id']}: {e!r}")
        last = rows[-1]["id"]
        log(f"    профилей: {res.profiles}")
    last_code = ""
    while True:
        rows = await conn.fetch(
            "SELECT code, profile_id, text FROM posts WHERE code > $1 "
            "AND profile_id IS NOT NULL AND text IS NOT NULL AND text <> '' "
            "AND ($2 OR geo_version IS NULL OR geo_version < $3) ORDER BY code LIMIT $4",
            last_code, force, DETECTOR_VERSION, BATCH,
        )  # fmt: skip
        if not rows:
            break
        new_rows, codes = [], [r["code"] for r in rows]
        for r in rows:
            try:
                new_rows += _rows("post", r["code"], r["profile_id"], post_signals(r["text"]))
            except Exception as e:
                res.errors.append(f"post {r['code']}: {e!r}")
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM location_mentions WHERE entity_type = 'post' AND entity_id = ANY($1)",
                codes,
            )
            if new_rows:
                await conn.executemany(INSERT_SQL, new_rows)
            await conn.execute(
                "UPDATE posts SET geo_version = $2 WHERE code = ANY($1)", codes, DETECTOR_VERSION
            )
        res.posts += len(rows)
        res.mentions += len(new_rows)
        last_code = codes[-1]
        log(f"    постов: {res.posts}")
    return res


async def fill_text_location(
    conn: asyncpg.Connection,
    sources: tuple[str, ...],
    only_empty: bool = False,
    log=print,
) -> tuple[int, int]:
    """Пересчитывает только profiles.text_location по выбранным источникам (location_mentions и
    about_country не трогает). Возвращает (профилей просмотрено, с найденной локацией)."""
    seen = found = 0
    last = 0
    while True:
        rows = await conn.fetch(
            f"SELECT {PROFILE_COLS} FROM profiles WHERE id > $1 "
            "AND (NOT $2 OR text_location IS NULL) ORDER BY id LIMIT $3",
            last, only_empty, BATCH,
        )  # fmt: skip
        if not rows:
            return seen, found
        upd = []
        for r in rows:
            loc, src = await _text_location(conn, r, sources)
            upd.append((r["id"], loc, src))
            found += loc is not None
        await conn.executemany(
            "UPDATE profiles SET text_location = $2, text_location_src = $3 WHERE id = $1", upd
        )
        seen += len(rows)
        last = rows[-1]["id"]
        log(f"    профилей: {seen}, с локацией из текста: {found}")


FIX_COLS = ("bio", "title", "og_title", "og_description", "about_name", "about_location")


async def fix_encoding(
    conn: asyncpg.Connection, apply: bool, text_sources: tuple[str, ...], log=print
) -> tuple[int, dict[str, int]]:
    """Находит текстовые поля профилей с неразобранными `\\uXXXX` и записывает нормальный текст.

    apply=False: только считает. Для исправленных профилей заново считаются геосигналы и
    text_location (в био мог быть город). Возвращает (профилей, {колонка: сколько исправлено})."""
    cond = " OR ".join(f"{c} ~ '\\\\u[0-9a-fA-F]{{4}}'" for c in FIX_COLS)
    cols = ", ".join(FIX_COLS)
    rows = await conn.fetch(f"SELECT id, {cols} FROM profiles WHERE {cond} ORDER BY id")
    per_col = dict.fromkeys(FIX_COLS, 0)
    fixed_ids: list[int] = []
    for r in rows:
        new = {c: fix_text(r[c]) for c in FIX_COLS}
        changed = {c: v for c, v in new.items() if v != r[c]}
        if not changed:
            continue
        for c in changed:
            per_col[c] += 1
        fixed_ids.append(r["id"])
        if apply:
            sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(changed))
            await conn.execute(
                f"UPDATE profiles SET {sets} WHERE id = $1", r["id"], *changed.values()
            )
    if apply and fixed_ids:
        for i in range(0, len(fixed_ids), BATCH):
            chunk = await conn.fetch(
                f"SELECT {PROFILE_COLS} FROM profiles WHERE id = ANY($1)", fixed_ids[i : i + BATCH]
            )
            for r in chunk:
                await _write_profile(conn, r, replace=True, text_sources=text_sources)
        log(f"    геосигналы пересчитаны: {len(fixed_ids)}")
    return len(fixed_ids), per_col


# --- Чтение для CLI -------------------------------------------------------------------


@dataclass
class ProfileGeo:
    id: int
    username: str
    about_location: str | None
    resolved: Resolved
    posts: list[tuple[str, str | None, int]]  # (страна, город, сколько постов), по убыванию


async def load_profile_geo(conn: asyncpg.Connection) -> list[ProfileGeo]:
    """Итог по каждому профилю: надёжные источники -> resolve(); посты отдельно (слабый сигнал)."""
    profiles = await conn.fetch(
        "SELECT id, username, about_location, about_country FROM profiles ORDER BY username"
    )
    by_profile: dict[int, list[Signal]] = {}
    for r in await conn.fetch(
        "SELECT profile_id, source, country, city, matched_text FROM location_mentions "
        "WHERE entity_type = 'profile' AND profile_id IS NOT NULL"
    ):
        by_profile.setdefault(r["profile_id"], []).append(
            Signal(r["source"], r["country"], r["city"], r["matched_text"])
        )
    posts: dict[int, list[tuple[str, str | None, int]]] = {}
    for r in await conn.fetch(
        "SELECT profile_id, country, city, count(DISTINCT entity_id) AS n FROM location_mentions "
        "WHERE entity_type = 'post' AND profile_id IS NOT NULL GROUP BY 1, 2, 3 ORDER BY n DESC"
    ):
        posts.setdefault(r["profile_id"], []).append((r["country"], r["city"], r["n"]))
    out = []
    for p in profiles:
        signals = by_profile.get(p["id"], [])
        if p["about_country"] == OTHER or (
            p["about_country"] and not any(s.source == "about_location" for s in signals)
        ):
            signals = [*signals, Signal("about_location", p["about_country"], None, "")]
        out.append(
            ProfileGeo(
                p["id"],
                p["username"],
                p["about_location"],
                resolve(signals),
                posts.get(p["id"], []),
            )
        )
    return out


async def post_totals(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Сколько разных постов упоминает страну/город (город NULL = только страна)."""
    return await conn.fetch(
        "SELECT country, city, count(DISTINCT entity_id) AS n, "
        "count(DISTINCT profile_id) AS profiles FROM location_mentions "
        "WHERE entity_type = 'post' GROUP BY 1, 2 ORDER BY n DESC"
    )


async def source_totals(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Сколько сущностей и профилей дал каждый источник."""
    return await conn.fetch(
        "SELECT source, count(DISTINCT entity_id) AS entities, "
        "count(DISTINCT profile_id) AS profiles "
        "FROM location_mentions GROUP BY 1 ORDER BY entities DESC"
    )
