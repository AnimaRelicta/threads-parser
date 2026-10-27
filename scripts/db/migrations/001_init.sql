-- 001_init: схема threads (ТЗ, п. 4). Таблица schema_migrations создаётся раннером (db/migrate.py).
CREATE SCHEMA IF NOT EXISTS threads;
SET search_path = threads;

-- ---------- аккаунты и запуски (замена accounts.json) ----------
CREATE TABLE accounts (
    name          TEXT PRIMARY KEY,                 -- account_001
    username      TEXT,                             -- ник в Threads, может быть NULL
    user_id       TEXT UNIQUE,                      -- ds_user_id, ключ дедупликации при import
    cookies_file  TEXT NOT NULL,                    -- имя файла в data/cookies/
    status        TEXT NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active','needs_login','disabled')),
    proxy         TEXT,                             -- socks5://..., NULL = общий PROXY
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    last_run_at   TIMESTAMPTZ,
    last_error    TEXT
);

CREATE TABLE runs (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('profiles','search')),
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status      TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','ok','error','interrupted')),
    settings    JSONB NOT NULL DEFAULT '{}',        -- run_settings из run(): since, limit, depth, ...
    error       TEXT
);

CREATE TABLE run_accounts (                          -- то, что раньше было записью в accounts.json.runs[]
    run_id      BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    account     TEXT   NOT NULL REFERENCES accounts(name),
    status      TEXT   NOT NULL DEFAULT 'ok',
    error       TEXT,
    errors_429  INT NOT NULL DEFAULT 0,
    errors_auth INT NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, account)
);

