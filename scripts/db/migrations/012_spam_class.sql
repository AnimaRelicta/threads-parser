-- 012_spam_class: класс спам-профиля по первым веткам (core/spamclass.py).
-- Метка не банит: is_banned по-прежнему выставляет только core/spam.py.
--   1 board_author  редкие ветки, много комментариев (барахолка)
--   2 seller        частые ветки, мало комментариев (продаёт своё)
--   3 spammer       как 2, подписчиков меньше порога
-- NULL = класса нет (или ещё не считали: смотрите spam_class_at).
SET search_path = threads;

ALTER TABLE profiles
    ADD COLUMN spam_class      SMALLINT CHECK (spam_class IN (1, 2, 3)),
    ADD COLUMN spam_class_info JSONB,
    ADD COLUMN spam_class_at   TIMESTAMPTZ;

CREATE INDEX profiles_spam_class ON profiles (spam_class) WHERE spam_class IS NOT NULL;

COMMENT ON COLUMN profiles.spam_class IS '1 барахолка | 2 продавец | 3 спамер; NULL = класса нет';
COMMENT ON COLUMN profiles.spam_class_info IS 'Метрики и причина класса: {reason, sample, metrics}';
COMMENT ON COLUMN profiles.spam_class_at IS 'Когда класс последний раз считали (даже если он NULL)';
