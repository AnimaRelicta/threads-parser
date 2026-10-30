-- 009_about_profile: данные окна «Об этом профиле» (···  → Об этом профиле).
SET search_path = threads;

ALTER TABLE profiles ADD COLUMN about_name       TEXT;
ALTER TABLE profiles ADD COLUMN about_joined     TEXT;
ALTER TABLE profiles ADD COLUMN about_location   TEXT;
ALTER TABLE profiles ADD COLUMN about_raw        JSONB;
ALTER TABLE profiles ADD COLUMN about_fetched_at TIMESTAMPTZ;

COMMENT ON COLUMN profiles.about_name IS 'Имя/название из окна «Об этом профиле»';
COMMENT ON COLUMN profiles.about_joined IS 'Дата регистрации как в окне («Октябрь 2026 г.»)';
COMMENT ON COLUMN profiles.about_location IS 'Местоположение из окна («Беларусь»); NULL = строки нет';
COMMENT ON COLUMN profiles.about_raw IS 'Все строки окна: {заголовок: значение}';
COMMENT ON COLUMN profiles.about_fetched_at IS 'Когда окно прочитано; NULL = ещё не брали (повторится при проходе)';
