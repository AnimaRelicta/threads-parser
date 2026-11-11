# ТЗ: перенос Instagram-парсера (insta-parser) в проект threads-parser

Статус: черновик ТЗ, код не писался. Основан на чтении архивов `threads-parser-main.zip` и `insta-parser.zip`.
Предполагаемое место в репозитории: `docs/TZ_instagram.md`.

---

## 0. Суть в пяти строках

1. Логику парсинга Instagram (сетка **n аккаунтов × m вкладок**, раздача профилей свободным аккаунтам, якорь «только новое», `--since`/`--limit`, 429 → профиль уходит другому аккаунту) переносим в threads-parser как отдельный пакет `scripts/ig/`.
2. Всё хранение переезжает в Postgres в **новую схему `instagram`** (профили, посты, комментарии, медиа, аккаунты, запуски). Файлы остаются только для медиа.
3. Профили берём **из файла** или **из `threads.instagram_links`**; в `instagram.profiles` фиксируем, откуда взят каждый.
4. Четыре уровня работы, всё через флаги: занос профилей → только метаданные профиля → только посты → посты + комментарии (глубина) + картинки + видео.
5. Выгрузка и правки БД — по образцу `export_queries.sql` / `db_tasks.sql`, но на своей схеме и со своими файлами.

---

## 1. Что есть сейчас

### insta-parser (источник переноса)
- 4 файла: `config.py`, `accounts.py` (569 стр.), `functions.py` (938 стр.), `run.py`. Playwright async, `uv`.
- Данные в файлах: `data/output/<ник>/{profile.json, posts.json, captions.txt, skipped.json, anchor.json, media/}`; реестр `data/accounts.json`; куки `data/cookies/<имя>.json`.
- Сетка: `Dispatcher` (общая очередь профилей) → `account_worker` (по одному на аккаунт) → `scrape_profile`: вкладка-«сетка» листает профиль (`iter_post_links`) и кладёт ссылки в очередь, `m` вкладок параллельно открывают **разные посты одного профиля**.
- Данные поста берутся из JSON, который страница сама отдаёт (`find_media` по `shortcode`) + XHR-ответы, перехваченные вкладкой. Комментарии — через `fetch('/api/v1/media/{id}/comments/')` из контекста страницы с `X-IG-App-ID` и `csrftoken` (`JS_FETCH`), **только верхний уровень**, курсор `min_id`.
- Медиа: `ctx.request.get` с запасным путём через вкладку; видео и рилсы по умолчанию отсеиваются (`SkipPost` → `skipped.json`).
- Метаданные профиля сейчас — только `og:title/og:description/og:image` (бедно).
- Устойчивость: `RateLimited`/`AuthProblem` останавливают аккаунт, профиль возвращается в очередь (не больше `n` раз), 5×m ошибок подряд останавливают профиль, якорь `backfilled`, закреплённые `PINNED_SLOTS=3` не считаются в серии известных.

### threads-parser (куда переносим)
- Слой хранения: Postgres, схема `threads`, `asyncpg`-пул, миграции `db/migrations/*.sql` (последняя 019), `ProfileStore` поверх пула, `runs/run_accounts/profile_runs`, `processed_mode` (`profile < threads < full`), режимы `--profile-only / --threads-only / --fill-threads`, `--source`, `--only-unprocessed`.
- Уже есть связь Threads → Instagram: `threads.instagram_links(profile_id, instagram_username, source, confidence confirmed|guessed, matched_raw, ...)`, отчёт `export.py instagram`, CLI `instagram.py`.
- Выгрузка `export.py` (read-only, `:since`, `:limit`, xlsx), правки `sqlrun.py` (`--apply`, откат по умолчанию, UPDATE/DELETE без WHERE отклоняются).
- Стандарт: модули ≤ ~400 строк, докстринг в одну строку, `ruff`, `.env` и `config.py` в `scripts/`, проверки `tests/*_check.py` без БД и сети.

### Принципиальное отличие n×m
В threads `m` = воркеры, каждый ведёт **свой профиль**. В insta-parser `m` = вкладки, делящие **посты одного профиля**. Вы просили «ту же логику n×m», поэтому для режимов с лентой и постами оставляем семантику insta-parser (вкладки делят профиль), а для режима «только метаданные» вкладки берут разные профили (см. п. 7).

---

## 2. Принятые решения (допущения; правьте, если не так)

