"""Таблицы instagram.posts и profile_anchors: лёгкая запись постов (--posts-only), якорь."""

import asyncpg

from core.timeutil import parse_dt

_INSERT = """
INSERT INTO posts (code, media_id, profile_id, url, kind, caption, taken_at, likes,
                   comments_count, views, hashtags, mentions, tagged_users, location_name,
                   location, is_pinned, comments_disabled, carousel_size)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
ON CONFLICT (code) DO UPDATE SET
    media_id = coalesce(posts.media_id, EXCLUDED.media_id),
    likes = EXCLUDED.likes, comments_count = EXCLUDED.comments_count,
    views = EXCLUDED.views, is_pinned = EXCLUDED.is_pinned, updated_at = now()
"""


def counters(p: dict) -> tuple:
    """Изменчивые поля поста: по ним решаем, нужна ли запись у известного поста."""
    return (p.get("likes"), p.get("comments_count"), p.get("views"), bool(p.get("is_pinned")))


async def known_posts(conn: asyncpg.Connection, pid: int) -> dict[str, tuple]:
    """Посты профиля в БД: code -> изменчивые поля (для «известных» и «без изменений»)."""
    rows = await conn.fetch(
        "SELECT code, likes, comments_count, views, is_pinned FROM posts WHERE profile_id = $1",
        pid,
    )
    return {r["code"]: counters(dict(r)) for r in rows}


async def save_post_light(
    conn: asyncpg.Connection, pid: int, post: dict, known: dict[str, tuple]
) -> str:
    """Пост без комментариев и медиа -> new | refreshed | unchanged.

    Известный пост перезаписывает только счётчики и закрепление; last_refreshed_at остаётся
    пустым (пост собран лёгким режимом, его добирает --fill). known дополняется."""
    code, now = post["code"], counters(post)
    old = known.get(code)
    if old == now:
        return "unchanged"
    await conn.execute(
        _INSERT,
        code,
        post["media_id"],
        pid,
        post["url"],
        post["kind"],
        post["caption"],
        post["taken_at"],
        post["likes"],
        post["comments_count"],
        post["views"],
        post["hashtags"],
        post["mentions"],
        post["tagged_users"],
        post["location_name"],
        post["location"],
        post["is_pinned"],
        post["comments_disabled"],
        post["carousel_size"],
    )
    known[code] = now
    return "refreshed" if old is not None else "new"


async def get_anchor(conn: asyncpg.Connection, pid: int) -> dict | None:
    row = await conn.fetchrow("SELECT * FROM profile_anchors WHERE profile_id = $1", pid)
    if not row:
        return None
    return {
        "newest_code": row["newest_code"],
        "newest_date": row["newest_date"].isoformat() if row["newest_date"] else None,
        "covered_from": row["covered_from"].isoformat() if row["covered_from"] else None,
        "known_posts": row["known_posts"],
    }


async def save_anchor(conn: asyncpg.Connection, pid: int, a: dict) -> None:
    await conn.execute(
        """
        INSERT INTO profile_anchors (profile_id, newest_code, newest_date, covered_from, known_posts)
        VALUES ($1,$2,$3,$4,$5)
        ON CONFLICT (profile_id) DO UPDATE SET
            newest_code = EXCLUDED.newest_code, newest_date = EXCLUDED.newest_date,
            covered_from = EXCLUDED.covered_from, known_posts = EXCLUDED.known_posts,
            updated_at = now()
        """,
        pid,
        a["newest_code"],
        parse_dt(a.get("newest_date")),
        parse_dt(a.get("covered_from")),
        a.get("known_posts", 0),
    )
