"""Базовые запросы выгрузки (`export.py <имя>`). Свои запросы: data/export_queries.sql.

Параметры: :since (флаг --since, смысл у каждого запроса свой, указан в описании) и :limit
(флаг --limit; 0 = все). Запросы только читают: выгрузка идёт в read-only транзакции.
"""

from exporter.sqlfile import Query

_URL = "'https://www.threads.com/@' || p.username"

# profiles.links: [{text, url, kind: instagram|telegram|whatsapp|other, handle}] из шапки профиля
_LINKS = "CASE WHEN jsonb_typeof(p.links) = 'array' THEN p.links ELSE '[]'::jsonb END"

_CLASS_NAME = (
    "CASE p.spam_class WHEN 1 THEN 'барахолка' WHEN 2 THEN 'продавец' WHEN 3 THEN 'спамер' END"
)

# profiles.about_joined: «Октябрь 2026 г.» (или «October 2026») -> 1-е число месяца. Дня в источнике нет.
_MONTHS = ", ".join(
    f"('{stem}', {n})"
    for n, stems in enumerate(
        (
            "янв jan",
            "фев feb",
            "мар mar",
            "апр apr",
            "мая май may",
            "июн jun",
            "июл jul",
            "авг aug",
            "сен sep",
            "окт oct",
            "ноя nov",
            "дек dec",
        ),
        start=1,
    )
    for stem in stems.split()
)
_JOINED = f"""LEFT JOIN LATERAL (
    SELECT make_date((regexp_match(p.about_joined, '([0-9]{{4}})'))[1]::int, m.n, 1) AS d
      FROM (VALUES {_MONTHS}) m(stem, n)
     WHERE lower(p.about_joined) LIKE m.stem || '%'
     LIMIT 1
) j ON true"""


def _est(days: int) -> str:
    """Ветки за N дней ≈ среднее в день с даты регистрации × N, но не больше всех веток."""
    return (
        "CASE WHEN j.d IS NOT NULL THEN round(least(p.threads_count::numeric, "
        f"p.threads_count::numeric / greatest(current_date - j.d, 1) * {days}), 1) END"
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
        "Лист для выгрузки: id, ник, ссылка, заголовок, подписчики, ветки, просмотры, био, "
        "дата создания, локации, темы, топики, оценка веток за 7/30/90 дней; --since: когда найден",
        f"""
SELECT p.id,
       p.username AS "название",
       {_URL} AS "ссылка",
       nullif(trim(regexp_replace(p.title, '\\s*\\(@[A-Za-z0-9_.]+\\)\\s*•.*$', '')), '') AS "заголовок",
       p.followers_count AS "подписчиков",
       p.threads_count AS "веток",
       p.recent_views AS "просмотров",
       p.bio AS "bio",
       coalesce(
           (SELECT l->>'url' FROM jsonb_array_elements({_LINKS}) l
             WHERE l->>'kind' = 'instagram' LIMIT 1),
           (SELECT 'https://www.instagram.com/' || il.instagram_username
              FROM instagram_links il
             WHERE il.profile_id = p.id AND il.confidence = 'confirmed')
       ) AS "instagram",
       (SELECT string_agg(DISTINCT l->>'url', ', ') FROM jsonb_array_elements({_LINKS}) l
         WHERE l->>'kind' = 'whatsapp') AS "whatsapp",
       (SELECT string_agg(DISTINCT l->>'url', ', ') FROM jsonb_array_elements({_LINKS}) l
         WHERE l->>'kind' = 'telegram') AS "telegram",
       (SELECT string_agg(DISTINCT l->>'url', ', ') FROM jsonb_array_elements({_LINKS}) l
         WHERE l->>'kind' = 'other') AS "другие ссылки",
       p.about_joined AS "дата создания",
       p.about_location AS "локация из био",
       p.text_location AS "локация из текста",
       (SELECT string_agg(e->>'text', ', ')
          FROM jsonb_array_elements(
                   CASE WHEN jsonb_typeof(p.topics) = 'array' THEN p.topics ELSE '[]'::jsonb END
               ) e) AS "темы",
       (SELECT string_agg(x.name || ' (' || x.n || ')', ', ' ORDER BY x.n DESC, x.name)
          FROM (SELECT tp.name, count(*) AS n
                  FROM posts t JOIN topics tp ON tp.id = t.topic_id
                 WHERE t.profile_id = p.id
                 GROUP BY tp.name ORDER BY n DESC, tp.name LIMIT 5) x) AS "топики",
       {_est(7)} AS "веток за 7д (оценка)",
       {_est(30)} AS "веток за 30д (оценка)",
       {_est(90)} AS "веток за 90д (оценка)"
FROM profiles p
{_JOINED}
WHERE p.is_target AND (:since::timestamptz IS NULL OR p.first_seen_at >= :since)
ORDER BY p.followers_count DESC NULLS LAST, p.username
LIMIT :limit
""",
    ),
    "profiles-full": (
        "Все профили, широко: подписчики, категория, страна, класс, бан; --since: когда найден",
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
