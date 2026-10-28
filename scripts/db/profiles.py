"""Хранилище профильного пайплайна поверх пула: одно обращение = одно соединение."""

from dataclasses import dataclass, field
from pathlib import Path

import asyncpg

from db import posts as repo
from db.runs import ensure_profile


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