| # | Решение |
|---|---|
| D1 | Пакет называется **`scripts/ig/`**, а не `instagram/`: в `scripts/` уже лежат `instagram.py` (CLI связей) и `core/instagram.py`; пакет с тем же именем затенил бы модуль и сломал `cli.py instagram`. |
| D2 | Точка входа: **`uv run scripts/cli.py ig <подкоманда>`** (одна строка в `COMMANDS`). Подкоманды: `profiles`, `parse`, `accounts`, `db`, `export`, `sql`. Отдельные файлы `ig_*.py` в корень `scripts/` не добавляем. |
| D3 | Схема БД **`instagram`**, свои таблицы `accounts`, `runs`, `run_accounts`, `profile_runs`, `schema_migrations` — ничего общего с `threads.*` по записи. Читаем из `threads.*` только при импорте профилей. |
| D4 | Связь с threads **мягкая**: `profiles.threads_profile_id BIGINT` без FK (схемы независимы, удаление в threads не ломает instagram). |
| D5 | Аккаунты Instagram — **отдельные** от аккаунтов Threads: своя таблица, свои куки в `data/ig_cookies/` (имена `account_001` совпадают с threads, общая папка привела бы к перезаписи). |
| D6 | Настройки в том же `scripts/.env`, префикс `IG_`; в `config.py` отдельный датакласс `IgSettings` (чтобы не раздувать `Settings`). Общие `DATABASE_URL` и `PROXY`. |
| D7 | Медиа хранится вне репозитория: `IG_STORAGE_DIR=../storage/instagram-parcer` (относительно корня проекта; написание «parcer» взято из вашего ТЗ, подтвердите). В БД путь **относительный** к этому каталогу. |
| D8 | Ключ поста — `code` (как в threads). Остальные поля сохраняют родные для Instagram имена (`caption`, `comments_count`), а не `text`/`reply_count`; единообразие с threads при необходимости делается алиасами в выгрузках. |
| D9 | Глубина комментариев в Instagram **ограничена двумя уровнями** (комментарий → ответы). `--depth 1` = только верхний уровень, `--depth 2` = + ответы, значения > 2 и `-1` приравниваются к 2 с предупреждением. |

---

## 3. Структура кода

```
scripts/
├── cli.py                 # + "ig": ("ig.cli", "Instagram: profiles, parse, accounts, db, export, sql")
├── config.py              # + IgSettings, load_ig_settings(), константы IG_URL, IG_APP_ID
├── .env.example           # + блок IG_* (см. п. 6)
└── ig/
    ├── cli.py             # диспетчер подкоманд ig (по образцу scripts/cli.py)
    ├── consts.py          # IG, IG_APP_ID, POST_RE, MEDIA_KEYS, JS_FETCH, PINNED_SLOTS, USERNAME_RE, RESERVED_PATHS
    ├── extract.py         # чистый разбор JSON без браузера: find_media, media_urls, caption_of, post_kind, shortcode_to_id, разбор профиля
    ├── profiles_cli.py    # ig profiles add | list | stats
    ├── parse_cli.py       # ig parse: аргументы и проверки совместимости флагов
    ├── db/
    │   ├── migrations/001_init.sql …
    │   ├── accounts.py    # таблица accounts (+cooldown_until)
    │   ├── profiles.py    # add_profiles, import из файла/threads, list_targets, drop_processed, mark_processed
    │   ├── sources.py     # чтение threads.instagram_links и фильтры (только на чтение)
    │   ├── posts.py       # save_post_light, save_post_full, known_codes, pending_posts, anchor
    │   ├── comments.py    # save_comments (дерево 2 уровня), recheck
    │   ├── media.py       # записи media, пути относительно IG_STORAGE_DIR
    │   ├── runs.py        # runs / run_accounts / profile_runs
    │   ├── store.py       # IgStore: пул + методы для воркеров (аналог ProfileStore)
    │   └── legacy.py      # разовый импорт старого data/output/<ник>/ и accounts.json
    ├── browser/
    │   ├── session.py     # check_session, find_username (через JS_FETCH), has_session
    │   └── account.py     # Account: браузер, куки, прокси (cookie domain .instagram.com)
    ├── registry/
    │   ├── store.py       # реестр в БД, sync файлов кук, pick_accounts (с учётом cooldown)
    │   ├── login.py       # ручной вход (окно), как в insta-parser
    │   └── commands.py    # add, list, relogin, check, proxy, export, import, import-legacy
    └── scrape/
        ├── entry.py       # открыть профиль: 404/429/логин/private, мета-блок
        ├── meta.py        # --profile-only
        ├── grid.py        # iter_post_links с заглядыванием вперёд, закреплённые
        ├── posts_light.py # --posts-only: лента → posts без открытия поста
        ├── post.py        # открыть пост, данные поста (по флагам: комментарии, медиа)
        ├── comments.py    # верхний уровень + ответы, курсоры, лимиты
        ├── media.py       # скачивание: картинки/видео, пути, атомарная запись, повтор
        ├── anchor.py      # якорь «только новое» (как в insta-parser, но в БД)
        ├── fill.py        # --fill: добор постов, собранных --posts-only
        ├── dispatch.py    # Dispatcher, account_main, tab-воркеры, run()
        └── report.py      # итоги запуска, summary.json, запись profile_runs
```

Зависимости «только вниз», как в README threads: `core ← db ← registry/browser ← scrape ← CLI`. `ig` берёт из общего кода только то, что не знает про Threads (см. п. 12).

---

## 4. БД: схема `instagram`

