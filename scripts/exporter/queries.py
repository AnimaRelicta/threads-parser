"""Базовые запросы выгрузки (`export.py <имя>`). Свои запросы: data/export_queries.sql.

Параметры: :since (флаг --since, смысл у каждого запроса свой, указан в описании) и :limit
(флаг --limit; 0 = все). Запросы только читают: выгрузка идёт в read-only транзакции.
"""

from exporter.sqlfile import Query

_URL = "'https://www.threads.com/@' || p.username"

_CLASS_NAME = (
    "CASE p.spam_class WHEN 1 THEN 'барахолка' WHEN 2 THEN 'продавец' WHEN 3 THEN 'спамер' END"
)

_SQL = {
    "spam-classes": (
        "Профили с классом 1/2/3 (spam_class); --since: когда класс посчитан",
        f"""
SELECT p.username, p.spam_class AS class, {_CLASS_NAME} AS class_name,
       p.followers_count,
       (p.spam_class_info->'metrics'->>'median_replies')::numeric AS median_replies,
       (p.spam_class_info->'metrics'->>'median_gap_hours')::numeric AS median_gap_hours,
       p.spam_class_info->>'reason' AS reason,
       p.is_banned, p.spam_class_at, {_URL} AS url
FROM profiles p
WHERE p.spam_class IS NOT NULL AND (:since::timestamptz IS NULL OR p.spam_class_at >= :since)
ORDER BY p.spam_class DESC, p.followers_count NULLS LAST, p.username
LIMIT :limit
""",
    ),
    "banned": (
        "Забаненные профили и причина; --since: когда забанен",
        f"""
SELECT p.username, p.banned_at, p.spam_score, p.ban_reason, p.followers_count,
       p.spam_whitelist, {_URL} AS url
FROM profiles p
WHERE p.is_banned AND (:since::timestamptz IS NULL OR p.banned_at >= :since)
ORDER BY p.banned_at DESC
LIMIT :limit
""",
    ),
    "profiles": (
        "Все профили: подписчики, категория, страна, класс, бан; --since: когда найден",
        f"""
SELECT p.username, p.followers_count, c.name_ru AS category, p.about_country AS country,
       p.about_location, p.spam_class, p.is_banned, p.review_status, p.discovered_via,
       p.first_seen_at, p.last_scraped_at, p.bio, {_URL} AS url
FROM profiles p
LEFT JOIN categories c ON c.id = p.category_id
WHERE :since::timestamptz IS NULL OR p.first_seen_at >= :since
ORDER BY p.followers_count DESC NULLS LAST, p.username
LIMIT :limit
""",
    ),
    "threads": (
        "Ветки (posts): текст, дата, ответы, лайки; --since: дата ветки",
        """
SELECT t.date_utc, t.author_username, t.reply_count, t.likes, t.reposts, t.quotes,
       t.filter_status, tp.name AS topic, t.text, t.url, t.code
FROM posts t
LEFT JOIN topics tp ON tp.id = t.topic_id
WHERE :since::timestamptz IS NULL OR t.date_utc >= :since
ORDER BY t.date_utc DESC NULLS LAST
LIMIT :limit
""",
    ),
    "top-authors": (
        "Авторы по числу веток за период: ответы, класс, подписчики; --since: дата ветки",
        f"""
SELECT p.username, count(*) AS threads,
       sum(t.reply_count) AS replies_total,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY t.reply_count) AS replies_median,
       min(t.date_utc) AS first_thread, max(t.date_utc) AS last_thread,
       p.followers_count, p.spam_class, p.is_banned, {_URL} AS url
FROM posts t
JOIN profiles p ON p.id = t.profile_id
WHERE :since::timestamptz IS NULL OR t.date_utc >= :since
GROUP BY p.id
ORDER BY threads DESC, p.username
LIMIT :limit
""",
    ),
    "instagram": (
        "Связки Threads → Instagram; --since: когда связка обновлена",
        f"""
SELECT p.username, l.instagram_username, l.source, l.confidence, p.followers_count,
       l.updated_at, {_URL} AS url,
       'https://www.instagram.com/' || l.instagram_username AS instagram_url
FROM instagram_links l
JOIN profiles p ON p.id = l.profile_id
WHERE :since::timestamptz IS NULL OR l.updated_at >= :since
ORDER BY p.username
LIMIT :limit
""",
    ),
}

BASE_QUERIES: dict[str, Query] = {
    name: Query(name, desc, sql.strip(), "base") for name, (desc, sql) in _SQL.items()
}
