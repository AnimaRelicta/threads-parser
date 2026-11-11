"""Миграции: применяет *.sql из каталога по порядку и помнит применённые (по схеме)."""

import re
from pathlib import Path

import asyncpg

from db.pool import SCHEMA

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOCK_ID = 7_410_001  # advisory-lock: два migrate одновременно не запустятся
_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _check_schema(schema: str) -> str:
    """Имя схемы подставляется в SQL текстом, поэтому проверяем его формат."""
    if not _SCHEMA_RE.match(schema):
        raise ValueError(f"Недопустимое имя схемы: {schema!r}")
    return schema


def bootstrap_sql(schema: str) -> str:
    _check_schema(schema)
    return f"""
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.schema_migrations (
    version    TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def list_migrations(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, Path]]:
    """(версия, файл) по возрастанию; версия = имя файла без .sql (001_init)."""
    return [(p.stem, p) for p in sorted(directory.glob("*.sql"))]


async def applied_versions(conn: asyncpg.Connection, schema: str = SCHEMA) -> set[str]:
    _check_schema(schema)
    exists = await conn.fetchval(f"SELECT to_regclass('{schema}.schema_migrations') IS NOT NULL")
    if not exists:
        return set()
    rows = await conn.fetch(f"SELECT version FROM {schema}.schema_migrations")
    return {r["version"] for r in rows}


async def migrate(
    conn: asyncpg.Connection,
    schema: str = SCHEMA,
    directory: Path = MIGRATIONS_DIR,
    lock_id: int = LOCK_ID,
) -> list[str]:
    """Применяет недостающие миграции, каждую в своей транзакции. Возвращает версии."""
    done_now: list[str] = []
    await conn.fetchval("SELECT pg_advisory_lock($1)", lock_id)
    try:
        await conn.execute(bootstrap_sql(schema))
        done = await applied_versions(conn, schema)
        for version, path in list_migrations(directory):
            if version in done:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute(
                    f"INSERT INTO {schema}.schema_migrations (version) VALUES ($1)", version
                )
            done_now.append(version)
    finally:
        await conn.fetchval("SELECT pg_advisory_unlock($1)", lock_id)
    return done_now


async def status(
    conn: asyncpg.Connection, schema: str = SCHEMA, directory: Path = MIGRATIONS_DIR
) -> dict:
    """Какие миграции применены, какие ждут, сколько строк в таблицах."""
    done = await applied_versions(conn, schema)
    known = [v for v, _ in list_migrations(directory)]
    tables: dict[str, int] = {}
    if done:
        names = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = $1 ORDER BY tablename", schema
        )
        for r in names:
            t = r["tablename"]
            tables[t] = await conn.fetchval(f'SELECT count(*) FROM {schema}."{t}"')
    return {
        "applied": [v for v in known if v in done],
        "pending": [v for v in known if v not in done],
        "unknown": sorted(done - set(known)),  # применены, а файла нет (старая сборка?)
        "tables": tables,
    }
