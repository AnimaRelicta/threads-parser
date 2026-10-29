"""Спам-проверка профилей в БД: выборка веток, запись вердикта и бана, разбан."""

import asyncpg

from core.spam import RULES, SpamRules, Verdict, analyze

RECENT_SQL = """
SELECT extract(epoch FROM date_utc)::bigint AS ts, text, likes, reply_count AS replies,
       reposts, quotes
FROM posts
WHERE profile_id = $1 AND date_utc IS NOT NULL
ORDER BY date_utc DESC
LIMIT $2
"""

# is_banned выставляется, только если профиль не в белом списке (ручной разбан)
SAVE_SQL = """
UPDATE profiles SET
    spam_score = $2,
    spam_signals = $3::jsonb,
    spam_checked_at = now(),
    is_banned = ($4 AND NOT spam_whitelist),
    ban_reason = CASE WHEN $4 AND NOT spam_whitelist THEN $5 ELSE NULL END,
    banned_at = CASE WHEN $4 AND NOT spam_whitelist THEN coalesce(banned_at, now()) END
WHERE id = $1 AND NOT is_banned
RETURNING is_banned
"""


def _missing_migration(e: asyncpg.UndefinedColumnError) -> SystemExit:
    return SystemExit(
        f"В БД нет колонок бана ({e}). Примените миграцию 006: uv run scripts/cli.py db migrate"
    )


async def banned_reason(conn: asyncpg.Connection, profile_id: int) -> str | None:
    """Причина бана или None, если профиль не забанен."""
    try:
        row = await conn.fetchrow(
            "SELECT is_banned, ban_reason FROM profiles WHERE id = $1", profile_id
        )
    except asyncpg.UndefinedColumnError as e:
        raise _missing_migration(e) from e
    if row and row["is_banned"]:
        return row["ban_reason"] or "забанен"
    return None


async def save_verdict(conn: asyncpg.Connection, profile_id: int, v: Verdict) -> bool:
    """Пишет вердикт; True, если профиль в итоге забанен этим вызовом."""
    try:
        row = await conn.fetchrow(
            SAVE_SQL,
            profile_id,
            v.score,
            {"signals": v.signals, "metrics": v.metrics, "sample": v.sample},
            v.is_spam,
            v.reason or None,
        )
    except asyncpg.UndefinedColumnError as e:
        raise _missing_migration(e) from e
    return bool(row and row["is_banned"])


async def recent_threads(
    conn: asyncpg.Connection, profile_id: int, limit: int = RULES.max_posts
) -> list[dict]:
    return [dict(r) for r in await conn.fetch(RECENT_SQL, profile_id, limit)]


async def followers_count(conn: asyncpg.Connection, profile_id: int) -> int | None:
    """Подписчики профиля из БД; None, если неизвестны (или нет миграции 007)."""
    try:
        return await conn.fetchval("SELECT followers_count FROM profiles WHERE id = $1", profile_id)
    except asyncpg.UndefinedColumnError:
        return None


async def check_profile(
    conn: asyncpg.Connection,
    profile_id: int,
    apply: bool = True,
    rules: SpamRules = RULES,
) -> Verdict:
    """Проверяет профиль по веткам из БД; apply=False только считает, ничего не пишет."""
    v = analyze(
        await recent_threads(conn, profile_id), rules, await followers_count(conn, profile_id)
    )
    if apply:
        await save_verdict(conn, profile_id, v)
    return v


async def unban(conn: asyncpg.Connection, username: str) -> bool:
    """Снимает бан и вносит профиль в белый список. False, если такого профиля нет."""
    row = await conn.fetchrow(
        """
        UPDATE profiles SET is_banned = false, ban_reason = NULL, banned_at = NULL,
                            spam_whitelist = true
        WHERE lower(username) = lower($1) RETURNING id
        """,
        username,
    )
    return row is not None
