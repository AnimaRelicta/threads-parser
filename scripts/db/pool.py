"""Подключение к Postgres: пул asyncpg и разовые запросы из синхронного кода."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import TypeVar

import asyncpg

from config import Settings

SCHEMA = "threads"
T = TypeVar("T")


def require_url(st: Settings) -> str:
    """DATABASE_URL из scripts/.env; без него работать с БД нельзя."""
    if not st.database_url:
        raise SystemExit(
            "Не задан DATABASE_URL в scripts/.env, например: "
            "postgresql://USER:PASSWORD@localhost:5432/DBNAME"
        )
    return st.database_url


async def _init_conn(conn: asyncpg.Connection) -> None:
    """jsonb <-> python-объекты (runs.settings и т. п.), без ручного json.dumps."""
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: json.dumps(v, ensure_ascii=False),
        decoder=json.loads,
        schema="pg_catalog",
    )


def _server_settings() -> dict[str, str]:
    # все запросы кода идут без префикса схемы: threads.posts == posts
    return {"search_path": f"{SCHEMA},public", "application_name": "threads-parser"}


def _explain(e: Exception) -> SystemExit:
    return SystemExit(f"Не удалось подключиться к Postgres: {type(e).__name__}: {e}")


async def create_pool(st: Settings, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    """Пул на время одного asyncio-цикла (воркеры парсера берут из него соединения)."""
    try:
        return await asyncpg.create_pool(
            require_url(st),
            min_size=min_size,
            max_size=max_size,
            init=_init_conn,
            server_settings=_server_settings(),
        )
    except (OSError, asyncpg.PostgresError) as e:
        raise _explain(e) from e


async def connect(st: Settings) -> asyncpg.Connection:
    """Одно соединение (миграции, статистика)."""
    try:
        conn = await asyncpg.connect(require_url(st), server_settings=_server_settings())
    except (OSError, asyncpg.PostgresError) as e:
        raise _explain(e) from e
    await _init_conn(conn)
    return conn


def run_sync(st: Settings, fn: Callable[[asyncpg.Connection], Awaitable[T]]) -> T:
    """Выполняет fn(conn) в отдельном цикле и закрывает соединение.

    Для синхронных мест кода (accounts.py list, search.py --stats, хвост run() после
    asyncio.run): пул привязан к своему циклу и между циклами не переносится.
    """

    async def _inner() -> T:
        conn = await connect(st)
        try:
            return await fn(conn)
        finally:
            await conn.close()

    return asyncio.run(_inner())
