"""Посты, комментарии, медиа, якоря и пропущенные посты профильного пайплайна.

Функции принимают соединение и работают с теми же словарями, что и раньше жили в
posts.json: код парсинга (needs_refresh, collect_thread, ...) остаётся прежним.
"""

from datetime import datetime
from pathlib import Path

import asyncpg

from config import TH
from core.logs import log
from core.textfilter import FILTER_VERSION, classify, hashtags_mentions
from core.timeutil import iso, parse_dt
from db.geo import save_post_mentions

MEDIA_SUBDIR = "media"


def media_path(username: str, file: str) -> str:
    """Путь файла для media.file_path: относительно каталога вывода (MEDIA_DIR)."""
    return f"{username}/{MEDIA_SUBDIR}/{file}"


def media_file(path: str) -> str:
    """Обратное преобразование: имя файла внутри папки media профиля."""
    return path.split(f"/{MEDIA_SUBDIR}/", 1)[-1]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _size(root: Path | None, rel: str) -> int | None:
    if root is None:
        return None
    try:
        return (root / rel).stat().st_size
    except OSError:
        return None


async def upsert_topic(conn: asyncpg.Connection, name: str, source: str = "threads") -> int:
    """id темы (регистр не важен); заводит, если её нет."""
    return await conn.fetchval(
        """
        INSERT INTO topics (name, source) VALUES ($1, $2)
        ON CONFLICT ((lower(name))) DO UPDATE SET name = topics.name
        RETURNING id
        """,
        name,
        source,
    )


POST_SQL = """
INSERT INTO posts (
    code, url, author_username, profile_id, text, topic_id, date_utc,
    likes, reply_count, reposts, quotes, hashtags, mentions,
    videos_skipped, media_failed, visited_reply_count, comments_depth,
    filter_status, filter_matched, filter_matched_in, filter_version,
    first_seen_via, last_refreshed_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,
        'profile', now())
ON CONFLICT (code) DO UPDATE SET
    url = EXCLUDED.url,
    author_username = EXCLUDED.author_username,
    profile_id = EXCLUDED.profile_id,
    text = EXCLUDED.text,
    topic_id = coalesce(EXCLUDED.topic_id, posts.topic_id),
    date_utc = coalesce(EXCLUDED.date_utc, posts.date_utc),
    likes = EXCLUDED.likes,
    reply_count = EXCLUDED.reply_count,
    reposts = EXCLUDED.reposts,
    quotes = EXCLUDED.quotes,
    hashtags = EXCLUDED.hashtags,
    mentions = EXCLUDED.mentions,
    videos_skipped = EXCLUDED.videos_skipped,
    media_failed = EXCLUDED.media_failed,
    visited_reply_count = EXCLUDED.visited_reply_count,
    comments_depth = EXCLUDED.comments_depth,
    filter_status = EXCLUDED.filter_status,
    filter_matched = EXCLUDED.filter_matched,
    filter_matched_in = EXCLUDED.filter_matched_in,
    filter_version = EXCLUDED.filter_version,
    updated_at = now(),
    last_refreshed_at = now()
"""

COMMENT_SQL = """
INSERT INTO comments (
    id, post_code, code, parent_id, depth, reply_to_author, author, text, posted_at,
    likes, url, reply_count, visited_reply_count, media_failed)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
ON CONFLICT (id) DO UPDATE SET
    post_code = EXCLUDED.post_code,
    code = coalesce(EXCLUDED.code, comments.code),
    parent_id = EXCLUDED.parent_id,
    depth = EXCLUDED.depth,
    reply_to_author = EXCLUDED.reply_to_author,
    author = EXCLUDED.author,
    text = EXCLUDED.text,
    posted_at = EXCLUDED.posted_at,
    likes = EXCLUDED.likes,
    url = EXCLUDED.url,
    reply_count = EXCLUDED.reply_count,
    visited_reply_count = EXCLUDED.visited_reply_count,
    media_failed = EXCLUDED.media_failed
"""

MEDIA_SQL = """
INSERT INTO media (post_code, comment_id, kind, file_path, position, bytes)
VALUES ($1,$2,$3,$4,$5,$6)
ON CONFLICT (file_path) DO UPDATE SET
    post_code = EXCLUDED.post_code,
    comment_id = EXCLUDED.comment_id,
    position = EXCLUDED.position,
    bytes = coalesce(EXCLUDED.bytes, media.bytes)
"""


