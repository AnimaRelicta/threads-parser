-- 011_geo_signals: геосигналы профиля (about_location, телефон WhatsApp, темы, имя из about).
SET search_path = threads;

-- Новые источники упоминаний. Телефон хранит только префикс («+7708»), не номер.
ALTER TABLE location_mentions DROP CONSTRAINT IF EXISTS location_mentions_source_check;
ALTER TABLE location_mentions ADD CONSTRAINT location_mentions_source_check
    CHECK (source IN ('bio', 'post_text', 'about_location', 'phone', 'topic', 'about_name'));

-- Страна из «Об этом профиле»: факт, а не итог. OTHER = указана, но не КЗ/УЗ («Беларусь»),
-- чтобы такой профиль не считался «без данных». Итоговая страна считается при чтении
-- (core/geo.py::resolve), поэтому правила приоритета меняются без миграций.
ALTER TABLE profiles ADD COLUMN about_country TEXT
    CHECK (about_country IN ('KZ', 'UZ', 'OTHER'));
COMMENT ON COLUMN profiles.about_country IS 'Страна из about_location: KZ|UZ|OTHER; NULL = не указана/не собрана';

-- Версия справочника core/geo.py, по которой пересчитан профиль/пост (NULL = ещё не считали);
-- по ней `geo rescan` без --force берёт только устаревшее.
ALTER TABLE profiles ADD COLUMN geo_version SMALLINT;
ALTER TABLE posts ADD COLUMN geo_version SMALLINT;
COMMENT ON COLUMN profiles.geo_version IS 'DETECTOR_VERSION последнего пересчёта локаций профиля';
COMMENT ON COLUMN posts.geo_version IS 'DETECTOR_VERSION последнего пересчёта локаций поста';
