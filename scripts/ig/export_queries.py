"""Базовые запросы `cli.py ig export <имя>`. Свои: data/ig_export_queries.sql.

Параметры: :since (флаг --since, смысл указан в описании) и :limit (флаг --limit; 0 = все).
Запросы только читают: выгрузка идёт в read-only транзакции, схема instagram.
"""

from exporter.sqlfile import Query

_URL = "'https://www.instagram.com/' || p.username"
_SINCE_ADDED = "(:since::timestamptz IS NULL OR p.added_at >= :since)"

_SQL = {
    "ig-profiles": (
        "Профили: источник, подписчики, статус, % обработки (собрано постов от posts_count); "
        "--since: когда добавлен",
        f"""
SELECT p.username, {_URL} AS url, p.source, p.link_confidence, p.status, p.is_target,
       p.followers_count, p.posts_count, count(po.code) AS posts_collected,
       round(least(100.0, 100.0 * count(po.code) / nullif(p.posts_count, 0)), 1) AS processed_pct,
       p.processed_mode, p.account_location, p.added_at, p.last_scraped_at
FROM profiles p
LEFT JOIN posts po ON po.profile_id = p.id
WHERE {_SINCE_ADDED}
GROUP BY p.id
ORDER BY p.followers_count DESC NULLS LAST, p.username
LIMIT :limit
""",
    ),
    "ig-posts": (
        "Посты: дата, тип, лайки, комментарии, просмотры, подпись, статусы медиа и комментариев; "
        "--since: дата поста",
        f"""
SELECT p.username, po.code, po.url, po.kind, po.taken_at, po.likes, po.comments_count,
       po.views, po.caption, array_to_string(po.hashtags, ' ') AS hashtags, po.location_name,
       po.is_pinned, po.carousel_size, po.media_status, po.comments_status,
       po.comments_collected
FROM posts po
JOIN profiles p ON p.id = po.profile_id
WHERE :since::timestamptz IS NULL OR po.taken_at >= :since
ORDER BY po.taken_at DESC NULLS LAST, po.code
LIMIT :limit
""",
    ),
    "ig-comments": (
        "Комментарии и ответы: автор, текст, лайки, пост; --since: дата комментария",
        """
SELECT p.username AS profile, c.post_code, c.id, c.parent_id, c.depth, c.author_username,
       c.text, c.created_at, c.likes, c.reply_count
FROM comments c
JOIN posts po ON po.code = c.post_code
JOIN profiles p ON p.id = po.profile_id
WHERE :since::timestamptz IS NULL OR c.created_at >= :since
ORDER BY c.created_at DESC NULLS LAST, c.id
LIMIT :limit
""",
    ),
    "ig-media": (
        "Файлы медиа: путь (от IG_STORAGE_DIR), размер, разрешение; --since: дата поста",
        """
SELECT p.username, m.post_code, m.position, m.kind, m.file_path, m.bytes, m.width, m.height,
       m.duration_sec, po.taken_at
FROM media m
JOIN posts po ON po.code = m.post_code
JOIN profiles p ON p.id = po.profile_id
WHERE :since::timestamptz IS NULL OR po.taken_at >= :since
ORDER BY po.taken_at DESC NULLS LAST, m.post_code, m.position
LIMIT :limit
""",
    ),
    "ig-sources": (
        "Профили по источнику (file/threads/cli) и связи (confirmed/guessed) в разрезе статусов; "
        "--since: когда добавлен",
        f"""
SELECT p.source, coalesce(p.link_confidence, '-') AS link_confidence, count(*) AS profiles,
       count(*) FILTER (WHERE p.is_target) AS targets,
       count(*) FILTER (WHERE p.status = 'new') AS new,
       count(*) FILTER (WHERE p.status = 'ok') AS ok,
       count(*) FILTER (WHERE p.status = 'private') AS private,
       count(*) FILTER (WHERE p.status = 'not_found') AS not_found,
       count(*) FILTER (WHERE p.status = 'error') AS error
FROM profiles p
WHERE {_SINCE_ADDED}
GROUP BY 1, 2
ORDER BY 1, 2
LIMIT :limit
""",
    ),
    "ig-coverage": (
        "Охват: собрано постов против posts_count, посты без файлов и без комментариев; "
        "--since: когда добавлен",
        f"""
WITH x AS (
    SELECT po.profile_id, count(*) AS collected,
           count(*) FILTER (WHERE m.post_code IS NULL) AS no_media,
           count(*) FILTER (WHERE po.media_status IN ('pending', 'partial', 'failed')) AS media_incomplete,
           count(*) FILTER (WHERE coalesce(po.comments_count, 0) > 0
                              AND po.comments_collected = 0) AS no_comments
    FROM posts po
    LEFT JOIN (SELECT DISTINCT post_code FROM media) m ON m.post_code = po.code
    GROUP BY po.profile_id
)
SELECT p.username, {_URL} AS url, p.posts_count, coalesce(x.collected, 0) AS collected,
       CASE WHEN p.posts_count IS NOT NULL
            THEN greatest(p.posts_count - coalesce(x.collected, 0), 0) END AS missing,
       coalesce(x.no_media, 0) AS posts_no_media,
       coalesce(x.media_incomplete, 0) AS posts_media_incomplete,
       coalesce(x.no_comments, 0) AS posts_no_comments, p.processed_mode
FROM profiles p
LEFT JOIN x ON x.profile_id = p.id
WHERE p.status = 'ok' AND {_SINCE_ADDED}
ORDER BY missing DESC NULLS LAST, p.username
LIMIT :limit
""",
    ),
}

BASE_QUERIES: dict[str, Query] = {
    name: Query(name, desc, sql.strip(), "base") for name, (desc, sql) in _SQL.items()
}