Миграции: `scripts/ig/db/migrations/001_init.sql`, `002_…` и т. д.; учёт в `instagram.schema_migrations`; запуск `cli.py ig db migrate|status`. Эскиз таблиц (имена и ключевые поля; точные типы фиксируются при реализации):

**profiles** — главный справочник и «откуда взяли»
| Поле | Смысл |
|---|---|
| `id` BIGSERIAL PK, `username` TEXT NOT NULL, уникальный индекс по `lower(username)` | ник (нормализованный, без `@` и ссылки) |
| `ig_user_id` TEXT UNIQUE NULL | числовой pk, заполняется при первом открытии; по нему ловим переименование |
| **`source`** TEXT NOT NULL CHECK (`file`,`threads`,`cli`) | **откуда взяли впервые**; не перезаписывается |
| `sources` TEXT[] | все источники, где профиль встретился (`{file,threads}`) |
| `source_ref` TEXT | имя файла либо `threads.instagram_links#<id>` |
| `threads_profile_id` BIGINT, `threads_username` TEXT | для источника threads |
| `link_confidence` (`confirmed`/`guessed`), `link_source` | копия из `instagram_links`: насколько надёжна связка |
| `status` CHECK (`new`,`ok`,`private`,`not_found`,`error`) | результат последнего открытия |
| `is_target` BOOL DEFAULT true | в парсинге ли профиль (как в threads) |
| `processed_mode` CHECK (`profile`,`posts`,`full`) | чем профиль обработан (для `--only-unprocessed`) |
| `full_name`, `biography`, `bio_links` JSONB, `external_url`, `category` | мета |
| `followers_count`, `following_count`, `posts_count` | счётчики |
| `is_private`, `is_verified`, `is_business` | флаги |
| `profile_pic_url`, `avatar_file` | аватар (файл — по флагу) |
| `meta_raw` JSONB | сырой ответ, из которого взяты поля (на случай смены формата Instagram) |
| `added_at`, `first_seen_at`, `meta_fetched_at`, `last_scraped_at` | даты |

**posts**
| Поле | Смысл |
|---|---|
| `code` TEXT PK, `media_id` TEXT, `profile_id` FK, `url` | shortcode и ссылка |
| `kind` CHECK (`photo`,`carousel`,`video`,`reel`) | тип; по нему `--videos` решает, что качать |
| `caption`, `taken_at`, `likes` (NULL, если скрыты), `comments_count`, `views` | основное |
| `hashtags[]`, `mentions[]`, `tagged_users[]`, `location_name`, `location` JSONB | разбор подписи и меток |
| `is_pinned`, `comments_disabled`, `carousel_size` | служебное |
| `media_status` CHECK (`pending`,`done`,`partial`,`skipped`,`failed`), `media_failed` INT | состояние файлов |
| `comments_status` CHECK (`none`,`partial`,`done`), `comments_depth`, `comments_collected` | состояние комментариев |
| `first_seen_at`, `updated_at`, `last_refreshed_at` | `last_refreshed_at IS NULL` = собран лёгким режимом (`--posts-only`), его добирает `--fill` |

**comments**: `id` TEXT PK (pk комментария), `post_code` FK CASCADE, `parent_id` (NULL = верхний уровень), `depth` CHECK 1..2, `author_username`, `author_id`, `text`, `created_at`, `likes`, `reply_count`, `collected_at`; индексы `(post_code, depth)`, `(parent_id)`.

**media**: `id`, `post_code` FK CASCADE, `position`, `kind` (`image`/`video`), `file_path` UNIQUE (относительно `IG_STORAGE_DIR`), `bytes`, `width`, `height`, `duration_sec`. Исходные URL **не храним**: они протухают.

**profile_anchors**: как в threads (`newest_code`, `newest_date`, `covered_from`, `known_posts`).

**accounts**: как в `threads.accounts` + `cooldown_until TIMESTAMPTZ` (пауза после 429).
**runs / run_accounts / profile_runs**: как в threads; `runs.kind` = `profiles`/`meta`/`posts`/`fill`; `profile_runs.mode` = `profile|posts|full|fill`; счётчики `posts_new/refreshed/unchanged/skipped_old`, `comments_new`, `media_new_files/images/videos/bytes/failed`, `pages_opened`.

Принципы записи:
- каждый пост пишется upsert-ом сразу после обработки (вкладки работают параллельно, пул `asyncpg`) — вместо перезаписи `posts.json` целиком;
- файл на диск → затем строка в `media` (нет файла — нет строки; сбой считается в `media_failed`);
- повторный запуск идемпотентен: `ON CONFLICT` по `code`, `comments.id`, `media.file_path`.

---

## 5. Профили: источники и занос в БД (п. 3.1)

Команда `cli.py ig profiles add` — **только занос**, браузер не открывается.