def _media_rows(
    post_code: str, comment_id: str | None, items: list[dict], username: str, root: Path | None
) -> list[tuple]:
    rows = []
    for i, m in enumerate(items or [], start=1):
        file = m.get("file")
        if not file:
            continue
        rel = media_path(username, file)
        kind = m.get("type") if m.get("type") in ("image", "video") else "image"
        rows.append((post_code, comment_id, kind, rel, i, _size(root, rel)))
    return rows


async def save_post(
    conn: asyncpg.Connection,
    profile_id: int,
    username: str,
    post: dict,
    media_root: Path | None = None,
) -> None:
    """Сохраняет пост профиля вместе с комментариями и метаданными медиа (одна транзакция).

    Статус фильтра пересчитывается при каждом сохранении. last_refreshed_at ставится
    только здесь: по нему профильный пайплайн отличает полностью собранные посты от
    постов, найденных поиском (у тех нет комментариев и скачанных файлов).
    """
    code = post["code"]
    topic = post.get("topic")
    f_status, f_matched, f_in = classify(post.get("text"), topic)
    comments = post.get("comments") or []
    async with conn.transaction():
        topic_id = await upsert_topic(conn, topic) if topic else None
        await conn.execute(
            POST_SQL,
            code,
            post.get("url") or f"{TH}/@{username}/post/{code}",
            username,
            profile_id,
            post.get("text"),
            topic_id,
            parse_dt(post.get("date_utc")),
            post.get("likes"),
            post.get("reply_count"),
            post.get("reposts"),
            post.get("quotes"),
            list(post.get("hashtags") or []),
            list(post.get("mentions") or []),
            post.get("videos_skipped") or 0,
            post.get("media_failed") or 0,
            post.get("visited_reply_count"),
            post.get("comments_depth") or 0,
            f_status,
            f_matched,
            f_in,
            FILTER_VERSION,
        )
        try:  # города в тексте ветки; сбой детектора не должен ронять сохранение поста
            async with conn.transaction():  # savepoint: ошибка откатит только упоминания
                await save_post_mentions(conn, profile_id, code, post.get("text"))
        except Exception as e:
            log(f"[!] геолокации поста {code}: {e!r}")
        if comments:
            await conn.executemany(
                COMMENT_SQL,
                [
                    (
                        str(c["id"]),
                        code,
                        c.get("code"),
                        None if c.get("parent_id") in (None, code) else str(c["parent_id"]),
                        c.get("depth") or 1,
                        c.get("reply_to_author"),
                        c.get("author") or "",
                        c.get("text"),
                        parse_dt(c.get("created_at")),
                        c.get("likes"),
                        c.get("url"),
                        c.get("reply_count") or 0,
                        c.get("visited_reply_count"),
                        c.get("media_failed") or 0,
                    )
                    for c in comments
                ],
            )
        rows = _media_rows(code, None, post.get("media"), username, media_root)
        for c in comments:
            rows += _media_rows(code, str(c["id"]), c.get("media"), username, media_root)
        if rows:
            await conn.executemany(MEDIA_SQL, rows)


async def load_profile_posts(conn: asyncpg.Connection, profile_id: int) -> list[dict]:
    """Посты профиля, собранные профильным пайплайном, в виде прежних словарей posts.json."""
    prow = await conn.fetch(
        """
        SELECT p.*, t.name AS topic_name
        FROM posts p LEFT JOIN topics t ON t.id = p.topic_id
        WHERE p.profile_id = $1 AND p.last_refreshed_at IS NOT NULL
        ORDER BY p.date_utc DESC NULLS LAST, p.code
        """,
        profile_id,
    )
    if not prow:
        return []
    codes = [r["code"] for r in prow]
    crow = await conn.fetch(
        "SELECT * FROM comments WHERE post_code = ANY($1) ORDER BY posted_at NULLS LAST, id", codes
    )
    mrow = await conn.fetch(
        """
        SELECT post_code, comment_id, kind, file_path FROM media
        WHERE post_code = ANY($1) ORDER BY position, id
        """,
        codes,
    )
    post_media: dict[str, list[dict]] = {}
    comment_media: dict[str, list[dict]] = {}
    for m in mrow:
        item = {"type": m["kind"], "file": media_file(m["file_path"])}
        if m["comment_id"] is None:
            post_media.setdefault(m["post_code"], []).append(item)
        else:
            comment_media.setdefault(m["comment_id"], []).append(item)
    post_comments: dict[str, list[dict]] = {}
    for c in crow:
        post_comments.setdefault(c["post_code"], []).append(
            {
                "id": c["id"],
                "code": c["code"],
                "parent_id": c["parent_id"] or c["post_code"],
                "depth": c["depth"],
                "reply_to_author": c["reply_to_author"],
                "author": c["author"],
                "text": c["text"],
                "created_at": _iso(c["posted_at"]),
                "likes": c["likes"],
                "media": comment_media.get(c["id"], []),
                "media_failed": c["media_failed"],
                "url": c["url"],
                "has_replies": c["reply_count"] > 0,
                "reply_count": c["reply_count"],
                "visited_reply_count": c["visited_reply_count"],
            }
        )
    return [
        {
            "code": r["code"],
            "url": r["url"],
            "date_utc": _iso(r["date_utc"]),
            "text": r["text"],
            "topic": r["topic_name"],
            "hashtags": list(r["hashtags"]),
            "mentions": list(r["mentions"]),
            "likes": r["likes"],
            "reply_count": r["reply_count"],
            "reposts": r["reposts"],
            "quotes": r["quotes"],
            "videos_skipped": r["videos_skipped"],
            "media": post_media.get(r["code"], []),
            "media_failed": r["media_failed"],
            "comments": post_comments.get(r["code"], []),
            "visited_reply_count": r["visited_reply_count"],
            "comments_depth": r["comments_depth"],
        }
        for r in prow
    ]


