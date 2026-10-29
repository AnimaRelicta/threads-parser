-- 003_discovery: новые профили по авторам (discover.py, db/discovery.py).
-- Профиль, найденный по автору поста под веткой или автору ветки из поиска, получает
-- откуда пришёл (discovered_via), из чьей ветки (discovered_from_profile_id) и статус проверки
-- (review_status). review_status описывает ПРОФИЛЬ, не ветку. Профили из списка и вручную
-- заведённые остаются seed_list / new, поведение прежних запусков не меняется.
SET search_path = threads;

ALTER TABLE profiles
    ADD COLUMN discovered_via TEXT NOT NULL DEFAULT 'seed_list'
        CHECK (discovered_via IN ('seed_list', 'search', 'author')),
    ADD COLUMN discovered_from_profile_id BIGINT REFERENCES profiles(id) ON DELETE SET NULL,
    ADD COLUMN review_status TEXT NOT NULL DEFAULT 'new'
        CHECK (review_status IN ('new', 'pending_review', 'reviewed', 'review_failed')),
    ADD COLUMN mentions_count INT,
    ADD COLUMN discovered_first_code TEXT REFERENCES posts(code) ON DELETE SET NULL;

CREATE INDEX profiles_discovered ON profiles (discovered_via) WHERE discovered_via <> 'seed_list';
CREATE INDEX profiles_review ON profiles (review_status) WHERE review_status <> 'new';

COMMENT ON COLUMN profiles.discovered_via IS
    'seed_list = список/вручную; search = автор ветки из поиска; author = автор поста под веткой';
COMMENT ON COLUMN profiles.discovered_from_profile_id IS
    'Профиль, под чьей веткой автор найден (только для discovered_via = author, иначе NULL)';
COMMENT ON COLUMN profiles.review_status IS
    'Статус проверки ПРОФИЛЯ: new -> pending_review -> reviewed | review_failed';
COMMENT ON COLUMN profiles.mentions_count IS
    'Сколько раз автор встретился при поиске (постов под ветками + веток из поиска за период)';
COMMENT ON COLUMN profiles.discovered_first_code IS
    'Код ветки (posts.code), где автор встретился впервые за период поиска';
