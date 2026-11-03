-- 015_processed_mode: чем профиль уже обработан (для --only-unprocessed).
-- Пишется сразу по окончании профиля, а не в конце запуска, поэтому переживает падение:
--   profile  только метаданные профиля (--profile-only)
--   threads  метаданные и список веток (--threads-only; профиль чужого класса при --only-class)
--   full     ветки разобраны полностью (полный проход, --fill-threads)
-- Более глубокий режим покрывает более мелкий: full >= threads >= profile.
-- NULL = профиль ещё не обработан (или обработка прервана: 429, ошибка).
SET search_path = threads;

ALTER TABLE profiles
    ADD COLUMN processed_mode TEXT CHECK (processed_mode IN ('profile', 'threads', 'full'));

COMMENT ON COLUMN profiles.processed_mode IS
    'Самый глубокий режим, в котором профиль обработан до конца: profile | threads | full; NULL = нет';

-- Профили, обработанные до этой миграции, по тому, что уже лежит в БД
UPDATE profiles p SET processed_mode = CASE
    WHEN p.is_banned THEN 'full'
    WHEN EXISTS (SELECT 1 FROM posts t WHERE t.profile_id = p.id AND t.last_refreshed_at IS NOT NULL)
        THEN 'full'
    WHEN EXISTS (SELECT 1 FROM posts t WHERE t.profile_id = p.id) OR p.spam_class_at IS NOT NULL
        THEN 'threads'
    WHEN p.last_scraped_at IS NOT NULL THEN 'profile'
END;
