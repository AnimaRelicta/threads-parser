-- 008_geo_instagram: биография профиля, упоминания локаций (КЗ/УЗ), связь с Instagram.
SET search_path = threads;

ALTER TABLE profiles ADD COLUMN bio TEXT;
COMMENT ON COLUMN profiles.bio IS 'Биография профиля (biography из embedded JSON страницы), NULL = не найдена/не бралась';

-- Упоминания городов/стран КЗ и УЗ: в био профиля или в тексте поста.
-- Одна сущность (профиль или пост) может дать несколько строк (упомянуто несколько городов).
CREATE TABLE location_mentions (
    id           BIGSERIAL PRIMARY KEY,
    entity_type  TEXT NOT NULL CHECK (entity_type IN ('profile', 'post')),
    entity_id    TEXT NOT NULL,              -- profiles.id::text либо posts.code
    profile_id   BIGINT REFERENCES profiles(id) ON DELETE CASCADE,  -- заполнен всегда: профиль поста/автора
    source       TEXT NOT NULL CHECK (source IN ('bio', 'post_text')),
    country      TEXT NOT NULL CHECK (country IN ('KZ', 'UZ')),
    city         TEXT,                       -- NULL = нашли только страну, без конкретного города
    matched_text TEXT NOT NULL,              -- что именно совпало в исходном тексте
    detector_version SMALLINT NOT NULL DEFAULT 1,  -- версия справочника core/geo.py (для --rescan)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX location_mentions_entity  ON location_mentions (entity_type, entity_id);
CREATE INDEX location_mentions_profile ON location_mentions (profile_id);
CREATE INDEX location_mentions_country_city ON location_mentions (country, city);

-- Для --rescan (пересчёт без повторного похода на entity): уникальность находки,
-- чтобы повторный rescan не плодил дубликаты одной и той же строки.
CREATE UNIQUE INDEX location_mentions_dedup
    ON location_mentions (entity_type, entity_id, source, country, coalesce(city, ''), matched_text);

-- Связь ник Threads <-> ник Instagram.
CREATE TABLE instagram_links (
    id                  BIGSERIAL PRIMARY KEY,
    profile_id          BIGINT NOT NULL UNIQUE REFERENCES profiles(id) ON DELETE CASCADE,
    instagram_username  TEXT NOT NULL,
    source              TEXT NOT NULL CHECK (source IN ('threads_json', 'username_guess')),
    confidence          TEXT NOT NULL CHECK (confidence IN ('confirmed', 'guessed')),
    matched_raw         TEXT,                -- кусок исходных данных, где нашли (для разбора ложных срабатываний)
    detector_version    SMALLINT NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX instagram_links_username ON instagram_links (lower(instagram_username));
