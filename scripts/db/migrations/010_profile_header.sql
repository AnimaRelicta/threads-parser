-- 010_profile_header: шапка профиля: темы, ссылки в био, недавние просмотры.
SET search_path = threads;

ALTER TABLE profiles ADD COLUMN recent_views      BIGINT;
ALTER TABLE profiles ADD COLUMN topics            JSONB;
ALTER TABLE profiles ADD COLUMN links             JSONB;
ALTER TABLE profiles ADD COLUMN header_fetched_at TIMESTAMPTZ;

COMMENT ON COLUMN profiles.recent_views IS 'Недавние просмотры из шапки («38 621 недавних просмотров») на момент header_fetched_at';
COMMENT ON COLUMN profiles.topics IS 'Темы профиля (чипы в шапке): [{text, query, tag_id}]';
COMMENT ON COLUMN profiles.links IS 'Ссылки шапки без трекинга: [{text, url, kind: instagram|telegram|whatsapp|other, handle}]';
COMMENT ON COLUMN profiles.header_fetched_at IS 'Когда шапка прочитана последний раз';

-- Ник Instagram из иконки/ссылки в шапке: подтверждённая связь
ALTER TABLE instagram_links DROP CONSTRAINT IF EXISTS instagram_links_source_check;
ALTER TABLE instagram_links ADD CONSTRAINT instagram_links_source_check
    CHECK (source IN ('threads_json', 'username_guess', 'header_link'));
