-- 013_cleanup: убрать из схемы то, чем код не пользуется (проверено по репозиторию).
-- 1) search_jobs.origin/parent_job_id/topic_id/depth: задел под раскрутку тем (--follow-topics),
--    которой нет в коде: INSERT их не заполняет, нигде не читаются. Вместе с колонками уходят
--    их CHECK и внешние ключи.
-- 2) topics.is_active: тот же недостроенный --follow-topics (is_active, который читает код, —
--    это categories.is_active, он остаётся).
-- 3) location_mentions_entity (entity_type, entity_id): дубль, это префикс уникального индекса
--    location_mentions_dedup (entity_type, entity_id, source, ...): тот же поиск идёт по нему.
SET search_path = threads;

ALTER TABLE search_jobs
    DROP COLUMN IF EXISTS origin,
    DROP COLUMN IF EXISTS parent_job_id,
    DROP COLUMN IF EXISTS topic_id,
    DROP COLUMN IF EXISTS depth;

ALTER TABLE topics DROP COLUMN IF EXISTS is_active;

DROP INDEX IF EXISTS location_mentions_entity;
