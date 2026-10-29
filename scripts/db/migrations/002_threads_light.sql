-- 002_threads_light: быстрый сбор веток профиля (run.py --threads-only).
-- Ветка = запись верхнего уровня в ленте профиля = строка posts. Пост под веткой = строка comments.
-- Ветка, собранная быстрым режимом, лежит в posts с last_refreshed_at IS NULL (ещё не разобрана);
-- полный проход подбирает такие ветки сам. Индекс ускоряет выборку «ветки профиля без разбора».
SET search_path = threads;

CREATE INDEX IF NOT EXISTS posts_profile_pending
    ON posts (profile_id, date_utc DESC)
    WHERE last_refreshed_at IS NULL;

COMMENT ON TABLE posts IS 'Ветки (записи верхнего уровня в ленте профиля); посты под веткой лежат в comments';
COMMENT ON COLUMN posts.last_refreshed_at IS 'NULL = ветка найдена (быстрый сбор или поиск), но не разобрана полностью';
COMMENT ON COLUMN profile_runs.mode IS 'full | only_new | threads (быстрый сбор веток; posts_* считают ветки)';