| Вариант | Команда | Что делает |
|---|---|---|
| Из файла | `ig profiles add --file data/ig_profiles.txt` | формат как у insta-parser: ник, `@ник`, ссылка; пустые и `#` игнорируются. `source='file'`, `source_ref=<имя файла>` |
| Из командной строки | `ig profiles add ник1 ник2` | `source='cli'` |
| Из threads | `ig profiles add --from-threads` | читает `threads.instagram_links` JOIN `threads.profiles`. `source='threads'`, `threads_profile_id`, `link_confidence`, `link_source` |

Фильтры для `--from-threads` (все необязательные, комбинируются через AND):
`--threads-confidence confirmed|guessed|all` (по умолчанию **confirmed**: «догадка по нику» даёт много несуществующих аккаунтов), `--threads-target-only` (только `threads.profiles.is_target`), `--threads-location Казахстан` (по `about_location`), `--threads-min-followers N`, `--threads-category ID`, `--limit N`, `--dry-run` (только счётчики).

Правила:
- Нормализация: ссылка → ник, `@` и регистр снимаются, проверка по `USERNAME_RE` (`[A-Za-z0-9._]{1,30}`), служебные пути (`p`, `reel`, `explore`, `stories`, …) отбрасываются (используем уже существующий `RESERVED_PATHS`).
- Дубли: один ник — одна строка (`lower(username)`). Если профиль уже есть, `source` не меняется, новый источник **добавляется в `sources`**, а `link_confidence` обновляется только вверх (`guessed` → `confirmed`, не наоборот).
- Несколько профилей threads с одним Instagram: одна строка в `instagram.profiles`, `threads_profile_id` — первый, остальные видны через `threads.instagram_links`.
- Итог занесения печатается: добавлено / уже было / отброшено (с причинами) / по источникам.
- `ig profiles list [--source …] [--status …] [--limit …]` и `ig profiles stats` (по источникам, статусам, `processed_mode`, confirmed/guessed).

При запуске `ig parse` профили берутся только из `instagram.profiles`; `--file` — удобство (занести и сразу парсить, `source='file'`).

---

## 6. Режимы и флаги (всё управляется флагами)

Запуск: `cli.py ig parse [ники…] [флаги]`. Каждый флаг имеет двойник в `.env` (`IG_…`), флаг в командной строке сильнее. Переключатели имеют обратную форму (`--no-comments`, `--no-videos`, …).

### Какие профили
| Флаг | Назначение |
|---|---|
| `ник…` | профили из командной строки (добавляются в БД, если их нет) |
| `--file F` | занести из файла (`source=file`) и парсить; без аргументов и `--source` берётся `IG_PROFILES_FILE` |
| `--source all\|new\|ник,ник` | из `instagram.profiles` с `is_target`; `new` = профили без постов |
| `--only-unprocessed` | пропустить уже обработанные в этом или более глубоком режиме (`processed_mode`), прерванные берутся снова |
| `--dry-run` | показать список профилей и режим, без браузера |

### Режим (взаимоисключающие)
| Флаг | Что делает | Пишет |
|---|---|---|
| `--profile-only` | **п. 3.2**: открыть профиль, снять только метаданные | `profiles.*`, `processed_mode='profile'` |
| `--posts-only` | **п. 3.3**: пройти ленту, сохранить посты **без комментариев и без медиа** (текст, дата, тип, счётчики, число слайдов; `media_status='pending'`) | `posts` (`last_refreshed_at IS NULL`), `processed_mode='posts'` |
| *(по умолчанию)* полный проход | **п. 3.4**: посты + то, что включено флагами содержимого | всё, `processed_mode='full'` |
| `--fill` | добрать посты, собранные `--posts-only`: открыть, снять комментарии/медиа по флагам, обход ленты не делается (аналог `--fill-threads`) | как полный |

### Содержимое (для полного прохода и `--fill`; в `--profile-only`/`--posts-only` игнорируются с предупреждением, как `THREADS_ONLY_IGNORED`)
| Флаг | По умолчанию | Назначение |
|---|---|---|
| `--comments/--no-comments` | выкл. | собирать комментарии |
| `--depth N` | 2 | 1 = верхний уровень, 2 = + ответы (потолок, см. D9) |
| `--max-comments N` | 0 (все) | лимит комментариев на пост (ответы входят в счёт) |
| `--images/--no-images` | **вкл.** | скачивать фото и кадры каруселей |
| `--videos/--no-videos` | выкл. | скачивать видео и рилсы (в каруселях — видео-слайды) |
| `--avatars` | выкл. | скачивать аватар профиля |
| `--recheck-days N` | 7 | посты младше N дней при каждом запуске перепроверяются на новые комментарии; 0 = выкл. |
| `--full-rescan` | выкл. | не останавливаться на знакомом комментарии |

Если видео не качаем, пост всё равно **сохраняется** (метаданные, `kind='reel'`, `media_status='skipped'`), а не выбрасывается, как сейчас в `skipped.json`. Позже его можно добрать `--fill --videos`.

