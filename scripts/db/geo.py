"""Локации профилей и постов (location_mentions): запись при сборе, пересчёт, выборки для CLI."""

import json
from dataclasses import dataclass, field

import asyncpg

from core.geo import (
    DETECTOR_VERSION,
    OTHER,
    Resolved,
    Signal,
    about_country,
    post_signals,
    profile_signals,
    resolve,
)

BATCH = 500
INSERT_SQL = """
INSERT INTO location_mentions
    (entity_type, entity_id, profile_id, source, country, city, matched_text, detector_version)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT DO NOTHING
"""  # без цели: любое нарушение location_mentions_dedup = «уже есть»
PROFILE_COLS = "id, bio, about_name, about_location, links, topics"


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


async def _write_profile(conn: asyncpg.Connection, row, replace: bool) -> int:
    signals = _signals_of(row)
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
            "UPDATE profiles SET about_country = $2, geo_version = $3 WHERE id = $1",
            pid,
            about_country(signals),
            DETECTOR_VERSION,
        )
    return len(rows)


async def save_geo(conn: asyncpg.Connection, row) -> Resolved:
    """Геосигналы профиля по строке с колонками PROFILE_COLS (bio, about, шапка).

    Без запросов к Threads и без повторного чтения профиля: строку отдаёт UPDATE ... RETURNING
    в `db/profile_data.py`."""
    await _write_profile(conn, row, replace=False)
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


async def rescan(conn: asyncpg.Connection, force: bool, log=print) -> RescanResult:
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
                res.mentions += await _write_profile(conn, r, replace=True)
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