-- ---------- профили (замена profile.json, anchor.json, threads_profiles.txt как состояния) ----------
CREATE TABLE profiles (
    id              BIGSERIAL PRIMARY KEY,
    username        TEXT NOT NULL,
    user_id         TEXT,
    url             TEXT,
    title           TEXT,
    og_title        TEXT,
    og_description  TEXT,
    og_image        TEXT,
    is_target       BOOLEAN NOT NULL DEFAULT true,   -- есть в списке на парсинг
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_scraped_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX profiles_username_lc ON profiles (lower(username));

CREATE TABLE profile_anchors (
    profile_id   BIGINT PRIMARY KEY REFERENCES profiles(id) ON DELETE CASCADE,
    newest_code  TEXT NOT NULL,
    newest_date  TIMESTAMPTZ,
    covered_from TIMESTAMPTZ,                        -- NULL = пройден весь профиль (как в anchor.json)
    known_posts  INT NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE profile_runs (                          -- профильная часть записи запуска (profile_run_entry)
    id                   BIGSERIAL PRIMARY KEY,
    run_id               BIGINT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    account              TEXT   NOT NULL REFERENCES accounts(name),
    profile_id           BIGINT NOT NULL REFERENCES profiles(id),
    status               TEXT NOT NULL,              -- ok|empty|not_found|abort|error|not_run
    mode                 TEXT,                       -- full|only_new
    stop_reason          TEXT,                       -- end|old|limit|known
    abort_kind           TEXT,                       -- 429|auth|NULL
    note                 TEXT,
    started_at           TIMESTAMPTZ,
    finished_at          TIMESTAMPTZ,
    duration_sec         NUMERIC(10,1),
    posts_new            INT NOT NULL DEFAULT 0,
    posts_refreshed      INT NOT NULL DEFAULT 0,
    posts_unchanged      INT NOT NULL DEFAULT 0,
    posts_skipped_old    INT NOT NULL DEFAULT 0,
    posts_skipped_video  INT NOT NULL DEFAULT 0,
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

-- ---------- темы ----------
CREATE TABLE topics (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('threads','custom')),
    is_active     BOOLEAN NOT NULL DEFAULT true,     -- можно ли раскручивать через --follow-topics
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX topics_name_lc ON topics (lower(name));

CREATE TABLE custom_topics (                         -- свои темы, правила задаёте вручную
    id        SERIAL PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    pattern   TEXT NOT NULL,                         -- регулярка Python, (?i) подставляется кодом
    priority  INT  NOT NULL DEFAULT 100,             -- меньше = раньше
    is_active BOOLEAN NOT NULL DEFAULT true
);

-- ---------- поиск: очередь задач ----------
CREATE TABLE search_jobs (
    id            BIGSERIAL PRIMARY KEY,
    job_key       TEXT NOT NULL UNIQUE,              -- lower(query)|mode|after|before, как job_key() в коде
    query         TEXT NOT NULL,
    mode          TEXT NOT NULL CHECK (mode IN ('top','recent')),
    after_date    DATE,
    before_date   DATE,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','done','failed')),
    retries       INT  NOT NULL DEFAULT 0,
    origin        TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual','follow_topic')),
    parent_job_id BIGINT REFERENCES search_jobs(id),
    topic_id      BIGINT REFERENCES topics(id),      -- для origin = follow_topic
    depth         INT  NOT NULL DEFAULT 0,           -- глубина раскрутки тем
    run_id        BIGINT REFERENCES runs(id),
    account       TEXT REFERENCES accounts(name),
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    posts_found   INT NOT NULL DEFAULT 0,
    posts_new     INT NOT NULL DEFAULT 0,
    posts_dupes   INT NOT NULL DEFAULT 0,
    last_error    TEXT
);
CREATE INDEX search_jobs_status ON search_jobs (status);

-- ---------- посты (единая таблица для обоих пайплайнов) ----------
CREATE TABLE posts (
    code               TEXT PRIMARY KEY,             -- shortcode Threads, глобально уникален
    url                TEXT NOT NULL,
    author_username    TEXT NOT NULL,
    author_user_id     TEXT,
    profile_id         BIGINT REFERENCES profiles(id),  -- заполнен, если автор в списке профилей
    text               TEXT,
    topic_id           BIGINT REFERENCES topics(id),
    date_utc           TIMESTAMPTZ,
    likes              INT,
    reply_count        INT,
    reposts            INT,
    quotes             INT,
    hashtags           TEXT[] NOT NULL DEFAULT '{}',
    mentions           TEXT[] NOT NULL DEFAULT '{}',
    videos_skipped     INT NOT NULL DEFAULT 0,
    media_failed       INT NOT NULL DEFAULT 0,
    visited_reply_count INT,                         -- NULL = обход комментариев не завершён
    comments_depth     INT NOT NULL DEFAULT 0,
    filter_status      TEXT NOT NULL DEFAULT 'rejected'
                       CHECK (filter_status IN ('approved','rejected')),
    filter_matched     TEXT,                         -- что сработало: 'олх' / 'olx' / NULL
    filter_matched_in  TEXT,                         -- 'text' | 'topic' | NULL
    filter_version     SMALLINT NOT NULL DEFAULT 1,  -- версия правил фильтра (для --refilter)
    first_seen_via     TEXT NOT NULL CHECK (first_seen_via IN ('profile','search')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_refreshed_at  TIMESTAMPTZ
);
CREATE INDEX posts_date          ON posts (date_utc DESC);
CREATE INDEX posts_filter_date   ON posts (filter_status, date_utc DESC);
CREATE INDEX posts_topic         ON posts (topic_id);
CREATE INDEX posts_author_lc     ON posts (lower(author_username));
CREATE INDEX posts_profile       ON posts (profile_id);

-- откуда пост пришёл в поиске: один пост находится многими запросами
CREATE TABLE post_hits (
    post_code TEXT   NOT NULL REFERENCES posts(code) ON DELETE CASCADE,
    job_id    BIGINT NOT NULL REFERENCES search_jobs(id) ON DELETE CASCADE,
    seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (post_code, job_id)
);

-- замена skipped.json
CREATE TABLE skipped_posts (
    code       TEXT PRIMARY KEY,
    profile_id BIGINT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    reason     TEXT NOT NULL CHECK (reason IN ('old','video')),
    post_date  TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX skipped_profile ON skipped_posts (profile_id);

-- ---------- комментарии ----------
CREATE TABLE comments (
    id                  TEXT PRIMARY KEY,            -- pk комментария
    post_code           TEXT NOT NULL REFERENCES posts(code) ON DELETE CASCADE,
    code                TEXT,                        -- shortcode самого комментария
    parent_id           TEXT,                        -- NULL = ответ на сам пост (depth 1), иначе comments.id
    depth               INT  NOT NULL,
    reply_to_author     TEXT,
    author              TEXT NOT NULL,
    text                TEXT,
    posted_at           TIMESTAMPTZ,
    likes               INT,
    url                 TEXT,
    reply_count         INT NOT NULL DEFAULT 0,
    visited_reply_count INT,                         -- сколько ответов было, когда зашли внутрь
    media_failed        INT NOT NULL DEFAULT 0,
    collected_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX comments_post   ON comments (post_code, depth);
CREATE INDEX comments_parent ON comments (parent_id);

-- ---------- метаданные медиа (сами файлы на диске) ----------
CREATE TABLE media (
    id         BIGSERIAL PRIMARY KEY,
    post_code  TEXT NOT NULL REFERENCES posts(code) ON DELETE CASCADE,
    comment_id TEXT REFERENCES comments(id) ON DELETE CASCADE,  -- NULL = медиа самого поста
    kind       TEXT NOT NULL CHECK (kind IN ('image','video')),
    file_path  TEXT NOT NULL UNIQUE,                 -- относительно MEDIA_DIR
    position   INT  NOT NULL DEFAULT 1,
    bytes      BIGINT
);
CREATE INDEX media_post ON media (post_code);