### Окно и остановка
`--since 30d|4w|1m|1y|дата` · `--limit N` (постов на профиль, 0 = все) · `--full` (игнорировать якорь, пройти окно целиком) · `--stop-after N` (серия известных постов, по умолчанию 4; закреплённые `PINNED_SLOTS=3` не считаются) · `--min-posts N` (добрать до N постов, по образцу `--min-threads`).

### Сетка и поведение
`--accounts a,b` (по умолчанию все активные IG-аккаунты) · `--tabs M` (вкладок на аккаунт) · `--headless/--no-headless` · `--delay S` · `--profile-delay S` · `--debug` · `--output DIR` (summary.json).

### Совместимость
- `--profile-only`, `--posts-only`, `--fill` несовместимы друг с другом.
- `--min-posts` только с `--posts-only`.
- Глубокий режим покрывает мелкий: `--only-unprocessed` в режиме `profile` пропускает и тех, у кого уже `posts`/`full`.
- `processed_mode` ставится сразу по окончании каждого профиля (переживает Ctrl+C).
- Обработанным считается `ok`, `private`, `not_found`; `429`, ошибка браузера/БД — нет.

### Новые переменные `.env`
`IG_ACCOUNTS`, `IG_TABS_PER_ACCOUNT=3`, `IG_HEADLESS`, `IG_LIMIT`, `IG_SINCE`, `IG_COMMENTS`, `IG_DEPTH=2`, `IG_MAX_COMMENTS`, `IG_IMAGES=true`, `IG_VIDEOS=false`, `IG_AVATARS=false`, `IG_RECHECK_DAYS=7`, `IG_STOP_AFTER_KNOWN=4`, `IG_DELAY=4`, `IG_PROFILE_DELAY=8`, `IG_429_PAUSE=300`, `IG_PROFILES_FILE=data/ig_profiles.txt`, `IG_COOKIES_DIR=data/ig_cookies`, `IG_STORAGE_DIR=../storage/instagram-parcer`, `IG_OUTPUT_DIR=data/ig_output`, `IG_EXPORT_QUERIES_FILE=data/ig_export_queries.sql`, `IG_DB_TASKS_FILE=data/ig_db_tasks.sql`, `IG_ACCOUNTS_FILE=data/ig_accounts.json` (только для разового импорта), `IG_MIN_FREE_GB=5`.

---

## 7. Сетка n × m

- **n** — аккаунты (`--accounts`), у каждого свой браузер, куки и (опционально) прокси. **m** — вкладки (`--tabs`).
- **Режимы с лентой/постами** (`--posts-only`, полный, `--fill`): как в insta-parser. Профиль достаётся свободному аккаунту; вкладка-«сетка» листает ленту и кладёт коды в очередь, `m` вкладок параллельно обрабатывают посты **этого** профиля. Одновременно открыто до `n × m` постов, в работе `n` профилей. Сохраняются: резервирование места под `--limit`, `cutoff_index` по `--since`, серия известных, остановка при `5 × m` ошибок подряд.
- **`--profile-only`**: на профиль нужен один запрос, поэтому вкладки здесь берут **разные профили** из общей очереди (`n × m` профилей одновременно), лента не листается.
- **429 / checkpoint**: аккаунт останавливается, профиль возвращается в очередь и достаётся другому (не больше `n` попыток). Улучшение: на 429 аккаунт получает `accounts.cooldown_until = now + IG_429_PAUSE`, до окончания паузы `pick_accounts` его пропускает (вместо вечной остановки до следующего запуска). `needs_login` ставится при потере сессии.
- Куки каждого аккаунта обновляются один раз при закрытии браузера (Instagram их ротирует).
- Запись в БД идёт через общий пул, соединение берётся на одну запись; размер пула `n × m + 2`.

---

## 8. Логика парсинга по уровням

### 8.1 Метаданные профиля (`--profile-only`, п. 3.2)
Цель — то, чего нет в insta-parser (там только `og:*`): `full_name`, `biography`, ссылки в био, `external_url`, `category`, подписчики/подписки/число публикаций, `is_private/is_verified/is_business`, `ig_user_id`, аватар.
Предлагаемый источник: запрос профиля через `JS_FETCH` из контекста страницы (эндпоинт веб-профиля) с запасным путём — embedded JSON страницы и `og:*`. Сырое сохраняется в `meta_raw`. **Эндпоинты и поля нужно подтвердить на живой сессии** (см. п. 15, шаг 0): в архивах есть только `/api/v1/accounts/current_user/`, `/api/v1/users/{id}/info/` и комментарии.
Статусы: 404 → `not_found`; закрытый профиль → `private` (мета сохраняется, посты невозможны, профиль считается обработанным); смена ника (`ig_user_id` тот же, ник другой) → ник обновляется.

### 8.2 Посты без комментариев (`--posts-only`, п. 3.3)
Лента листается как в `iter_post_links`; для каждого кода сохраняется то, что видно в ленте/ответах без открытия поста. Если без открытия поста часть полей недоступна (`likes`, `taken_at`, `caption`), они остаются NULL и добираются `--fill`. Якорь, `--since`, `--limit`, `--stop-after` работают. Параметры закреплённых постов и серия известных переносятся 1:1.

