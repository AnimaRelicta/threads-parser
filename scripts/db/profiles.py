"""Хранилище профильного пайплайна поверх пула: одно обращение = одно соединение."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import asyncpg

from core.consts import USERNAME_RE
from core.fsio import normalize_username
from core.geo import Resolved
from db import posts as repo
from db.profile_data import save_profile_data
from db.runs import ensure_profile

SOURCE_SQL = {
    "all": "p.is_target",
    "new": "p.is_target AND NOT EXISTS (SELECT 1 FROM posts t WHERE t.profile_id = p.id)",
}


# Глубина обработки: более глубокий режим покрывает более мелкий
LEVELS = ("profile", "threads", "full")
LEVEL_OF = {
    "profile": "profile",
    "threads": "threads",
    "full": "full",
    "only_new": "full",
    "fill": "full",
}
# Результаты, после которых профиль обработан: ok/empty, 404.
# abort/error (429, сбой) обработкой не считаются.
DONE_STATUSES = ("ok", "empty", "not_found")


def levels_covering(mode: str) -> list[str]:
    """Уровни processed_mode, которые уже покрывают режим запуска (fill обрабатывает сам)."""
    need = LEVEL_OF.get(mode)
    return list(LEVELS[LEVELS.index(need) :]) if need and mode != "fill" else []


async def drop_processed(conn: asyncpg.Connection, names: list[str], mode: str) -> list[str]:
    """names без профилей, уже обработанных в этом режиме (или глубже)."""
    levels = levels_covering(mode)
    if not levels:
        return names
    rows = await conn.fetch(
        "SELECT lower(username) AS u FROM profiles "
        "WHERE lower(username) = ANY($1) AND processed_mode = ANY($2)",
        [n.lower() for n in names],
        levels,
    )
    done = {r["u"] for r in rows}
    return [n for n in names if n.lower() not in done]


MARK_SQL = """
UPDATE profiles SET processed_mode = $2
WHERE lower(username) = lower($1)
  AND coalesce(array_position($3::text[], processed_mode), 0) < array_position($3::text[], $2)
"""


def parse_source(value: str) -> tuple[str, list[str]]:
    """('all'|'new', []) или ('list', [ники]) для «ник1,ник2»."""
    v = value.strip()
    if v.lower() in SOURCE_SQL:
        return v.lower(), []
    names = []
    for part in v.split(","):
        name = normalize_username(part)
        if not name or not USERNAME_RE.match(name):
            raise SystemExit(
                f"--source: «{part.strip()}» не ник и не одно из: {', '.join(SOURCE_SQL)}"
            )
        names.append(name)
    return "list", names


async def list_targets(conn: asyncpg.Connection, source: str) -> list[str]:
    """Ники профилей для запуска: all | new | «ник1,ник2». Из БД берутся только is_target."""
    kind, names = parse_source(source)
    if kind == "list":
        return names
    rows = await conn.fetch(
        f"SELECT p.username FROM profiles p WHERE {SOURCE_SQL[kind]} ORDER BY p.id"
    )
    return [r["username"] for r in rows]


@dataclass
class ProfileState:
    """Что уже известно о профиле перед проходом (раньше: posts/skipped/anchor.json)."""

    profile_id: int
    posts: list[dict] = field(default_factory=list)
    skipped: dict = field(default_factory=dict)
    anchor: dict | None = None


class ProfileStore:
    """Общий для всех воркеров запуска; media_root = каталог вывода (OUTPUT_DIR)."""

    def __init__(self, pool: asyncpg.Pool, media_root: Path):
        self.pool = pool
        self.media_root = media_root

    async def load(self, username: str) -> ProfileState:
        async with self.pool.acquire() as conn:
            pid = await ensure_profile(conn, username)
            return ProfileState(
                profile_id=pid,
                posts=await repo.load_profile_posts(conn, pid),
                skipped=await repo.load_skipped(conn, pid),
                anchor=await repo.load_anchor(conn, pid),
            )

    async def save_profile_data(
        self, profile_id: int, meta: dict, about=None, header=None, link=None
    ) -> Resolved:
        """Данные профиля одной записью (страница, about, шапка, Instagram) + геосигналы."""
        async with self.pool.acquire() as conn:
            return await save_profile_data(conn, profile_id, meta, about, header, link)

    async def about_fetched(self, profile_id: int) -> bool:
        async with self.pool.acquire() as conn:
            return await repo.about_fetched(conn, profile_id)

    async def save_post(self, profile_id: int, username: str, post: dict) -> None:
        async with self.pool.acquire() as conn:
            await repo.save_post(conn, profile_id, username, post, self.media_root)

    async def mark_skipped(
        self, profile_id: int, code: str, reason: str, date_str: str | None
    ) -> None:
        async with self.pool.acquire() as conn:
            await repo.mark_skipped(conn, profile_id, code, reason, date_str)

    async def save_anchor(self, profile_id: int, anchor: dict) -> None:
        async with self.pool.acquire() as conn:
            await repo.save_anchor(conn, profile_id, anchor)

    async def ensure_profile(self, username: str) -> int:
        """id профиля; заводит строку, если её нет (без чтения постов, в отличие от load)."""
        async with self.pool.acquire() as conn:
            return await ensure_profile(conn, username)

    async def mark_processed(self, username: str, mode: str) -> None:
        """Профиль обработан до конца в режиме mode; более глубокую отметку не понижает."""
        async with self.pool.acquire() as conn:
            await conn.execute(MARK_SQL, username, LEVEL_OF[mode], list(LEVELS))

    async def known_codes(self, profile_id: int) -> set[str]:
        async with self.pool.acquire() as conn:
            return await repo.known_codes(conn, profile_id)

    async def pending_threads(
        self, profile_id: int, cutoff: datetime | None, limit: int | None
    ) -> list[dict]:
        async with self.pool.acquire() as conn:
            return await repo.pending_threads(conn, profile_id, cutoff, limit)

    async def save_threads_light(self, profile_id: int, username: str, items: list[dict]) -> None:
        async with self.pool.acquire() as conn:
            await repo.save_threads_light(conn, profile_id, username, items)
