-- Описание сводного отчёта для `uv run scripts/build_report.py` (путь: REPORT_FILE или --file).
-- Каждый блок = один лист xlsx. Все листы пишутся в один файл threads_report_ГГГГ-ММ-ДД.xlsx.
--
--   -- name: имя          (латиница, цифры, дефис; по нему --only / --on / --off / --set имя.параметр)
--   -- desc: описание     (видно в --list)
--   -- sheet: Имя листа   (необязательно; до 31 символа, по умолчанию = name)
--   -- enabled: on|off    (необязательно; входит ли лист в отчёт без флагов, по умолчанию on)
--   -- param: имя=значение  параметр запроса: в тексте :имя::тип, значение по умолчанию, пусто = NULL
--   запрос (только SELECT; запросы идут в read-only транзакции)
--
-- Общая логика не копируется между листами, а выносится во фрагмент: блок с `-- kind: fragment`
-- содержит подзапрос в скобках и подставляется в листы как {{имя}}. Параметры фрагмента
-- (`-- param:` в нём) листы получают автоматически. Фрагменты в xlsx не попадают.
--
-- Страна автора = profiles.about_location (поле «Об этом профиле»), других источников нет.
-- Параметры: since (--since; пусто = за всё время), home_location (страна автора для «своих»
-- листов), limit (пусто = все строки).
-- Для типа параметра задайте приведение в SQL: :since::timestamptz, :limit::int, :home_location::text.


-- ============================== ФРАГМЕНТЫ ==============================

-- name: kz-profiles
-- kind: fragment
-- param: home_location=Казахстан
(SELECT pr.*
   FROM profiles pr
  WHERE lower(btrim(pr.about_location)) = lower(btrim(:home_location::text)))


-- name: period-posts
-- kind: fragment
-- param: since=
(SELECT p.*
   FROM posts p
  WHERE :since::timestamptz IS NULL OR p.date_utc >= :since::timestamptz)


-- name: kz-posts
-- kind: fragment
-- Посты авторов из home_location за период. Класс поста = категория поста, а если её нет, то категория автора.
(SELECT p.*,
        a.id              AS author_id,
        a.followers_count AS author_followers,
        a.about_location  AS author_location,
        coalesce(p.category_id, a.category_id) AS class_id
   FROM {{period-posts}} p
   JOIN {{kz-profiles}} a ON lower(a.username) = lower(p.author_username))


-- ============================== ЛИСТЫ ==============================

-- name: summary
-- desc: Краткая общая статистика по базе (привязка только к БД)
-- sheet: Сводка
-- param: since=
-- param: home_location=Казахстан
SELECT m.label AS "показатель", m.val AS "значение", m.note AS "примечание"
  FROM (VALUES
    (1,  'Профилей в базе',                  (SELECT count(*) FROM profiles), NULL::text),
    (2,  'Профилей с локацией (about_location)', (SELECT count(*) FROM profiles WHERE btrim(coalesce(about_location, '')) <> ''), NULL),
    (3,  'Профилей из home_location',        (SELECT count(*) FROM {{kz-profiles}} k), 'страна = :home_location'),
    (4,  'Профилей с категорией',            (SELECT count(*) FROM profiles WHERE category_id IS NOT NULL), NULL),
    (5,  'Профилей со ссылкой на Instagram', (SELECT count(*) FROM instagram_links), NULL),
    (6,  'Постов в базе',                    (SELECT count(*) FROM posts), NULL),
    (7,  'Постов за период',                 (SELECT count(*) FROM {{period-posts}} x), 'период = since (пусто = всё время)'),
    (8,  'Постов авторов из home_location за период', (SELECT count(*) FROM {{kz-posts}} x), NULL),
    (9,  'Авторов этих постов',              (SELECT count(DISTINCT author_id) FROM {{kz-posts}} x), NULL),
    (10, 'Комментариев в базе',              (SELECT count(*) FROM comments), NULL),
    (11, 'Комментариев к постам периода',    (SELECT count(*) FROM comments c JOIN {{period-posts}} x ON x.code = c.post_code), NULL),
    (12, 'Поисковых заданий',                (SELECT count(*) FROM search_jobs), NULL),
    (13, 'Самый ранний пост',                NULL, (SELECT min(date_utc)::date::text FROM posts)),
    (14, 'Самый поздний пост',               NULL, (SELECT max(date_utc)::date::text FROM posts))
  ) AS m(ord, label, val, note)
 ORDER BY m.ord;


