-- Свои запросы на изменение БД для `uv run scripts/cli.py ig sql <имя>` (схема instagram). Формат:
--   -- name: имя          (латиница, цифры, дефис)
--   -- desc: описание     (видно в `ig sql --list`)
--   -- param: имя=значение (необязательно; значение по умолчанию, меняется `--set имя=значение`)
--   один запрос, в конце `;`. В тексте параметр пишется как :имя с приведением типа (:имя::int).
-- Без --apply запрос выполняется и откатывается: видно, сколько строк изменится.
-- UPDATE и DELETE без WHERE отклоняются. Файлы в IG_STORAGE_DIR запросы не трогают.

-- name: targets-stats
-- desc: сколько профилей в парсинге, по статусам и без данных для отбора
SELECT count(*) AS profiles,
       count(*) FILTER (WHERE is_target) AS targets,
       count(*) FILTER (WHERE NOT is_target) AS off,
       count(*) FILTER (WHERE status = 'new') AS new,
       count(*) FILTER (WHERE status = 'private') AS private,
       count(*) FILTER (WHERE status = 'not_found') AS not_found,
       count(*) FILTER (WHERE followers_count IS NULL) AS no_followers
FROM profiles;

-- name: targets-on
-- desc: вернуть в парсинг все выключенные профили (в том числе не-казахстанские: осторожно)
UPDATE profiles SET is_target = true WHERE NOT is_target;

-- name: targets-off
-- desc: выключить из парсинга профили с заданным статусом (по умолчанию not_found)
-- param: status=not_found
UPDATE profiles SET is_target = false WHERE status = :status::text AND is_target;

-- name: set-target-by-followers
-- desc: is_target = подписчиков не меньше N; не-казахстанские (по «Об аккаунте») остаются выключены
-- param: min_followers=100
-- Профили без числа подписчиков не трогаются: сначала `ig parse --profile-only`.
UPDATE profiles
SET is_target = (
    followers_count >= :min_followers::int
    AND NOT (account_location IS NULL
             AND coalesce(about_raw->>'text' ~* '(местоположение аккаунта|account based in)', false))
)
WHERE followers_count IS NOT NULL;

-- name: reset-unprocessed
-- desc: сбросить processed_mode у всех профилей (следующий --only-unprocessed возьмёт их заново)
UPDATE profiles SET processed_mode = NULL WHERE processed_mode IS NOT NULL;

-- name: retry-failed-media
-- desc: вернуть в очередь посты с неудачной или неполной докачкой медиа (затем parse --fill)
UPDATE posts SET media_status = 'pending', media_failed = 0
WHERE media_status IN ('failed', 'partial');

-- name: unprivate
-- desc: снять статус private у профилей: они снова станут new и перепроверятся при парсинге
UPDATE profiles SET status = 'new', processed_mode = NULL WHERE status = 'private';

-- name: drop-guessed-not-found
-- desc: удалить профили-«догадки» (guessed), которых нет в Instagram, вместе с их данными
WITH gone AS (
    SELECT id FROM profiles WHERE status = 'not_found' AND link_confidence = 'guessed'
), runs AS (
    DELETE FROM profile_runs WHERE profile_id IN (SELECT id FROM gone)
)
DELETE FROM profiles WHERE id IN (SELECT id FROM gone);

-- name: drop-profile
-- desc: удалить один профиль со всеми постами, комментариями и медиа (БД): --set username=ник (обязателен)
WITH gone AS (
    SELECT id FROM profiles WHERE lower(username) = lower(:username::text)
), runs AS (
    DELETE FROM profile_runs WHERE profile_id IN (SELECT id FROM gone)
)
DELETE FROM profiles WHERE id IN (SELECT id FROM gone);
