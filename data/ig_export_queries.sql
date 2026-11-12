-- Свои запросы для `uv run scripts/cli.py ig export <имя>`. Формат:
--   -- name: имя        (латиница, цифры, дефис)
--   -- desc: описание   (видно в `ig export --list`)
--   запрос, в конце `;`
-- Доступны :since (--since) и :limit (--limit). Запросы идут в read-only транзакции.
-- Свой запрос с тем же именем, что у базового, заменяет базовый.

-- name: top-profiles
-- desc: Топ профилей по подписчикам (не меньше 1000), только казахстанские и в парсинге; --since: когда добавлен
SELECT p.username, 'https://www.instagram.com/' || p.username AS url, p.followers_count,
       p.posts_count, p.category, p.account_location, p.source, p.status
FROM profiles p
WHERE p.is_target AND p.followers_count >= 1000
  AND (:since::timestamptz IS NULL OR p.added_at >= :since)
ORDER BY p.followers_count DESC, p.username
LIMIT :limit;

-- name: posts-period
-- desc: Посты за период с текстом и счётчиками; --since: дата поста (по умолчанию все)
SELECT p.username, po.code, po.url, po.kind, po.taken_at, po.likes, po.comments_count,
       po.views, po.caption
FROM posts po
JOIN profiles p ON p.id = po.profile_id
WHERE :since::timestamptz IS NULL OR po.taken_at >= :since
ORDER BY po.taken_at DESC NULLS LAST
LIMIT :limit;

-- name: posts-no-files
-- desc: Посты без единого файла в media (ссылки, чтобы докачать: parse --fill); --since: дата поста
SELECT p.username, po.code, po.url, po.kind, po.taken_at, po.media_status, po.media_failed
FROM posts po
JOIN profiles p ON p.id = po.profile_id
WHERE NOT EXISTS (SELECT 1 FROM media m WHERE m.post_code = po.code)
  AND (:since::timestamptz IS NULL OR po.taken_at >= :since)
ORDER BY po.taken_at DESC NULLS LAST
LIMIT :limit;