### 8.3 Полный проход (п. 3.4)
Пост открывается (как `scrape_post`): данные из JSON страницы и перехваченных XHR → `posts`. Затем по флагам:
1. **Комментарии**: верхний уровень через `/api/v1/media/{id}/comments/` (курсор `min_id`, как сейчас), при `--depth 2` — ответы к комментариям с `child_comment_count > 0` (отдельный запрос по комментарию, **эндпоинт подтвердить**). Паузы 1–2.5 с, лимит `--max-comments`, при 429 в середине обхода частичный результат сохраняется (`comments_status='partial'`), аккаунт уходит на паузу.
2. **Картинки** (`--images`): фото и кадры каруселей.
3. **Видео** (`--videos`): `video_versions[0]` для видео-постов, рилсов и видео-слайдов.
Перепроверка: посты младше `--recheck-days` обходятся заново, старше — только если вырос `comments_count`.

---

## 9. Хранилище медиа (п. 3.6)

```
<корень проекта>/../storage/instagram-parcer/
└── <ник_в_нижнем_регистре>/
    ├── avatar.jpg                          # --avatars
    ├── 20260101_<code>_1.jpg               # фото и кадры каруселей
    └── 20260101_<code>_2.mp4               # видео
```
- `IG_STORAGE_DIR` задаётся в `.env`, относительные пути считаются от корня проекта; каталог создаётся сам.
- `media.file_path` — путь относительно `IG_STORAGE_DIR` (`<ник>/20260101_<code>_1.jpg`), поэтому хранилище можно переносить без правки БД.
- Запись атомарная: `*.part` → `rename`; существующий непустой файл повторно не качается (повторный запуск безопасен).
- Расширение определяется по URL/типу содержимого, как в `download()`.
- Защита: проверка свободного места (`IG_MIN_FREE_GB`) перед запуском и каждые N файлов; безопасное имя каталога (ник проходит `USERNAME_RE`, никаких `..`).
- Видео крупные: по `--videos` ограничитель `--max-video-mb` (нужен ли — в вопросах).

---

## 10. Аккаунты Instagram

`cli.py ig accounts …` — те же команды, что в insta-parser/threads: `add N`, `list`, `relogin`, `check [--headless]`, `proxy NAME [URL]`, `export`, `import`, плюс **`import-legacy`** (разовый перенос `data/accounts.json` и кук из insta-parser; реестр и счётчики запусков не переносятся, как решено для threads).
Вход только с окном, парсер браузером не логинится. Требуются куки `sessionid`, `ds_user_id`, `csrftoken`; User-Agent хранится вместе с куками (как сейчас). Статусы `active / needs_login / disabled`.

---

## 11. Выгрузка и изменение БД (п. 3.7)

**Выгрузка**: `cli.py ig export <имя…> [--since] [--limit] [--out]`, формат файла `data/ig_export_queries.sql` тот же (`-- name:`, `-- desc:`, `:since`, `:limit`), read-only транзакция, xlsx по листу на запрос.
Базовые запросы (в коде, по образцу `exporter/queries.py`):
`ig-profiles` (профиль, источник, подписчики, статус, процент обработки) · `ig-posts` · `ig-comments` · `ig-media` · `ig-sources` (сколько профилей из файла/threads, confirmed/guessed) · `ig-coverage` (профили: сколько постов собрано против `posts_count`, сколько постов без медиа/комментариев).
Примеры «своих» запросов кладём в `data/ig_export_queries.sql` (топ профилей по подписчикам, посты за период, посты без файлов).

**Изменения БД**: `cli.py ig sql <имя…> [--set имя=значение] [--apply]`, файл `data/ig_db_tasks.sql`, поведение как у `sqlrun.py`: пробный прогон с откатом, `--apply` записывает, UPDATE/DELETE без WHERE отклоняются.
Заготовки: `targets-on`/`targets-off` (включить/выключить профили), `set-target-by-followers` (`min_followers`), `reset-unprocessed` (сбросить `processed_mode`), `retry-failed-media` (вернуть `media_status='pending'`), `unprivate` (перепроверить закрытые профили), `drop-guessed-not-found` (убрать `guessed`-профили со статусом `not_found`), `drop-profile` (`username` → каскадное удаление), `targets-stats`.

Для обеих команд схема (`instagram`), файл запросов и набор базовых запросов — параметры, общий движок с threads (см. п. 12), поведение threads не меняется.

---

## 12. Изменения в существующем коде threads (минимальные, без изменения поведения)

