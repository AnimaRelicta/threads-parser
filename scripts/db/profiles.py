"""Хранилище профильного пайплайна поверх пула: одно обращение = одно соединение."""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import asyncpg

from core.consts import USERNAME_RE
from core.fsio import normalize_username
from core.spam import RULES, SpamRules, Verdict
from db import posts as repo
from db import spam as spam_repo
from db.runs import ensure_profile

SOURCE_SQL = {
    "all": "p.is_target AND NOT p.is_banned",
    "new": "p.is_target AND NOT p.is_banned "
    "AND NOT EXISTS (SELECT 1 FROM posts t WHERE t.profile_id = p.id)",
    "search": "p.is_target AND NOT p.is_banned AND p.discovered_via = 'search'",
    "authors": "p.is_target AND NOT p.is_banned AND p.discovered_via = 'author'",
}
NEEDS_DISCOVERY = ("search", "authors")


async def _has_column(conn: asyncpg.Connection, column: str) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'threads' AND table_name = 'profiles' AND column_name = $1",
            column,
        )
    )


def parse_source(value: str) -> tuple[str, list[str]]:
    """('all'|'new'|'search'|'authors', []) или ('list', [ники]) для «ник1,ник2»."""
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


async def list_targets(
    conn: asyncpg.Connection, source: str, review_status: str | None = None
) -> list[str]:
    """Ники профилей для запуска: all | new | search | authors | «ник1,ник2».

    search и authors опираются на profiles.discovered_via, review_status на
    profiles.review_status (миграция 003_discovery); без неё понятная ошибка.
    """
    kind, names = parse_source(source)
    if kind == "list":
        return names
    if (kind in NEEDS_DISCOVERY and not await _has_column(conn, "discovered_via")) or (
        review_status and not await _has_column(conn, "review_status")
    ):
        raise SystemExit(
            f"--source {kind}: в profiles нет discovered_via/review_status "
            "(миграция 003_discovery): uv run scripts/cli.py db migrate"
        )
    where, args = SOURCE_SQL[kind], []
    if review_status:
        where += " AND p.review_status = $1"
        args.append(review_status)
    try:
        rows = await conn.fetch(
            f"SELECT p.username FROM profiles p WHERE {where} ORDER BY p.id", *args
        )
    except asyncpg.UndefinedColumnError as e:
        raise SystemExit(
            f"В profiles нет колонок бана ({e}). Примените миграцию 006: "
            "uv run scripts/cli.py db migrate"
        ) from e
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

    def __init__(self, pool: asyncpg.Pool, media_root: Path, spam_rules: SpamRules = RULES):
        self.pool = pool
        self.media_root = media_root
        self.spam_rules = spam_rules  # пороги спам-детектора (в т. ч. SPAM_MIN_FOLLOWERS)

    async def load(self, username: str) -> ProfileState:
        async with self.pool.acquire() as conn:
            pid = await ensure_profile(conn, username)
            return ProfileState(
                profile_id=pid,
                posts=await repo.load_profile_posts(conn, pid),
                skipped=await repo.load_skipped(conn, pid),
                anchor=await repo.load_anchor(conn, pid),
            )

    async def save_meta(self, profile_id: int, meta: dict) -> None:
        async with self.pool.acquire() as conn:
            await repo.save_meta(conn, profile_id, meta)

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

    async def followers_count(self, profile_id: int) -> int | None:
        async with self.pool.acquire() as conn:
            return await spam_repo.followers_count(conn, profile_id)

    async def banned_reason(self, profile_id: int) -> str | None:
        """Причина бана профиля или None, если он не забанен."""
        async with self.pool.acquire() as conn:
            return await spam_repo.banned_reason(conn, profile_id)

    async def save_spam_verdict(self, profile_id: int, verdict: Verdict) -> bool:
        """Записывает вердикт спам-детектора; True, если профиль теперь забанен."""
        async with self.pool.acquire() as conn:
            return await spam_repo.save_verdict(conn, profile_id, verdict)
