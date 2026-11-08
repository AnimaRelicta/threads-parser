-- 019_drop_unused_columns: убрать темы, поиск новых профилей, бан/спам и гео-кэш из схемы.
-- Код, который читал и писал эти колонки, удалён раньше (бан и спам-детектор, discover.py,
-- review_status, topics, text_location, about_country); теперь уходят сами колонки.
--
-- Необратимо: значения пропадают вместе с колонками. Если они ещё нужны, сделайте дамп
-- (pg_dump -n threads) до миграции. Посмотреть заранее, что будет потеряно:
--   SELECT count(*) FILTER (WHERE is_banned) AS banned, count(spam_class) AS spam_class,
--          count(text_location) AS text_location, count(about_country) AS about_country,
--          count(*) FILTER (WHERE discovered_via <> 'seed_list') AS discovered
--   FROM threads.profiles;
SET search_path = threads;

-- ---------- review_status: триггер и функция зависят от колонки ----------
DROP TRIGGER IF EXISTS profiles_review_status_transition ON profiles;
DROP FUNCTION IF EXISTS check_review_status_transition();

-- ---------- profiles ----------
-- Индексы по этим колонкам (profiles_discovered, profiles_review, profiles_banned,
-- profiles_spam_class) и CHECK-ограничения уходят вместе с колонками.
-- discovered_from_profile_id / discovered_first_code (внешние ключи) и mentions_count писал
-- только discover.py.
ALTER TABLE profiles
    DROP COLUMN IF EXISTS user_id,
    DROP COLUMN IF EXISTS title,
    DROP COLUMN IF EXISTS discovered_via,
    DROP COLUMN IF EXISTS discovered_from_profile_id,
    DROP COLUMN IF EXISTS discovered_first_code,
    DROP COLUMN IF EXISTS mentions_count,
    DROP COLUMN IF EXISTS review_status,
    DROP COLUMN IF EXISTS is_banned,
    DROP COLUMN IF EXISTS ban_reason,
    DROP COLUMN IF EXISTS banned_at,
    DROP COLUMN IF EXISTS spam_score,
    DROP COLUMN IF EXISTS spam_signals,
    DROP COLUMN IF EXISTS spam_checked_at,
    DROP COLUMN IF EXISTS spam_whitelist,
    DROP COLUMN IF EXISTS spam_class,
    DROP COLUMN IF EXISTS spam_class_info,
    DROP COLUMN IF EXISTS spam_class_at,
    DROP COLUMN IF EXISTS about_country,
    DROP COLUMN IF EXISTS geo_version,
    DROP COLUMN IF EXISTS text_location,
    DROP COLUMN IF EXISTS text_location_src;

-- ---------- темы ----------
-- posts.topic_id ссылается на topics; search_jobs.topic_id убрана ещё в 013.
DROP INDEX IF EXISTS posts_topic;
ALTER TABLE posts DROP COLUMN IF EXISTS topic_id;
ALTER TABLE posts DROP COLUMN IF EXISTS geo_version;
DROP TABLE IF EXISTS topics;

COMMENT ON TABLE profiles IS
    'Профили Threads. is_target = в парсинге; категория (category_id) ставится classify/jev; '
    'локации лежат в location_mentions, итог считается при чтении (core/geo.py::resolve)';