| Файл | Что меняется |
|---|---|
| `db/pool.py` | `SCHEMA` перестаёт быть жёсткой: `create_pool/connect/connection/run_sync(…, schema="threads")`; `search_path` и `application_name` от схемы |
| `db/migrate.py` | `migrate/status(conn, schema, migrations_dir, lock_id)`; для threads значения по умолчанию прежние |
| `exporter/*`, `export.py` | движок выгрузки принимает схему, файл запросов и словарь базовых запросов; `export.py` вызывает его со своими, `ig export` — со своими |
| `sqlrun.py` | общие функции (`bind`, `check_safe`, `run_task`, `parse_sets`) выносятся в модуль (например `db/tasks.py`), `sqlrun.py` остаётся тонким CLI |
| `browser/launch.py` | `account_proxy` получает источник аккаунтов параметром (сейчас читает `threads.accounts`); остальное (`launch_browser`, `new_context`, `proxy_settings`) переиспользуется |
| `registry/cookies.py` | переиспользуется целиком (`cookie_jar`, `normalize_cookie`); домен кук передаётся параметром |
| `core/*` | переиспользуются `logs`, `errors` (`RateLimited`, `AuthProblem`, `HttpStatusError`, `OldPost`), `timeutil.parse_since`, `jsonwalk.walk`; `SkipPost` для IG не нужен (пост сохраняется со `skipped`) |
| `cli.py` | строка `ig` в `COMMANDS` |
| `config.py`, `.env.example` | `IgSettings` и блок `IG_*` |
| `ruff.toml` | `ig` в `known-first-party` |
| `.gitignore` | `data/ig_cookies/*.json`, `data/ig_output/`, `data/ig_accounts.json`, `data/export/` |
| `README.md` | раздел «Instagram (`ig`)»: таблицы модулей, флаги, схема БД; этот файл — в `docs/TZ_instagram.md` |

---

## 13. Мои предложения (сверх вашего списка)

Приоритет: **М** — стоит делать сразу, **С** — желательно, **Н** — по желанию.

| # | Приоритет | Предложение | Зачем |
|---|---|---|---|
| 1 | М | `--fill` (добор постов, собранных `--posts-only`, как `--fill-threads`) | позволяет сначала быстро собрать ленты, потом дозагружать комментарии и медиа выборочно |
| 2 | М | Пост с видео при `--no-videos` не выбрасывается, а сохраняется со `skipped` | не теряем метаданные рилсов; видео можно добрать позже |
| 3 | М | `--dry-run` у `ig parse` и `ig profiles add` | видно объём и список до запуска |
| 4 | М | `cooldown_until` у аккаунта вместо вечной остановки после 429 | меньше ручных перезапусков, аккаунт не жжём повторами |
| 5 | М | Дедупликация и контроль файлов: атомарная запись, пропуск существующих, проверка места | устойчивость при больших объёмах видео |
| 6 | М | Отчёт о запуске: `profile_runs` + `summary.json` + `ig db stats` (прогресс, скорость, 429) — аналог `progress.sql` | видно, сколько осталось и где узкое место |
| 7 | М | Фильтр `confirmed` по умолчанию при импорте из threads | `guessed` — это «тот же ник», много несуществующих и чужих аккаунтов |
| 8 | С | Разовый импорт старых данных insta-parser (`ig db import-legacy`): `posts.json`, `profile.json`, `anchor.json`, перенос/копирование медиа в `IG_STORAGE_DIR` | не терять уже собранное (в архиве есть `data/output`) |
| 9 | С | `--min-posts N` (добрать до N постов, как `--min-threads`) | единый критерий готовности профиля |
| 10 | С | История ников профиля (`profile_usernames`) при переименовании | ссылки на старые ники не теряются |
| 11 | С | Автопроверка `guessed`-связок: после `--profile-only` сверить `biography/external_url` с ником Threads и пометить `link_verified` | переводит догадки в подтверждённые |
| 12 | С | `ig recon <ник>` — сохранить в `output/` структуру ответов Instagram (как `--recon` у поиска threads) | нужно для шага 0 и на случай смены формата |
| 13 | С | `--avatars` и сохранение обложки видео, если качаем картинки, а видео нет | полезно для просмотра без тяжёлых файлов |
| 14 | Н | Ограничение размера видео `--max-video-mb`, ограничение длительности | экономия места |
| 15 | Н | Бюджет аккаунта на запуск (`--max-posts-per-account`) | снижает риск блокировки |
| 16 | Н | Отчёт `ig-report` (как `build_report.sql`): сводный xlsx из нескольких листов | единый файл для разбора |
| 17 | Н | Контрольная сумма файлов (`sha256`) в `media` | поиск дублей между постами |

---

## 14. Не входит в эту задачу
Stories, highlights, посты с меткой «отмечен» (tagged), reels-вкладка как отдельный источник, лайкеры, подписчики/подписки (списки), поиск по хэштегам и гео, автоматический вход без участия человека (капча, 2FA), классификация профилей через LLM/Jev для Instagram.

---

## 15. Этапы (после каждого — остановка на проверку, результат патчем `git apply`, файлы проекта в CRLF)

