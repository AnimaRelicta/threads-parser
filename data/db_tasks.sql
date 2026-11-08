-- Свои запросы на изменение БД для `uv run scripts/sqlrun.py <имя>`. Формат:
--   -- name: имя          (латиница, цифры, дефис)
--   -- desc: описание     (видно в `sqlrun.py --list`)
--   -- param: имя=значение (необязательно; значение по умолчанию, меняется `--set имя=значение`)
--   один запрос, в конце `;`. В тексте параметр пишется как :имя с приведением типа (:имя::int).
-- Без --apply запрос выполняется и откатывается: видно, сколько строк изменится.
-- UPDATE и DELETE без WHERE отклоняются.

-- name: select-targets
-- desc: is_target = подписчиков не меньше N и в среднем не меньше M веток в день с регистрации
-- param: min_followers=100
-- param: min_per_day=1
-- Профили без числа подписчиков, веток или даты регистрации не трогаются: сначала --profile-only.
WITH joined AS (
    SELECT p.id,
           make_date((regexp_match(p.about_joined, '([0-9]{4})'))[1]::int, m.n, 1) AS d
    FROM profiles p
    JOIN (VALUES ('янв', 1), ('jan', 1), ('фев', 2), ('feb', 2), ('мар', 3), ('mar', 3),
                 ('апр', 4), ('apr', 4), ('мая', 5), ('май', 5), ('may', 5), ('июн', 6),
                 ('jun', 6), ('июл', 7), ('jul', 7), ('авг', 8), ('aug', 8), ('сен', 9),
                 ('sep', 9), ('окт', 10), ('oct', 10), ('ноя', 11), ('nov', 11),
                 ('дек', 12), ('dec', 12)) m(stem, n)
      ON lower(p.about_joined) LIKE m.stem || '%'
    WHERE p.about_joined ~ '[0-9]{4}'
)
UPDATE profiles p
SET is_target = (
    p.followers_count >= :min_followers::int
    AND p.threads_count::float8 / greatest(current_date - j.d, 1) >= :min_per_day::float8
)
FROM joined j
WHERE j.id = p.id AND p.followers_count IS NOT NULL AND p.threads_count IS NOT NULL;

-- name: targets-stats
-- desc: сколько профилей в парсинге и сколько без данных для отбора
SELECT count(*) AS profiles,
       count(*) FILTER (WHERE is_target) AS targets,
       count(*) FILTER (WHERE followers_count IS NULL OR threads_count IS NULL
                          OR about_joined IS NULL) AS no_stats
FROM profiles;

-- name: targets-on
-- desc: вернуть в парсинг все профили, которые выключены
UPDATE profiles SET is_target = true WHERE NOT is_target;

-- name: reset-unprocessed
-- desc: сбросить processed_mode у профилей, у которых нет данных «Об этом профиле»
UPDATE profiles
SET processed_mode = NULL
WHERE about_raw IS NULL;

-- name: mark-processed
-- desc: пропустить уже обработанные профили (about_raw есть) как обработанные в режиме profile
UPDATE profiles
SET processed_mode = 'profile'
WHERE about_raw IS NOT NULL;

-- name: category-by-location
-- desc: категория профилей по месту из «Об этом профиле»
-- param: category_id=1
-- param: location=Казахстан
UPDATE profiles
SET category_id = :category_id::int
WHERE about_location LIKE '%' || :location::text || '%';
