-- 007_followers: число подписчиков профиля (og:description страницы, запасной путь: JSON).
-- Нужно спам-детектору: профиль с числом подписчиков >= SPAM_MIN_FOLLOWERS спамом не считается.
-- NULL = число не удалось определить (детектор тогда проверяет профиль как обычно).
SET search_path = threads;

ALTER TABLE profiles ADD COLUMN followers_count INT;

COMMENT ON COLUMN profiles.followers_count IS
    'Подписчиков на момент последнего открытия профиля; NULL = неизвестно';
