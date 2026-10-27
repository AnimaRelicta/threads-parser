"""Таблица accounts: реестр аккаунтов Threads (замена data/accounts.json)."""

from datetime import datetime

import asyncpg

from core.timeutil import parse_dt

COLS = "name, username, user_id, cookies_file, status, proxy, created_at, last_login_at, last_run_at, last_error"
STATUSES = ("active", "needs_login", "disabled")
# поля, которые разрешено менять через update_account (имена колонок идут в SQL)
EDITABLE = {"username", "user_id", "status", "proxy", "last_error", "last_login_at", "last_run_at"}


async def fetch_accounts(conn: asyncpg.Connection) -> dict[str, dict]:
    """{имя: запись} по всем аккаунтам."""
    rows = await conn.fetch(f"SELECT {COLS} FROM accounts ORDER BY name")
    return {r["name"]: dict(r) for r in rows}


async def fetch_account(conn: asyncpg.Connection, name: str) -> dict | None:
    row = await conn.fetchrow(f"SELECT {COLS} FROM accounts WHERE name = $1", name)
    return dict(row) if row else None


async def register_login(
    conn: asyncpg.Connection, name: str, username: str | None, user_id: str | None
) -> None:
    """Новый аккаунт или повторный вход: статус active, время входа, сброс ошибки.

    Пустые username/user_id не затирают уже известные значения.
    """
    await conn.execute(
        """
        INSERT INTO accounts (name, username, user_id, cookies_file, last_login_at)
        VALUES ($1, $2, $3, $4, now())
        ON CONFLICT (name) DO UPDATE SET
            status        = 'active',
            last_login_at = now(),
            last_error    = NULL,
            user_id       = coalesce(EXCLUDED.user_id, accounts.user_id),
            username      = coalesce(EXCLUDED.username, accounts.username)
        """,
        name,
        username or None,
        user_id or None,
        f"{name}.json",
    )


async def update_account(conn: asyncpg.Connection, name: str, **fields) -> None:
    """Точечное обновление колонок из EDITABLE."""
    bad = set(fields) - EDITABLE
    if bad:
        raise ValueError(f"Нельзя менять поля аккаунта: {', '.join(sorted(bad))}")
    if not fields:
        return
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
    await conn.execute(f"UPDATE accounts SET {sets} WHERE name = $1", name, *fields.values())


async def mark_run(
    conn: asyncpg.Connection,
    name: str,
    status: str | None,
    error: str | None,
    username: str | None,
) -> None:
    """Итог запуска для аккаунта: время, ошибка, новый статус, ник (только если был пуст)."""
    await conn.execute(
        """
        UPDATE accounts SET
            last_run_at = now(),
            last_error  = $2,
            status      = coalesce($3::text, status),
            username    = CASE WHEN coalesce(username, '') = '' THEN $4::text ELSE username END
        WHERE name = $1
        """,
        name,
        error,
        status,
        username,
    )


async def import_legacy(conn: asyncpg.Connection, reg: dict) -> tuple[int, int]:
    """Переносит записи старого accounts.json. Уже существующие имена не трогает.

    Возвращает (добавлено, пропущено). Счётчики и runs[] здесь не переносятся.
    """
    added = skipped = 0
    for name, r in reg.items():
        if not isinstance(r, dict):
            continue
        status = r.get("status") if r.get("status") in STATUSES else "active"
        created: datetime | None = parse_dt(r.get("created_at"))
        res = await conn.execute(
            """
            INSERT INTO accounts (name, username, user_id, cookies_file, status, proxy,
                                  created_at, last_login_at, last_run_at, last_error)
            VALUES ($1, $2, $3, $4, $5, $6, coalesce($7::timestamptz, now()), $8, $9, $10)
            ON CONFLICT DO NOTHING
            """,
            name,
            r.get("username") or None,
            r.get("user_id") or None,
            r.get("cookies_file") or f"{name}.json",
            status,
            (r.get("proxy") or "").strip() or None,
            created,
            parse_dt(r.get("last_login_at")),
            parse_dt(r.get("last_run_at")),
            r.get("last_error"),
        )
        if res.endswith(" 1"):
            added += 1
        else:
            skipped += 1
    return added, skipped