-- name: categories
-- desc: Справочник categories и сколько профилей и постов в каждой категории
-- sheet: Категории
-- param: since=
-- param: home_location=Казахстан
SELECT t.id, t.code, t."категория", t."активна",
       t."профилей", t."профилей из home_location", t."постов за период (класс)"
  FROM (
    SELECT c.id, c.code, c.name_ru AS "категория", c.is_active AS "активна",
           (SELECT count(*) FROM profiles pr WHERE pr.category_id = c.id) AS "профилей",
           (SELECT count(*) FROM {{kz-profiles}} k WHERE k.category_id = c.id) AS "профилей из home_location",
           (SELECT count(*) FROM {{kz-posts}} kp WHERE kp.class_id = c.id) AS "постов за период (класс)",
           c.id AS ord
      FROM categories c
    UNION ALL
    SELECT NULL, NULL, 'не определена', NULL,
           (SELECT count(*) FROM profiles pr WHERE pr.category_id IS NULL),
           (SELECT count(*) FROM {{kz-profiles}} k WHERE k.category_id IS NULL),
           (SELECT count(*) FROM {{kz-posts}} kp WHERE kp.class_id IS NULL),
           1000
  ) t
 ORDER BY t.ord;


-- name: comments
-- desc: Комментарии (threads.comments) к постам за период; limit: сколько строк, пусто = все
-- sheet: Комментарии
-- param: since=
-- param: limit=
SELECT c.post_code AS "пост", c.id AS "id комментария", c.depth AS "глубина",
       c.author AS "автор", c.reply_to_author AS "ответ автору", c.text AS "текст",
       c.likes AS "лайки", c.reply_count AS "ответов", c.posted_at AS "дата (UTC)", c.url AS "ссылка"
  FROM comments c
  JOIN {{period-posts}} p ON p.code = c.post_code
 ORDER BY c.posted_at DESC NULLS LAST, c.id
 LIMIT :limit::int;


-- name: instagram
-- desc: Связь профилей Threads с Instagram (threads.instagram_links)
-- sheet: Instagram
-- param: limit=
SELECT pr.username AS "ник Threads", 'https://www.threads.com/@' || pr.username AS "ссылка Threads",
       il.instagram_username AS "Instagram", 'https://www.instagram.com/' || il.instagram_username AS "ссылка Instagram",
       il.confidence AS "уверенность", il.source AS "источник",
       pr.followers_count AS "подписчиков", pr.about_location AS "локация", c.name_ru AS "категория"
  FROM instagram_links il
  JOIN profiles pr ON pr.id = il.profile_id
  LEFT JOIN categories c ON c.id = pr.category_id
 ORDER BY pr.followers_count DESC NULLS LAST, pr.username
 LIMIT :limit::int;


-- name: posts-kz
-- desc: Все посты авторов из home_location за период с категорией и метриками (основа для сводной таблицы)
-- sheet: Посты
-- param: since=
-- param: home_location=Казахстан
-- param: limit=
SELECT p.code AS "код", p.url AS "ссылка", p.author_username AS "автор",
       p.author_location AS "локация автора", p.author_followers AS "подписчиков автора",
       p.date_utc AS "дата (UTC)", c.name_ru AS "класс", cp.name_ru AS "категория поста",
       ca.name_ru AS "категория автора",
       p.likes AS "лайки", p.reply_count AS "комментарии", p.reposts AS "репосты", p.quotes AS "цитаты",
       coalesce(p.likes, 0) + coalesce(p.reply_count, 0) + coalesce(p.reposts, 0) + coalesce(p.quotes, 0) AS "вовлечённость",
       array_to_string(p.found_keywords, ', ') AS "ключевые слова",
       p.text AS "текст"
  FROM {{kz-posts}} p
  LEFT JOIN categories c  ON c.id  = p.class_id
  LEFT JOIN categories cp ON cp.id = p.category_id
  LEFT JOIN profiles pa   ON pa.id = p.author_id
  LEFT JOIN categories ca ON ca.id = pa.category_id
 ORDER BY p.date_utc DESC NULLS LAST, p.code
 LIMIT :limit::int;


-- name: posts-kz-stats
-- desc: Статистика постов авторов из home_location по классам: посты, авторы, лайки, комментарии, репосты
-- sheet: Статистика постов
-- param: since=
-- param: home_location=Казахстан
SELECT coalesce(c.name_ru, 'не классифицирован') AS "класс",
       count(*) AS "постов",
       count(DISTINCT p.author_id) AS "авторов",
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS "доля постов, %",
       sum(coalesce(p.likes, 0)) AS "лайков",
       round(avg(p.likes), 1) AS "лайков в среднем",
       percentile_cont(0.5) WITHIN GROUP (ORDER BY p.likes) AS "лайков медиана",
       sum(coalesce(p.reply_count, 0)) AS "комментариев",
       round(avg(p.reply_count), 1) AS "комментариев в среднем",
       sum(coalesce(p.reposts, 0)) AS "репостов",
       round(avg(p.reposts), 1) AS "репостов в среднем",
       sum(coalesce(p.quotes, 0)) AS "цитат",
       max(p.likes) AS "максимум лайков"
  FROM {{kz-posts}} p
  LEFT JOIN categories c ON c.id = p.class_id
 GROUP BY c.name_ru, c.id
 ORDER BY count(*) DESC, c.id;


