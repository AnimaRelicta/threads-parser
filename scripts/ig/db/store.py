"""IgStore: пул соединений и методы записи для воркеров парсера (аналог ProfileStore)."""

import asyncpg

from ig.db import accounts as accounts_repo
from ig.db import posts as posts_repo
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

    async def about_fetched(self, username: str) -> bool:
        async with self.pool.acquire() as conn:
            return await repo.about_fetched(conn, self.pid(username))

    async def save_about(self, username: str, fields: dict, raw: dict) -> None:
        async with self.pool.acquire() as conn:
            await repo.save_about(conn, self.pid(username), fields, raw)

    async def mark_processed(self, username: str, mode: str) -> None:
        async with self.pool.acquire() as conn:
            await repo.mark_processed(conn, self.pid(username), mode)

    async def mark_error(self, username: str) -> None:
        async with self.pool.acquire() as conn:
            await repo.set_status(conn, self.pid(username), "error")

    async def posts_context(self, username: str) -> tuple[dict, dict | None]:
        """Известные посты профиля (code -> счётчики) и якорь (None — прохода ещё не было)."""
        pid = self.pid(username)
        async with self.pool.acquire() as conn:
            return await posts_repo.known_posts(conn, pid), await posts_repo.get_anchor(conn, pid)

    async def save_post_light(self, username: str, post: dict, known: dict) -> str:
        pid = self.pid(username)
        async with self.pool.acquire() as conn:
            return await posts_repo.save_post_light(conn, pid, post, known)

    async def save_anchor(self, username: str, anchor: dict) -> None:
        pid = self.pid(username)
        async with self.pool.acquire() as conn:
            await posts_repo.save_anchor(conn, pid, anchor)

    async def post_states(self, username: str) -> dict[str, dict]:
        pid = self.pid(username)
        async with self.pool.acquire() as conn:
            return await posts_repo.post_states(conn, pid)

    async def posts_to_fill(self, username: str, comments: bool, videos: bool) -> list[dict]:
        pid = self.pid(username)
        async with self.pool.acquire() as conn:
            return await posts_repo.posts_to_fill(conn, pid, comments, videos)

    async def update_post_fields(self, post: dict) -> None:
        async with self.pool.acquire() as conn:
            await posts_repo.update_post_fields(conn, post)

    async def known_comments(self, code: str) -> dict[str, int]:
        async with self.pool.acquire() as conn:
            return await posts_repo.known_comments(conn, code)

    async def save_comments(self, rows: list[dict]) -> None:
        async with self.pool.acquire() as conn:
            await posts_repo.save_comments(conn, rows)

    async def set_comments_state(self, code: str, status: str, depth: int) -> None:
        async with self.pool.acquire() as conn:
            await posts_repo.set_comments_state(conn, code, status, depth)

    async def save_media(self, code: str, rows: list[dict]) -> None:
        async with self.pool.acquire() as conn:
            await posts_repo.save_media(conn, code, rows)

    async def finish_post(self, code: str, media_status: str | None, failed: int) -> None:
        async with self.pool.acquire() as conn:
            await posts_repo.finish_post(conn, code, media_status, failed)

    async def avatar_url(self, username: str) -> str | None:
        pid = self.pid(username)
        async with self.pool.acquire() as conn:
            return await posts_repo.get_pic_url(conn, pid)

    async def set_avatar(self, username: str, file_path: str) -> None:
        pid = self.pid(username)
        async with self.pool.acquire() as conn:
            await posts_repo.set_avatar(conn, pid, file_path)

    async def set_cooldown(self, account: str, seconds: int) -> None:
        async with self.pool.acquire() as conn:
            await accounts_repo.set_cooldown(conn, account, seconds)