| Этап | Содержание | Результат/проверка |
|---|---|---|
| 0 | **Разведка форматов** (нужен ваш живой сеанс): `ig recon` либо ручной прогон — эндпоинты профиля, ленты, ответов на комментарии, URL видео | список подтверждённых эндпоинтов, образцы JSON в `output/` |
| 1 | Параметризация `pool`/`migrate`; схема `instagram`, миграция 001; `IgSettings`; `cli.py ig db migrate/status` | `ig db status` показывает таблицы |
| 2 | Аккаунты IG: реестр в БД, `add/list/relogin/check/proxy/export/import/import-legacy` | `ig accounts check` на ваших куках |
| 3 | Профили: `ig profiles add/list/stats`, источники file/threads/cli, фильтры threads | занесены профили из файла и из threads |
| 4 | Каркас запуска: `Dispatcher`, `account_main`, `IgStore`, `runs`; режим `--profile-only`, `--source`, `--only-unprocessed`, `--dry-run` | мета профилей в БД |
| 5 | `--posts-only`: лента, якорь, `--since/--limit/--stop-after/--min-posts` | лёгкие посты в БД |
| 6 | Полный проход и `--fill`: комментарии (`--depth`, `--max-comments`, recheck), `--images`, `--videos`, `--avatars`, хранилище | комментарии и медиа в `storage/instagram-parcer/` |
| 7 | Выгрузка и правки: параметризация `export`/`sqlrun`, `ig export`, `ig sql`, файлы `ig_export_queries.sql`, `ig_db_tasks.sql` | xlsx и пробный `--apply` |
| 8 | `import-legacy` старых данных, README, `ruff`, аудит лишнего кода | чистый репозиторий |

---

## 16. Критерии приёмки и проверки

- `ig profiles add --file …` и `--from-threads` дают ожидаемые счётчики; повторный запуск ничего не дублирует, `source` у существующих не меняется, `sources` пополняется.
- `--profile-only` не открывает ленту и не пишет `posts`; `processed_mode='profile'`.
- `--posts-only` не пишет `comments` и `media`; `last_refreshed_at IS NULL`; `--fill` их добирает.
- Полный проход с `--comments --depth 2 --images --videos`: комментарии двух уровней, файлы в `storage/instagram-parcer/<ник>/`, `media.file_path` относительные, `media_failed` считается.
- `--no-videos`: видео-пост сохранён со `skipped`, файла нет; `--fill --videos` его докачивает.
- Повторный запуск после Ctrl+C продолжает с места остановки, без повторной загрузки файлов и дублей строк.
- 429 у одного аккаунта: профиль уходит другому, у первого `cooldown_until`.
- `ig export` и `ig sql` работают на схеме `instagram`; `export.py`/`sqlrun.py` для threads ведут себя как прежде.
- Автопроверки в `tests/ig_*_check.py` без БД и сети (как остальные `*_check.py`): нормализация ников, разбор `find_media/media_urls`, пути хранилища, совместимость флагов, `parse_since`, глубина комментариев, сборка дерева.

---

## 17. Риски

- **Живой Instagram проверить не из чего**: эндпоинты профиля/ответов и формат ленты подтверждаются на этапе 0; Instagram меняет их без предупреждения, поэтому сырой JSON профиля храним в `meta_raw`.
- Блокировки аккаунтов: автоматизация нарушает условия Instagram; мера — паузы, малые объёмы, `cooldown`, прокси по аккаунту, не пользоваться аккаунтом вручную во время запуска.
- Объём видео: диск, поэтому `--videos` выключен по умолчанию, есть контроль места.
- **Безопасность**: в `insta-parser.zip` лежат живые куки (`data/cookies/account_001.json`, `instagram_cookies.json`) и `data/accounts.json`. Не коммитить, не пересылать; если архив уходил третьим лицам, сессию лучше пересоздать (`relogin`). Новые пути уже в `.gitignore` (п. 12).
- Две схемы в одной БД: `cli.py db migrate` (threads) и `cli.py ig db migrate` независимы; порядок не важен.

---

## 18. Открытые вопросы (с ответами по умолчанию, если не ответите)

1. Папка хранилища: `../storage/instagram-parcer` («parcer») или `instagram-parser`? *По умолчанию: как в вашем ТЗ, `parcer`, задаётся в `.env`.*
2. `--images` по умолчанию включён, `--videos` выключен — так? *По умолчанию: так.*
3. «Только посты без комментариев» (п. 3.3) — без скачивания медиа тоже? *По умолчанию: да, только данные, медиа добираются полным проходом или `--fill`.*
4. Профиль из threads, найденный как `guessed`, по умолчанию не заносим — устраивает? *По умолчанию: да, включается `--threads-confidence all`.*
5. Нужен ли перенос уже собранных данных insta-parser (`data/output`) — этап 8? *По умолчанию: да.*
6. Ограничение размера видео нужно сразу? *По умолчанию: нет.*
7. Для `--profile-only` вкладки раздаём разным профилям (п. 7) — подходит? *По умолчанию: да.*