-- name: posts-weekly
-- desc: Динамика по неделям: посты, авторы и метрики авторов из home_location
-- sheet: Динамика
-- param: since=
-- param: home_location=Казахстан
SELECT date_trunc('week', p.date_utc)::date AS "неделя с",
       count(*) AS "постов",
       count(DISTINCT p.author_id) AS "авторов",
       sum(coalesce(p.likes, 0)) AS "лайков",
       sum(coalesce(p.reply_count, 0)) AS "комментариев",
       sum(coalesce(p.reposts, 0)) AS "репостов"
  FROM {{kz-posts}} p
 WHERE p.date_utc IS NOT NULL
 GROUP BY 1
 ORDER BY 1;


-- name: top-authors
-- desc: Топ авторов из home_location по числу постов за период; limit: сколько авторов
-- sheet: Топ авторов
-- param: since=
-- param: home_location=Казахстан
-- param: limit=50
SELECT p.author_username AS "автор", max(p.author_followers) AS "подписчиков",
       max(c.name_ru) AS "категория автора",
       count(*) AS "постов", sum(coalesce(p.likes, 0)) AS "лайков",
       sum(coalesce(p.reply_count, 0)) AS "комментариев", sum(coalesce(p.reposts, 0)) AS "репостов",
       'https://www.threads.com/@' || p.author_username AS "ссылка"
  FROM {{kz-posts}} p
  LEFT JOIN profiles pa ON pa.id = p.author_id
  LEFT JOIN categories c ON c.id = pa.category_id
 GROUP BY p.author_username
 ORDER BY count(*) DESC, p.author_username
 LIMIT :limit::int;


-- name: profiles
-- desc: Список аккаунтов с локацией, подписчиками и категорией; limit: сколько строк, пусто = все
-- sheet: Профили
-- param: limit=
SELECT pr.id, pr.username AS "ник", 'https://www.threads.com/@' || pr.username AS "ссылка",
       pr.about_location AS "локация", pr.followers_count AS "подписчиков",
       pr.threads_count AS "веток", pr.recent_views AS "просмотры",
       c.name_ru AS "категория", pr.is_target AS "в списке парсинга",
       pr.about_joined AS "в Threads с", il.instagram_username AS "Instagram",
       (SELECT count(*) FROM posts p WHERE p.profile_id = pr.id) AS "постов в базе",
       pr.last_scraped_at AS "последний парсинг (UTC)"
  FROM profiles pr
  LEFT JOIN categories c ON c.id = pr.category_id
  LEFT JOIN instagram_links il ON il.profile_id = pr.id
 ORDER BY pr.followers_count DESC NULLS LAST, pr.username
 LIMIT :limit::int;


-- name: profiles-stats
-- desc: Статистика профилей по странам (about_location): сколько аккаунтов, подписчики, Instagram, категории
-- sheet: Статистика профилей
SELECT coalesce(nullif(btrim(pr.about_location), ''), 'не указана') AS "локация",
       count(*) AS "профилей",
       sum(coalesce(pr.followers_count, 0)) AS "подписчиков всего",
       round(avg(pr.followers_count), 1) AS "подписчиков в среднем",
       percentile_cont(0.5) WITHIN GROUP (ORDER BY pr.followers_count) AS "подписчиков медиана",
       max(pr.followers_count) AS "максимум подписчиков",
       count(*) FILTER (WHERE pr.followers_count >= 1000)  AS "от 1 тыс.",
       count(*) FILTER (WHERE pr.followers_count >= 10000) AS "от 10 тыс.",
       count(il.id) AS "с Instagram",
       count(pr.category_id) AS "с категорией"
  FROM profiles pr
  LEFT JOIN instagram_links il ON il.profile_id = pr.id
 GROUP BY 1
 ORDER BY count(*) DESC, 1;


-- name: runs
-- desc: Последние запуски парсера и поиска (когда и что собиралось)
-- sheet: Запуски
-- enabled: off
-- param: limit=50
SELECT r.id, r.kind AS "тип", r.status AS "статус", r.started_at AS "начало (UTC)",
       r.finished_at AS "конец (UTC)", r.error AS "ошибка"
  FROM runs r
 ORDER BY r.started_at DESC
 LIMIT :limit::int;