async def load_skipped(conn: asyncpg.Connection, profile_id: int) -> dict:
    """{код: {"reason", "date"}} — замена skipped.json."""
    rows = await conn.fetch(
        "SELECT code, reason, post_date FROM skipped_posts WHERE profile_id = $1", profile_id
    )
    return {r["code"]: {"reason": r["reason"], "date": _iso(r["post_date"])} for r in rows}


async def mark_skipped(
    conn: asyncpg.Connection, profile_id: int, code: str, reason: str, date_str: str | None
) -> None:
    await conn.execute(
        """
        INSERT INTO skipped_posts (code, profile_id, reason, post_date) VALUES ($1, $2, $3, $4)
        ON CONFLICT (code) DO UPDATE SET
            profile_id = EXCLUDED.profile_id, reason = EXCLUDED.reason,
            post_date = coalesce(EXCLUDED.post_date, skipped_posts.post_date)
        """,
        code,
        profile_id,
        reason,
        parse_dt(date_str),
    )


async def load_anchor(conn: asyncpg.Connection, profile_id: int) -> dict | None:
    r = await conn.fetchrow("SELECT * FROM profile_anchors WHERE profile_id = $1", profile_id)
    if not r:
        return None
    return {
        "newest_code": r["newest_code"],
        "newest_date": _iso(r["newest_date"]),
        "covered_from": _iso(r["covered_from"]),
        "known_posts": r["known_posts"],
        "updated_at": _iso(r["updated_at"]),
    }


async def save_anchor(conn: asyncpg.Connection, profile_id: int, anchor: dict) -> None:
    await conn.execute(
        """
        INSERT INTO profile_anchors (profile_id, newest_code, newest_date, covered_from,
                                     known_posts, updated_at)
        VALUES ($1, $2, $3, $4, $5, now())
        ON CONFLICT (profile_id) DO UPDATE SET
            newest_code = EXCLUDED.newest_code, newest_date = EXCLUDED.newest_date,
            covered_from = EXCLUDED.covered_from, known_posts = EXCLUDED.known_posts,
            updated_at = now()
        """,
        profile_id,
        anchor["newest_code"],
        parse_dt(anchor.get("newest_date")),
        parse_dt(anchor.get("covered_from")),
        anchor.get("known_posts") or 0,
    )


async def about_fetched(conn: asyncpg.Connection, profile_id: int) -> bool:
    """True, если окно «Об этом профиле» этого профиля уже прочитано."""
    return bool(
        await conn.fetchval(
            "SELECT about_fetched_at IS NOT NULL FROM profiles WHERE id = $1", profile_id
        )
    )


def _keep_full(col: str) -> str:
    """Поле обновляется быстрым сбором, только пока ветка не разобрана полностью."""
    return (
        f"CASE WHEN posts.last_refreshed_at IS NULL "
        f"THEN coalesce(EXCLUDED.{col}, posts.{col}) ELSE posts.{col} END"
    )


_FILTER_OPEN = "posts.last_refreshed_at IS NULL AND EXCLUDED.text IS NOT NULL"

