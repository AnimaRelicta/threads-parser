-- Прогресс парсинга Instagram (схема instagram). Только чтение; блоки независимы.
--   psql "$DATABASE_URL" -f scripts/ig/db/progress.sql
-- Можно гонять по кругу: watch -n 60 'psql "$DATABASE_URL" -f scripts/ig/db/progress.sql'
-- Профиль «обработан», если у него есть processed_mode (profile < posts < full).
-- Скорость считается по profile_runs: завершённые профили (ok, empty, private, not_found).

SET search_path = instagram;

\echo '== 1. Обработано профилей (is_target) =='
SELECT coalesce(processed_mode, '- не обработан') AS processed_mode,
       count(*) AS profiles,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct
FROM profiles
WHERE is_target
GROUP BY 1
ORDER BY 1;

\echo '== 2. Скорость: профилей в час и секунд на профиль =='
SELECT w.label,
       count(r.id) AS profiles,
       round(count(r.id) / w.hours, 1) AS per_hour,
       round(avg(r.duration_sec), 1) AS sec_per_profile,
       sum(r.posts_new) AS posts_new,
       sum(r.comments_new) AS comments_new,
       sum(r.media_new_files) AS media_files
FROM (VALUES ('15 минут', interval '15 minutes', 0.25),
             ('1 час', interval '1 hour', 1.0),
             ('6 часов', interval '6 hours', 6.0),
             ('24 часа', interval '24 hours', 24.0)) w(label, span, hours)
LEFT JOIN profile_runs r
       ON r.finished_at >= now() - w.span
      AND r.status IN ('ok', 'empty', 'private', 'not_found')
GROUP BY w.label, w.hours
ORDER BY w.hours;

\echo '== 3. По аккаунтам за 24 часа: профили, 429, авторизация, пауза =='
SELECT a.name,
       a.status,
       count(r.id) FILTER (WHERE r.status IN ('ok', 'empty', 'private', 'not_found')) AS done,
       count(r.id) FILTER (WHERE r.abort_kind = '429') AS errors_429,
       count(r.id) FILTER (WHERE r.abort_kind = 'auth') AS errors_auth,
       round(avg(r.duration_sec) FILTER (WHERE r.status = 'ok'), 1) AS sec_per_profile,
       CASE WHEN a.cooldown_until > now()
            THEN 'пауза до ' || to_char(a.cooldown_until, 'HH24:MI') END AS cooldown
FROM accounts a
LEFT JOIN profile_runs r
       ON r.account = a.name AND r.finished_at >= now() - interval '24 hours'
GROUP BY a.name, a.status, a.cooldown_until
ORDER BY a.name;

\echo '== 4. Сколько осталось и когда закончится (скорость за последние 2 часа) =='
WITH left_now AS (
    SELECT count(*) AS n FROM profiles WHERE is_target AND processed_mode IS NULL
), speed AS (
    SELECT count(*) / 2.0 AS per_hour
    FROM profile_runs
    WHERE finished_at >= now() - interval '2 hours'
      AND status IN ('ok', 'empty', 'private', 'not_found')
)
SELECT l.n AS left_profiles,
       round(s.per_hour::numeric, 1) AS per_hour,
       CASE WHEN s.per_hour > 0 THEN round((l.n / s.per_hour)::numeric, 1) END AS hours_left,
       CASE WHEN s.per_hour > 0
            THEN to_char(now() + (l.n / s.per_hour)::float8 * interval '1 hour', 'YYYY-MM-DD HH24:MI')
       END AS eta
FROM left_now l, speed s;
