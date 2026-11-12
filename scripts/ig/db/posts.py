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


# ---------- полный проход и --fill ----------
async def post_states(conn: asyncpg.Connection, pid: int) -> dict[str, dict]:
    """Состояние постов профиля: code -> что уже собрано (решает, нужен ли пост перепроверке)."""
    rows = await conn.fetch(
        """
        SELECT code, taken_at, comments_count, comments_disabled, comments_status,
               comments_collected, media_status, last_refreshed_at
        FROM posts WHERE profile_id = $1
        """,
        pid,
    )
    return {r["code"]: dict(r) for r in rows}


async def posts_to_fill(
    conn: asyncpg.Connection, pid: int, comments: bool, videos: bool
) -> list[dict]:
    """Посты для --fill: собранные лёгким режимом; плюс по флагам — пропущенные видео и
    посты без полностью собранных комментариев. Новые первыми."""
    rows = await conn.fetch(
        """
        SELECT code, media_id, taken_at, comments_count, comments_disabled, comments_status,
               comments_collected, media_status, last_refreshed_at
        FROM posts
        WHERE profile_id = $1 AND media_id IS NOT NULL AND (
              last_refreshed_at IS NULL
           OR ($2 AND media_status = 'skipped')
           OR ($3 AND NOT comments_disabled AND comments_status <> 'done'))
        ORDER BY taken_at DESC NULLS LAST
        """,
        pid,
        videos,
        comments,
    )
    return [dict(r) for r in rows]


async def update_post_fields(conn: asyncpg.Connection, post: dict) -> None:
    """Все поля поста из полного ответа (добор после --posts-only)."""
    await conn.execute(
        """
        UPDATE posts SET kind = $2, caption = $3, taken_at = coalesce($4, taken_at), likes = $5,
            comments_count = $6, views = $7, hashtags = $8, mentions = $9, tagged_users = $10,
            location_name = $11, location = $12, is_pinned = $13, comments_disabled = $14,
            carousel_size = $15, updated_at = now()
        WHERE code = $1
        """,
        post["code"],
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


async def known_comments(conn: asyncpg.Connection, code: str) -> dict[str, int]:
    """Комментарии поста в БД: id -> число сохранённых ответов."""
    rows = await conn.fetch(
        """
        SELECT c.id, (SELECT count(*) FROM comments r WHERE r.parent_id = c.id) AS n
        FROM comments c WHERE c.post_code = $1
        """,
        code,
    )
    return {r["id"]: r["n"] for r in rows}


async def save_comments(conn: asyncpg.Connection, rows: list[dict]) -> None:
    """Пачка комментариев/ответов (upsert: счётчики и текст обновляются)."""
    for r in rows:
        await conn.execute(
            """
            INSERT INTO comments (id, post_code, parent_id, depth, author_username, author_id,
                                  text, created_at, likes, reply_count)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (id) DO UPDATE SET likes = EXCLUDED.likes, text = EXCLUDED.text,
                reply_count = EXCLUDED.reply_count
            """,
            r["id"],
            r["post_code"],
            r["parent_id"],
            r["depth"],
            r["author_username"],
            r["author_id"],
            r["text"],
            r["created_at"],
            r["likes"],
            r["reply_count"],
        )


async def set_comments_state(
    conn: asyncpg.Connection, code: str, status: str, depth: int
) -> None:
    """comments_status: none | partial | done; comments_collected пересчитывается из БД."""
    await conn.execute(
        """
        UPDATE posts SET comments_status = $2, comments_depth = $3,
            comments_collected = (SELECT count(*) FROM comments WHERE post_code = $1)
        WHERE code = $1
        """,
        code,
        status,
        depth,
    )


async def save_media(conn: asyncpg.Connection, code: str, rows: list[dict]) -> None:
    for r in rows:
        await conn.execute(
            """
            INSERT INTO media (post_code, position, kind, file_path, bytes, width, height,
                               duration_sec)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (file_path) DO NOTHING
            """,
            code,
            r["position"],
            r["kind"],
            r["file_path"],
            r["bytes"],
            r["width"],
            r["height"],
            r["duration"],
        )


async def finish_post(
    conn: asyncpg.Connection, code: str, media_status: str | None, media_failed: int
) -> None:
    """Пост обработан полностью: last_refreshed_at; media_status меняется, только если медиа
    обрабатывались (None — флаги медиа выключены, статус остаётся)."""
    await conn.execute(
        """
        UPDATE posts SET last_refreshed_at = now(), updated_at = now(),
            media_status = coalesce($2, media_status),
            media_failed = CASE WHEN $2 IS NULL THEN media_failed ELSE $3 END
        WHERE code = $1
        """,
        code,
        media_status,
        media_failed,
    )


async def set_avatar(conn: asyncpg.Connection, pid: int, file_path: str) -> None:
    await conn.execute("UPDATE profiles SET avatar_file = $2 WHERE id = $1", pid, file_path)


async def get_pic_url(conn: asyncpg.Connection, pid: int) -> str | None:
    return await conn.fetchval("SELECT profile_pic_url FROM profiles WHERE id = $1", pid)
