"""Хранилище поиска в Postgres: очередь задач (search_jobs), посты и связи post_hits."""

import asyncpg

from db import search as repo
from serp.tasks import SearchTask


def job_key(task: SearchTask) -> str:
    """Ключ задачи в search_jobs: запрос+режим+окно (без учёта регистра запроса,
    чтобы «Олх» и «олх» в файле запросов не плодили две записи)."""
    return "|".join(
        (
            task.query.lower(),
            task.mode,
            task.after.isoformat() if task.after else "",
            task.before.isoformat() if task.before else "",
        )
    )


class SearchStore:
    """Общее для воркеров запуска; каждая операция берёт соединение из пула."""

    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def plan(
        self, tasks: list[SearchTask], force: bool
    ) -> tuple[list[tuple[SearchTask, int]], int]:
        """Заводит задачи в БД. Возвращает ([(задача, id)], сколько готовых пропущено).

        Готовое окно не перезапускается без --force. Повтор одной и той же задачи
        (одинаковый ключ) в списке схлопывается.
        """
        pending: list[tuple[SearchTask, int]] = []
        seen: set[int] = set()
        skipped = 0
        async with self.pool.acquire() as conn, conn.transaction():
            for task in tasks:
                job_id, status = await repo.ensure_job(
                    conn, job_key(task), task.query, task.mode, task.after, task.before
                )
                if job_id in seen:
                    continue
                seen.add(job_id)
                if status == "done" and not force:
                    skipped += 1
                else:
                    pending.append((task, job_id))
        return pending, skipped

    async def start_job(self, job_id: int, run_id: int, account: str) -> None:
        async with self.pool.acquire() as conn:
            await repo.start_job(conn, job_id, run_id, account)

    async def save_posts(self, job_id: int, posts: list[dict]) -> tuple[int, int]:
        async with self.pool.acquire() as conn:
            return await repo.save_hits(conn, job_id, posts)

    async def finish_job(self, job_id: int, found: int, new: int, dupes: int) -> None:
        async with self.pool.acquire() as conn:
            await repo.finish_job(conn, job_id, found, new, dupes)

    async def fail_job(self, job_id: int, error: str, retries: int = 0) -> None:
        async with self.pool.acquire() as conn:
            await repo.fail_job(conn, job_id, error, retries)

    async def requeue_job(self, job_id: int, error: str | None, count_retry: bool = True) -> None:
        async with self.pool.acquire() as conn:
            await repo.requeue_job(conn, job_id, error, count_retry)
