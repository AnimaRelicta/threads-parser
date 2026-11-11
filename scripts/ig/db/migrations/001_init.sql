-- 001_init: схема instagram (ТЗ docs/TZ_instagram.md, п. 4). Таблица schema_migrations создаётся раннером.
CREATE SCHEMA IF NOT EXISTS instagram;
SET search_path = instagram;

-- ---------- аккаунты и запуски ----------
CREATE TABLE accounts (
    name           TEXT PRIMARY KEY,                 -- account_001
    username       TEXT,                             -- ник в Instagram, может быть NULL
    user_id        TEXT UNIQUE,                      -- ds_user_id, ключ дедупликации при import
    cookies_file   TEXT NOT NULL,                    -- имя файла в data/ig_cookies/
    status         TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','needs_login','disabled')),
    proxy          TEXT,                             -- NULL = общий PROXY
    cooldown_until TIMESTAMPTZ,                      -- пауза после 429: до этого времени аккаунт не берётся
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at  TIMESTAMPTZ,
    last_run_at    TIMESTAMPTZ,
    last_error     TEXT
);

CREATE TABLE runs (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('parse','import')),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','ok','error','interrupted')),
    settings    JSONB NOT NULL DEFAULT '{}',
    error       TEXT
);

CREATE TABLE run_accounts (
    run_id      BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    account     TEXT   NOT NULL REFERENCES accounts(name),
    status      TEXT   NOT NULL DEFAULT 'ok',
    error       TEXT,
    errors_429  INT NOT NULL DEFAULT 0,
    errors_auth INT NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, account)
);

-- ---------- профили ----------
CREATE TABLE profiles (
    id                 BIGSERIAL PRIMARY KEY,
    username           TEXT NOT NULL,
    ig_user_id         TEXT UNIQUE,                  -- числовой pk, ловим по нему переименование
    -- откуда взят профиль
    source             TEXT NOT NULL CHECK (source IN ('file','threads','cli')),  -- впервые, не перезаписывается
    sources            TEXT[] NOT NULL DEFAULT '{}', -- все источники, где профиль встретился
    source_ref         TEXT,                         -- имя файла либо threads.instagram_links#id
    threads_profile_id BIGINT,                       -- мягкая ссылка на threads.profiles.id (без FK)
    threads_username   TEXT,
    link_confidence    TEXT CHECK (link_confidence IN ('confirmed','guessed')),
    link_source        TEXT,                         -- header_link | threads_json | username_guess
    -- состояние
    status             TEXT NOT NULL DEFAULT 'new'
                       CHECK (status IN ('new','ok','private','not_found','error')),
    is_target          BOOLEAN NOT NULL DEFAULT true,
    processed_mode     TEXT CHECK (processed_mode IN ('profile','posts','full')),
    -- метаданные
    full_name          TEXT,
    biography          TEXT,
    bio_links          JSONB,
    external_url       TEXT,
    category           TEXT,
    followers_count    INT,
    following_count    INT,
    posts_count        INT,
    is_private         BOOLEAN,
    is_verified        BOOLEAN,
    is_business        BOOLEAN,
    profile_pic_url    TEXT,
    avatar_file        TEXT,                         -- относительно IG_STORAGE_DIR
    meta_raw           JSONB,                        -- сырой ответ, из которого взяты поля
    added_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_seen_at      TIMESTAMPTZ,
    meta_fetched_at    TIMESTAMPTZ,
    last_scraped_at    TIMESTAMPTZ
);
CREATE UNIQUE INDEX profiles_username_lc ON profiles (lower(username));
CREATE INDEX profiles_source ON profiles (source);
CREATE INDEX profiles_status ON profiles (status);
CREATE INDEX profiles_processed ON profiles (processed_mode);
CREATE INDEX profiles_threads ON profiles (threads_profile_id) WHERE threads_profile_id IS NOT NULL;

COMMENT ON COLUMN profiles.source IS 'Откуда профиль взят впервые: file | threads | cli. Не перезаписывается';
COMMENT ON COLUMN profiles.sources IS 'Все источники, где профиль встретился (например {file,threads})';
COMMENT ON COLUMN profiles.threads_profile_id IS 'threads.profiles.id; без FK, схемы независимы';
COMMENT ON COLUMN profiles.processed_mode IS 'Чем профиль обработан до конца: profile < posts < full (для --only-unprocessed)';

