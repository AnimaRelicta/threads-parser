-- 002_about: окно «Об аккаунте» (··· → Об аккаунте): дата регистрации, местоположение, подтверждение.
SET search_path = instagram;

ALTER TABLE profiles
    ADD COLUMN joined_text       TEXT,          -- как в окне: «Октябрь 2013 г.»
    ADD COLUMN joined_date       DATE,          -- первое число месяца (в окне только месяц и год)
    ADD COLUMN account_location  TEXT,          -- «Местоположение аккаунта»
    ADD COLUMN verified_text     TEXT,          -- «Март 2019 г.»: с какого времени подтверждён
    ADD COLUMN verified_date     DATE,
    ADD COLUMN about_raw         JSONB,         -- всё окно: text, html (с ограничением), строки
    ADD COLUMN about_fetched_at  TIMESTAMPTZ;   -- NULL = окно ещё не собрано
