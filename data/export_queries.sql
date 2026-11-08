-- Свои запросы для `uv run scripts/export.py <имя>`. Формат:
--   -- name: имя        (латиница, цифры, дефис)
--   -- desc: описание   (видно в `export.py --list`)
--   запрос, в конце `;`
-- Доступны :since (--since) и :limit (--limit). Запросы идут в read-only транзакции.
-- Свой запрос с тем же именем, что у базового, заменяет базовый.

-- name: low-followers-active
-- desc: Профили с подписчиками меньше 500, самые активные первыми; --since: дата ветки
SELECT p.username, p.followers_count, count(t.code) AS threads,
       'https://www.threads.com/@' || p.username AS url
FROM profiles p
LEFT JOIN posts t ON t.profile_id = p.id AND (:since::timestamptz IS NULL OR t.date_utc >= :since)
WHERE coalesce(p.followers_count, 0) < 500
GROUP BY p.id
ORDER BY threads DESC, p.username
LIMIT :limit;


-- name: all-posts
SELECT * 
FROM posts 
ORDER BY "reply_count" desc 
LIMIT 100000; 

--name: all-profiles
SELECT * 
FROM profiles 
ORDER BY "id" asc 
LIMIT 100000;

--name: about-location
SELECT about_location, Count(*) 
FROM profiles 
GROUP BY about_location 
ORDER BY "count" desc 
LIMIT 100000;