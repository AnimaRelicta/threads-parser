"""Спам-проверка профилей в БД: выборка веток, запись вердикта и бана, разбан."""

from datetime import datetime

import asyncpg

from config import Settings
from core.spam import RULES, SpamRules, Verdict, analyze
from core.spamclass import CLASS_RULES, ClassRules, ClassVerdict, class_rules_with, classify

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


CLASS_SQL = """
UPDATE profiles SET spam_class = $2, spam_class_info = $3::jsonb, spam_class_at = now()
WHERE id = $1
"""


def class_rules(st: Settings) -> ClassRules:
    """Пороги классов 1/2/3 из настроек (SPAM_CLASS_*)."""
    return class_rules_with(
        st.spam_class_sample,
        st.spam_class_low_followers,
        st.spam_class_gap_hours,
        st.spam_class_many_replies,
        st.spam_class_few_replies,
    )


async def banned_reason(conn: asyncpg.Connection, profile_id: int) -> str | None:
    """Причина бана или None, если профиль не забанен."""
    row = await conn.fetchrow(
        "SELECT is_banned, ban_reason FROM profiles WHERE id = $1", profile_id
    )
    if row and row["is_banned"]:
        return row["ban_reason"] or "забанен"
    return None


async def save_verdict(conn: asyncpg.Connection, profile_id: int, v: Verdict) -> bool:
    """Пишет вердикт; True, если профиль в итоге забанен этим вызовом."""
    row = await conn.fetchrow(
        SAVE_SQL,
        profile_id,
        v.score,
        {"signals": v.signals, "metrics": v.metrics, "sample": v.sample},
        v.is_spam,
        v.reason or None,
    )
    return bool(row and row["is_banned"])


async def recent_threads(
    conn: asyncpg.Connection, profile_id: int, limit: int = RULES.max_posts
) -> list[dict]:
    return [dict(r) for r in await conn.fetch(RECENT_SQL, profile_id, limit)]


async def followers_count(conn: asyncpg.Connection, profile_id: int) -> int | None:
    """Подписчики профиля из БД; None, если неизвестны."""
    return await conn.fetchval("SELECT followers_count FROM profiles WHERE id = $1", profile_id)


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


async def save_class(conn: asyncpg.Connection, profile_id: int, cv: ClassVerdict) -> None:
    """Пишет класс профиля (или NULL, если класса нет). Мало данных: прежний класс не трогаем."""
    if cv.no_data:
        return
    await conn.execute(
        CLASS_SQL,
        profile_id,
        cv.code,
        {"reason": cv.reason, "sample": cv.sample, "metrics": cv.metrics},
    )


async def check_class(
    conn: asyncpg.Connection,
    profile_id: int,
    apply: bool = True,
    rules: ClassRules = CLASS_RULES,
) -> ClassVerdict:
    """Класс профиля по свежим веткам из БД; apply=False только считает, ничего не пишет."""
    cv = classify(
        await recent_threads(conn, profile_id, rules.sample),
        await followers_count(conn, profile_id),
        rules,
    )
    if apply:
        await save_class(conn, profile_id, cv)
    return cv


async def class_state(
    conn: asyncpg.Connection, profile_id: int
) -> tuple[int | None, datetime | None]:
    """(класс, когда считали): время пусто = класс ещё ни разу не считали."""
    row = await conn.fetchrow(
        "SELECT spam_class, spam_class_at FROM profiles WHERE id = $1", profile_id
    )
    return (row["spam_class"], row["spam_class_at"]) if row else (None, None)


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