CREATE TABLE profile_anchors (
    profile_id   BIGINT PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    newest_code  TEXT NOT NULL,
    newest_date  TIMESTAMPTZ,
    covered_from TIMESTAMPTZ,                        -- NULL = пройден весь профиль
    known_posts  INT NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE profile_runs (
    id                   BIGSERIAL PRIMARY KEY,
    run_id               BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    account              TEXT   NOT NULL REFERENCES accounts(name),
    profile_id           BIGINT NOT NULL REFERENCES profiles(id),
    status               TEXT NOT NULL,              -- ok|empty|private|not_found|abort|error|not_run
    mode                 TEXT,                       -- profile|posts|full|fill
    stop_reason          TEXT,                       -- end|old|limit|known|enough
    abort_kind           TEXT,                       -- 429|auth|NULL
    note                 TEXT,
    started_at           TIMESTAMPTZ,
    finished_at          TIMESTAMPTZ,
    duration_sec         NUMERIC(10,1),
    posts_new            INT NOT NULL DEFAULT 0,
    posts_refreshed      INT NOT NULL DEFAULT 0,
    posts_unchanged      INT NOT NULL DEFAULT 0,
    posts_skipped_old    INT NOT NULL DEFAULT 0,
    posts_in_period      INT NOT NULL DEFAULT 0,
    posts_newest         TIMESTAMPTZ,
    comments_new         INT NOT NULL DEFAULT 0,
    media_new_files      INT NOT NULL DEFAULT 0,
    media_new_images     INT NOT NULL DEFAULT 0,
    media_new_videos     INT NOT NULL DEFAULT 0,
    media_new_bytes      BIGINT NOT NULL DEFAULT 0,
    media_failed         INT NOT NULL DEFAULT 0,
    pages_opened         INT NOT NULL DEFAULT 0
);
CREATE INDEX profile_runs_profile ON profile_runs (profile_id);

-- ---------- посты ----------
CREATE TABLE posts (
    code               TEXT PRIMARY KEY,             -- shortcode, глобально уникален
    media_id           TEXT,                         -- числовой pk (нужен для комментариев)
    profile_id         BIGINT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    url                TEXT NOT NULL,
    kind               TEXT CHECK (kind IN ('photo','carousel','video','reel')),  -- NULL = ещё не известно
    caption            TEXT,
    taken_at           TIMESTAMPTZ,
    likes              INT,                          -- NULL, если лайки скрыты
    comments_count     INT,
    views              INT,
    hashtags           TEXT[] NOT NULL DEFAULT '{}',
    mentions           TEXT[] NOT NULL DEFAULT '{}',
    tagged_users       TEXT[] NOT NULL DEFAULT '{}',
    location_name      TEXT,
    location           JSONB,
    is_pinned          BOOLEAN NOT NULL DEFAULT false,
    comments_disabled  BOOLEAN NOT NULL DEFAULT false,
    carousel_size      INT,
    media_status       TEXT NOT NULL DEFAULT 'pending'
                       CHECK (media_status IN ('pending','done','partial','skipped','failed')),
    media_failed       INT NOT NULL DEFAULT 0,
    comments_status    TEXT NOT NULL DEFAULT 'none'
                       CHECK (comments_status IN ('none','partial','done')),
    comments_depth     INT NOT NULL DEFAULT 0,
    comments_collected INT NOT NULL DEFAULT 0,
    first_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_refreshed_at  TIMESTAMPTZ                   -- NULL = собран лёгким режимом (--posts-only)
);
CREATE INDEX posts_profile_date ON posts (profile_id, taken_at DESC);
CREATE INDEX posts_date ON posts (taken_at DESC);
CREATE INDEX posts_media_status ON posts (media_status) WHERE media_status <> 'done';

-- ---------- комментарии (в Instagram два уровня: комментарий и ответы) ----------
CREATE TABLE comments (
    id              TEXT PRIMARY KEY,                -- pk комментария
    post_code       TEXT NOT NULL REFERENCES posts(code) ON DELETE CASCADE,
    parent_id       TEXT,                            -- NULL = верхний уровень, иначе comments.id
    depth           SMALLINT NOT NULL CHECK (depth BETWEEN 1 AND 2),
    author_username TEXT,
    author_id       TEXT,
    text            TEXT,
    created_at      TIMESTAMPTZ,
    likes           INT,
    reply_count     INT NOT NULL DEFAULT 0,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX comments_post ON comments (post_code, depth);
CREATE INDEX comments_parent ON comments (parent_id);

-- ---------- медиа (сами файлы в IG_STORAGE_DIR) ----------
CREATE TABLE media (
    id           BIGSERIAL PRIMARY KEY,
    post_code    TEXT NOT NULL REFERENCES posts(code) ON DELETE CASCADE,
    position     INT  NOT NULL DEFAULT 1,
    kind         TEXT NOT NULL CHECK (kind IN ('image','video')),
    file_path    TEXT NOT NULL UNIQUE,               -- относительно IG_STORAGE_DIR
    bytes        BIGINT,
    width        INT,
    height       INT,
    duration_sec NUMERIC(10,2)
);
CREATE INDEX media_post ON media (post_code);
