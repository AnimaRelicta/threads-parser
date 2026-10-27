"""Миграции: применяет db/migrations/*.sql по порядку и помнит применённые."""

from pathlib import Path

import asyncpg

from db.pool import SCHEMA

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOCK_ID = 7_410_001  # advisory-lock: два migrate одновременно не запустятся

BOOTSTRAP = f"""
CREATE SCHEMA IF NOT EXISTS {SCHEMA};
CREATE TABLE IF NOT EXISTS {SCHEMA}.schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def list_migrations() -> list[tuple[str, Path]]:
    """(версия, файл) по возрастанию; версия = имя файла без .sql (001_init)."""
    return [(p.stem, p) for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]


async def applied_versions(conn: asyncpg.Connection) -> set[str]:
    exists = await conn.fetchval(f"SELECT to_regclass('{SCHEMA}.schema_migrations') IS NOT NULL")
    if not exists:
        return set()
    rows = await conn.fetch(f"SELECT version FROM {SCHEMA}.schema_migrations")
    return {r["version"] for r in rows}


async def migrate(conn: asyncpg.Connection) -> list[str]:
    """Применяет недостающие миграции, каждую в своей транзакции. Возвращает версии."""
    done_now: list[str] = []
    await conn.fetchval("SELECT pg_advisory_lock($1)", LOCK_ID)
    try:
        await conn.execute(BOOTSTRAP)
        done = await applied_versions(conn)
        for version, path in list_migrations():
            if version in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(
                    f"INSERT INTO {SCHEMA}.schema_migrations (version) VALUES ($1)", version
                )
            done_now.append(version)
    finally:
        await conn.fetchval("SELECT pg_advisory_unlock($1)", LOCK_ID)
    return done_now


async def status(conn: asyncpg.Connection) -> dict:
    """Какие миграции применены, какие ждут, сколько строк в таблицах."""
    done = await applied_versions(conn)
    known = [v for v, _ in list_migrations()]
    tables: dict[str, int] = {}
    if done:
        names = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = $1 ORDER BY tablename", SCHEMA
        )
        for r in names:
            t = r["tablename"]
            tables[t] = await conn.fetchval(f'SELECT count(*) FROM {SCHEMA}."{t}"')
    return {
        "applied": [v for v in known if v in done],
        "pending": [v for v in known if v not in done],
        "unknown": sorted(done - set(known)),  # применены, а файла нет (старая сборка?)
        "tables": tables,
    }
