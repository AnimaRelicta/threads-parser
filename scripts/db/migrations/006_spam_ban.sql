-- 006_spam_ban: автобан спам-профилей (core/spam.py, scrape/spamcheck.py, spam.py).
-- Бан не удаляет данные: профиль остаётся в profiles с is_banned = true и выпадает из
-- запусков (--source), классификации и поиска новых авторов (discover не заводит его заново,
-- потому что строка уже есть). spam_whitelist = true ставит `spam.py --unban`: такой профиль
-- детектор больше не банит.
SET search_path = threads;

ALTER TABLE profiles
    ADD COLUMN is_banned       BOOLEAN NOT NULL DEFAULT false,
    ADD COLUMN ban_reason      TEXT,
    ADD COLUMN banned_at       TIMESTAMPTZ,
    ADD COLUMN spam_score      SMALLINT,
    ADD COLUMN spam_signals    JSONB,
    ADD COLUMN spam_checked_at TIMESTAMPTZ,
    ADD COLUMN spam_whitelist  BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX profiles_banned ON profiles (banned_at DESC) WHERE is_banned;

COMMENT ON COLUMN profiles.is_banned IS 'Профиль признан спамом: не парсится, не классифицируется';
COMMENT ON COLUMN profiles.spam_signals IS 'Сработавшие сигналы детектора и метрики: {no_engagement: {...}, ...}';
COMMENT ON COLUMN profiles.spam_whitelist IS 'Ручной разбан: детектор такой профиль не банит';