THREAD_LIGHT_SQL = f"""
INSERT INTO posts (
    code, url, author_username, author_user_id, profile_id, text, topic_id, date_utc,
    likes, reply_count, reposts, quotes, hashtags, mentions,
    filter_status, filter_matched, filter_matched_in, filter_version, first_seen_via)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,'profile')
ON CONFLICT (code) DO UPDATE SET
    profile_id = coalesce(posts.profile_id, EXCLUDED.profile_id),
    author_user_id = coalesce(posts.author_user_id, EXCLUDED.author_user_id),
    url = {_keep_full("url")},
    text = {_keep_full("text")},
    topic_id = {_keep_full("topic_id")},
    date_utc = {_keep_full("date_utc")},
    hashtags = CASE WHEN {_FILTER_OPEN} THEN EXCLUDED.hashtags ELSE posts.hashtags END,
    mentions = CASE WHEN {_FILTER_OPEN} THEN EXCLUDED.mentions ELSE posts.mentions END,
    filter_status = CASE WHEN {_FILTER_OPEN} THEN EXCLUDED.filter_status ELSE posts.filter_status END,
    filter_matched = CASE WHEN {_FILTER_OPEN} THEN EXCLUDED.filter_matched ELSE posts.filter_matched END,
    filter_matched_in = CASE WHEN {_FILTER_OPEN}
        THEN EXCLUDED.filter_matched_in ELSE posts.filter_matched_in END,
    filter_version = CASE WHEN {_FILTER_OPEN} THEN EXCLUDED.filter_version ELSE posts.filter_version END,
    likes = coalesce(EXCLUDED.likes, posts.likes),
    reply_count = coalesce(EXCLUDED.reply_count, posts.reply_count),
    reposts = coalesce(EXCLUDED.reposts, posts.reposts),
    quotes = coalesce(EXCLUDED.quotes, posts.quotes),
    updated_at = now()
"""


async def pending_threads(
    conn: asyncpg.Connection, profile_id: int, cutoff: datetime | None, limit: int | None
) -> list[dict]:
    """Ветки профиля, собранные быстрым режимом и ещё не разобранные, за период (новые первыми).

    Ветки из skipped_posts (видео при режиме «только фото») пропускаются: иначе их
    открывали бы при каждом запуске.
    """
    rows = await conn.fetch(
        """
        SELECT code, date_utc FROM posts p
        WHERE p.profile_id = $1 AND p.last_refreshed_at IS NULL
          AND ($2::timestamptz IS NULL OR p.date_utc IS NULL OR p.date_utc >= $2)
          AND NOT EXISTS (SELECT 1 FROM skipped_posts s WHERE s.code = p.code)
        ORDER BY p.date_utc DESC NULLS LAST, p.code
        LIMIT $3
        """,
        profile_id,
        cutoff,
        limit,
    )
    return [{"code": r["code"], "date": r["date_utc"]} for r in rows]


async def known_codes(conn: asyncpg.Connection, profile_id: int) -> set[str]:
    """Коды веток профиля, уже лежащих в posts (разобранных и собранных быстрым режимом)."""
    rows = await conn.fetch("SELECT code FROM posts WHERE profile_id = $1", profile_id)
    return {r["code"] for r in rows}


async def save_threads_light(
    conn: asyncpg.Connection, profile_id: int, username: str, items: list[dict]
) -> None:
    """Быстрый сбор веток: запись данных из ленты без постов под веткой и медиа.

    Новая ветка получает last_refreshed_at = NULL (признак «ещё не разобрана»). У уже
    разобранной ветки обновляются только счётчики: текст, дата, комментарии, медиа
    остаются как были. Фильтр «олх» считается по тексту, как при полном сохранении.
    """
    rows = []
    async with conn.transaction():
        topic_ids: dict[str, int] = {}
        for it in items:
            topic = it.get("topic")
            if topic and topic not in topic_ids:
                topic_ids[topic] = await upsert_topic(conn, topic)
            text = it.get("text")
            f_status, f_matched, f_in = classify(text, topic)
            tags, mentions = hashtags_mentions(text)
            rows.append(
                (
                    it["code"],
                    f"{TH}/@{username}/post/{it['code']}",
                    username,
                    it.get("author_id"),
                    profile_id,
                    text,
                    topic_ids.get(topic) if topic else None,
                    parse_dt(iso(it.get("ts"))),
                    it.get("likes"),
                    it.get("replies"),
                    it.get("reposts"),
                    it.get("quotes"),
                    tags,
                    mentions,
                    f_status,
                    f_matched,
                    f_in,
                    FILTER_VERSION,
                )
            )
        await conn.executemany(THREAD_LIGHT_SQL, rows)
