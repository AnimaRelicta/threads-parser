"""Список веток из файла (postlist.py): что уже лежит в posts и запись найденных веток."""

import asyncpg

from config import TH
from core.textfilter import FILTER_VERSION, classify, hashtags_mentions
from core.timeutil import parse_dt
from db.posts import _FILTER_OPEN, _keep_full, upsert_topic


async def existing_posts(conn: asyncpg.Connection, codes: list[str]) -> dict[str, dict]:
    """code -> {username, updated_at, parsed} для веток, которые уже есть в posts.

    parsed = ветка разобрана полностью (last_refreshed_at не NULL), её текст и фильтр
    список веток не перезаписывает, обновляются только счётчики.
    """
    rows = await conn.fetch(
        """
        SELECT code, author_username, updated_at, last_refreshed_at IS NOT NULL AS parsed
        FROM posts WHERE code = ANY($1::text[])
        """,
        codes,
    )
    return {
        r["code"]: {
            "username": r["author_username"],
            "updated_at": r["updated_at"],
            "parsed": r["parsed"],
        }
        for r in rows
    }


# Как у THREAD_LIGHT_SQL: новая ветка получает last_refreshed_at = NULL («не разобрана»),
# у существующей обновляются счётчики, а текст/дата/фильтр только пока она не разобрана.
# first_seen_via = 'search': ветка пришла не из профиля.
LISTED_POST_SQL = f"""
INSERT INTO posts (
    code, url, author_username, author_user_id, profile_id, text, topic_id, date_utc,
    likes, reply_count, reposts, quotes, hashtags, mentions,
    filter_status, filter_matched, filter_matched_in, filter_version, first_seen_via)
VALUES ($1, $2, $3, $4, (SELECT id FROM profiles WHERE lower(username) = lower($3)),
        $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, 'search')
ON CONFLICT (code) DO UPDATE SET
    profile_id = coalesce(posts.profile_id, EXCLUDED.profile_id),
    author_user_id = coalesce(posts.author_user_id, EXCLUDED.author_user_id),
    url = {_keep_full("url")},
    text = {_keep_full("text")},
    topic_id = {_keep_full("topic_id")},
    date_utc = {_keep_full("date_utc")},
    hashtags = CASE WHEN {_FILTER_OPEN} THEN EXCLUDED.hashtags ELSE posts.hashtags END,
    mentions = CASE WHEN {_FILTER_OPEN} THEN EXCLUDED.mentions ELSE posts.mentions END,
    filter_status = CASE WHEN {_FILTER_OPEN}
        THEN EXCLUDED.filter_status ELSE posts.filter_status END,
    filter_matched = CASE WHEN {_FILTER_OPEN}
        THEN EXCLUDED.filter_matched ELSE posts.filter_matched END,
    filter_matched_in = CASE WHEN {_FILTER_OPEN}
        THEN EXCLUDED.filter_matched_in ELSE posts.filter_matched_in END,
    filter_version = CASE WHEN {_FILTER_OPEN}
        THEN EXCLUDED.filter_version ELSE posts.filter_version END,
    likes = coalesce(EXCLUDED.likes, posts.likes),
    reply_count = coalesce(EXCLUDED.reply_count, posts.reply_count),
    reposts = coalesce(EXCLUDED.reposts, posts.reposts),
    quotes = coalesce(EXCLUDED.quotes, posts.quotes),
    updated_at = now()
RETURNING (xmax = 0) AS inserted
"""


async def save_listed_post(conn: asyncpg.Connection, p: dict) -> bool:
    """Записывает ветку со страницы. True = ветка новая, False = обновлена существующая."""
    topic = p.get("topic")
    text = p.get("text")
    status, matched, matched_in = classify(text, topic)
    tags, mentions = hashtags_mentions(text)
    async with conn.transaction():
        topic_id = await upsert_topic(conn, topic) if topic else None
        return bool(
            await conn.fetchval(
                LISTED_POST_SQL,
                p["code"],
                p.get("url") or f"{TH}/@{p['username']}/post/{p['code']}",
                p["username"],
                p.get("user_id"),
                text,
                topic_id,
                parse_dt(p.get("date_utc")),
                p.get("likes"),
                p.get("reply_count"),
                p.get("reposts"),
                p.get("quotes"),
                tags,
                mentions,
                status,
                matched,
                matched_in,
                FILTER_VERSION,
            )
        )
