-- 016_text_location: локация из текста профиля и число веток.
--   text_location      города/страны КЗ и УЗ из названия профиля, имени, био, описания (и, по флагу
--                      TEXT_GEO_SOURCES, из текстов веток): «KZ Астана, UZ Ташкент». Отдельная
--                      колонка: about_location, about_country и итог resolve() не затрагивает.
--   text_location_src  какие источники дали места: «title,bio».
--   threads_count      сколько веток у профиля (из og:description «N подписчиков • M веток •»).
SET search_path = threads;

ALTER TABLE profiles ADD COLUMN text_location     TEXT;
ALTER TABLE profiles ADD COLUMN text_location_src TEXT;
ALTER TABLE profiles ADD COLUMN threads_count     INT;

COMMENT ON COLUMN profiles.text_location IS 'Места из текста профиля (название, имя, био, описание, ветки по флагу): «KZ Астана, UZ Ташкент»; NULL = не найдено/не считалось';
COMMENT ON COLUMN profiles.text_location_src IS 'Источники text_location: title,name,bio,description,threads';
COMMENT ON COLUMN profiles.threads_count IS 'Число веток профиля из og:description на момент last_scraped_at';

-- Число веток для уже собранных профилей: целое число перед словом «веток/ветки/threads».
-- «1,2 тыс.» SQL не разбирает: такие профили заполнятся при следующем заходе.
UPDATE profiles SET threads_count = nullif(regexp_replace(
        substring(og_description from
            '(?:подписчик\S*|[Ff]ollowers?)\s*•\s*([0-9][0-9\s\u00a0]*)\s*(?:веток|ветк\S*|[Tt]hreads?)'),
        '\D', '', 'g'), '')::int
WHERE og_description IS NOT NULL;
