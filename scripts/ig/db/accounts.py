"""Таблица instagram.accounts: реестр аккаунтов Instagram (соединение уже в схеме instagram).

register_login и mark_run не зависят от схемы и берутся из db.accounts без изменений.
"""

import asyncpg

from core.timeutil import parse_dt
from db.accounts import STATUSES, mark_run, register_login  # noqa: F401  (реэкспорт)

COLS = (
    "name, username, user_id, cookies_file, status, proxy, cooldown_until, "
    "created_at, last_login_at, last_run_at, last_error"
)
EDITABLE = {
    "username",
    "user_id",
    "status",
    "proxy",
    "cooldown_until",
    "last_error",
    "last_login_at",
    "last_run_at",
}


async def fetch_accounts(conn: asyncpg.Connection) -> dict[str, dict]:
    rows = await conn.fetch(f"SELECT {COLS} FROM accounts ORDER BY name")
    return {r["name"]: dict(r) for r in rows}


async def fetch_account(conn: asyncpg.Connection, name: str) -> dict | None:
    row = await conn.fetchrow(f"SELECT {COLS} FROM accounts WHERE name = $1", name)
    return dict(row) if row else None


async def update_account(conn: asyncpg.Connection, name: str, **fields) -> None:
    """Точечное обновление колонок из EDITABLE."""
    bad = set(fields) - EDITABLE
    if bad:
        raise ValueError(f"Нельзя менять поля аккаунта: {', '.join(sorted(bad))}")
    if not fields:
        return
    sets = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, start=2))
    await conn.execute(f"UPDATE accounts SET {sets} WHERE name = $1", name, *fields.values())


async def set_cooldown(conn: asyncpg.Connection, name: str, seconds: int) -> None:
    """Аккаунт отдыхает seconds секунд (после 429); 0 снимает паузу."""
    await conn.execute(
        "UPDATE accounts SET cooldown_until = CASE WHEN $2 > 0 "
        "THEN now() + make_interval(secs => $2) END WHERE name = $1",
        name,
        float(seconds),
    )


def legacy_row(name: str, rec: dict) -> dict:
    """Запись реестра insta-parser (instagram_username, instagram_user_id) -> колонки accounts."""
    status = rec.get("status") if rec.get("status") in STATUSES else "active"
    return {
        "name": name,
        "username": rec.get("instagram_username") or rec.get("username") or None,
        "user_id": rec.get("instagram_user_id") or rec.get("user_id") or None,
        "cookies_file": rec.get("cookies_file") or f"{name}.json",
        "status": status,
        "proxy": (rec.get("proxy") or "").strip() or None,
        "created_at": parse_dt(rec.get("created_at")),
        "last_login_at": parse_dt(rec.get("last_login_at")),
        "last_run_at": parse_dt(rec.get("last_run_at")),
        "last_error": rec.get("last_error"),
    }


async def import_legacy(conn: asyncpg.Connection, reg: dict) -> tuple[int, int]:
    """Переносит записи accounts.json из insta-parser; существующие имена не трогает.

    Возвращает (добавлено, пропущено). Статистика и история запусков не переносятся.
    """
    added = skipped = 0
    for name, rec in reg.items():
        if not isinstance(rec, dict):
            continue
        r = legacy_row(name, rec)
        res = await conn.execute(
            """
            INSERT INTO accounts (name, username, user_id, cookies_file, status, proxy,
                                  created_at, last_login_at, last_run_at, last_error)
            VALUES ($1, $2, $3, $4, $5, $6, coalesce($7::timestamptz, now()), $8, $9, $10)
            ON CONFLICT DO NOTHING
            """,
            r["name"],
            r["username"],
            r["user_id"],
            r["cookies_file"],
            r["status"],
            r["proxy"],
            r["created_at"],
            r["last_login_at"],
            r["last_run_at"],
            r["last_error"],
        )
        if res.endswith(" 1"):
            added += 1
        else:
            skipped += 1
    return added, skipped
