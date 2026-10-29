-- 004_audit_runs: аудит схемы и единый учёт запусков.
-- 1) custom_topics нигде в коде не используется (проверено по репозиторию): удаляем.
-- 2) skipped_posts ОСТАВЛЕНА: db/posts.py (load_skipped, mark_skipped) и scrape/profile.py
--    пропускают по ней уже виденные старые и видео-ветки; профильные счётчики её не заменяют.
-- 3) search_jobs (очередь задач) и runs (сводка запуска) не дублируют друг друга: без изменений.
-- 4) runs.kind расширен под discover (шаг 4) и classify (шаг 6).
SET search_path = threads;

DROP TABLE IF EXISTS custom_topics;

COMMENT ON TABLE skipped_posts IS
    'Пропущенные ВЕТКИ профиля (старые и видео), чтобы профильный проход не открывал их снова; '
    'посты под ветками здесь не хранятся (они в comments)';

-- имя ограничения из 001 неявное: находим CHECK по колонке kind и пересоздаём
DO $$
DECLARE c TEXT;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'threads.runs'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) LIKE '%kind%'
    LOOP
        EXECUTE format('ALTER TABLE threads.runs DROP CONSTRAINT %I', c);
    END LOOP;
END $$;

ALTER TABLE runs
    ADD CONSTRAINT runs_kind_check CHECK (kind IN ('profiles', 'search', 'discover', 'classify'));

COMMENT ON COLUMN runs.kind IS
    'profiles (в т. ч. быстрый сбор веток: settings.mode = threads) | search | discover | classify';
COMMENT ON COLUMN runs.settings IS
    'Параметры запуска; для discover/classify сюда же пишутся итоги (found, added, ...) без аккаунтов';
