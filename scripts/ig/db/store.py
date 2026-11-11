"""IgStore: пул соединений и методы записи для воркеров парсера (аналог ProfileStore)."""

import asyncpg

from ig.db import accounts as accounts_repo
from ig.db import profiles as repo


class IgStore:
    """Каждая запись берёт соединение из пула на время одного вызова.

    ids: ник в нижнем регистре -> profiles.id; после смены ника обновляется."""

    def __init__(self, pool: asyncpg.Pool, ids: dict[str, int]):
        self.pool = pool
        self.ids = ids

    def pid(self, username: str) -> int:
        return self.ids[username.lower()]

    async def stored_user_id(self, username: str) -> str | None:
        async with self.pool.acquire() as conn:
            return await repo.get_ig_user_id(conn, self.pid(username))

    async def save_meta(
        self, username: str, status: str, meta: dict | None, raw: dict | None
    ) -> str | None:
        """Записывает результат; возвращает примечание, если пришлось отказаться от ig_user_id."""
        pid, note = self.pid(username), None
        async with self.pool.acquire() as conn:
            try:
                await repo.save_meta(conn, pid, status, meta, raw)
            except asyncpg.UniqueViolationError:
                # тот же ig_user_id уже у другой строки (один аккаунт занесён под двумя никами)
                note = f"ig_user_id {meta.get('ig_user_id')} уже у другого профиля"
                await repo.save_meta(conn, pid, status, {**meta, "ig_user_id": None}, raw)
        return note

    async def rename(self, username: str, new: str) -> bool:
        """Новый ник профиля (тот же ig_user_id). False — ник занят другой строкой."""
        pid = self.pid(username)
        async with self.pool.acquire() as conn:
            try:
                await repo.rename_profile(conn, pid, new)
            except asyncpg.UniqueViolationError:
                return False
        self.ids[new.lower()] = self.ids.pop(username.lower())
        return True

    async def mark_processed(self, username: str, mode: str) -> None:
        async with self.pool.acquire() as conn:
            await repo.mark_processed(conn, self.pid(username), mode)

    async def mark_error(self, username: str) -> None:
        async with self.pool.acquire() as conn:
            await repo.set_status(conn, self.pid(username), "error")

    async def set_cooldown(self, account: str, seconds: int) -> None:
        async with self.pool.acquire() as conn:
            await accounts_repo.set_cooldown(conn, account, seconds)
