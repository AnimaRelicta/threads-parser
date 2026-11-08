# Threads-парсер (Playwright, uv)

Открывает профили Threads в настоящем браузере и собирает посты (текст, дата, лайки,
фото), по флагу `--comments` ещё и комментарии деревом. Структура и работа с аккаунтами
такие же, как у `insta_parser`. Отдельный скрипт `search.py` собирает посты из поиска Threads.

## Структура кода

Все исполняемые файлы лежат в `scripts/` и запускаются из корня проекта (`uv run scripts/run.py ...`). Три файла верхнего уровня — тонкие CLI, вся логика в пакетах. Один модуль = одна задача, файлы не больше ~400 строк.

```
threads-parser/
├── scripts/
│   ├── .env.example   # шаблон настроек (копия -> scripts/.env, в git не попадает)
│   ├── config.py      # константы Threads, чтение scripts/.env, Settings
│   ├── cli.py         # единая точка входа: parse | search | classify | accounts
│   ├── run.py         # CLI: парсинг профилей
│   ├── search.py      # CLI: поиск по выдаче Threads, --refilter и --stats (БД, без браузера)
│   ├── geo.py         # CLI: страна/город профилей КЗ/УЗ (rescan/stats/list, БД, без браузера)
│   ├── accounts.py    # CLI: аккаунты (add/list/relogin/check/export/import)
│   ├── dbadmin.py     # CLI: Postgres (migrate/status/import-accounts/import-profiles/import-search/merge-old-profiles)
│   ├── export.py      # CLI: выгрузка из БД в xlsx (базовые и свои запросы)
│   ├── sqlrun.py      # CLI: свои запросы на изменение БД (data/db_tasks.sql), отбор is_target
│   ├── classify/      # пакет: категории профилей через LLM (запуск: cli.py classify)
│   ├── core/  db/  browser/  registry/  scrape/  serp/   # пакеты, см. таблицы ниже
├── data/              # списки профилей и запросов, куки, media/captions.txt/summary.json
│                         (посты, комментарии, реестр аккаунтов — в Postgres)
├── output/search/     # сырые данные --recon (результаты поиска лежат в Postgres)
├── ruff.toml          # единый стиль кода
└── pyproject.toml
```

### Единая точка входа

```bash
uv run scripts/cli.py parse olx_vetka_astana --limit 5 --comments   # = run.py
uv run scripts/cli.py search "олх ветка астана" --since 7d          # = search.py
uv run scripts/cli.py geo stats                                     # = geo.py
uv run scripts/cli.py accounts check --headless                     # = accounts.py
uv run scripts/cli.py db migrate                                    # = dbadmin.py
uv run scripts/cli.py parse --help                                  # справка подкоманды
```

`cli.py` только выбирает подкоманду и передаёт ей остальные аргументы без изменений, поэтому
флаги те же, что у `run.py`, `search.py`, `accounts.py`; эти файлы можно запускать и напрямую.
Новая команда добавляется одной строкой в `COMMANDS`.

### Правила зависимостей

Импорты между модулями идут только «вниз», циклов нет:

`core` ← `db` ← `registry.store`, `registry.cookies` ← `browser` ← `registry.login`, `registry.commands` и `scrape` ← `serp` ← CLI (`run.py`, `search.py`, `accounts.py`).

- `core` не импортирует другие пакеты проекта (только `config`).
- `classify` берёт только `core`, `db` и `config`, без браузера; запускается через `cli.py classify` (файл не называется `classify.py`, чтобы не совпасть с именем пакета).
- `serp` берёт из `scrape` и `browser` перехват ответов, прокрутку и воркеры; обратно — нет.
- Модули импортируются как `from scrape.posts import scrape_post`: `scripts/` — корень импортов.

### `core/` — Общее: константы, исключения, лог, время, файлы, обход JSON

| Модуль | Назначение | Основное содержимое |
|---|---|---|
| `consts.py` | Общие константы: регулярки ссылок и имён. | `POST_RE`, `USERNAME_RE` |
| `jsonwalk.py` | Обход вложенных JSON-структур Threads. | `walk`, `walk_ordered` |
| `errors.py` | Исключения парсера. | `RateLimited`, `AuthProblem`, `SkipPost`, `OldPost`, `ThreadAborted`, `HttpStatusError` |
| `logs.py` | Журнал воркеров: метка в строках лога и счётчики текущего профиля. | `TAG`, `PSTAT`, `pstat`, `log` |
| `timeutil.py` | Время и даты: ISO-строки, разбор --since, сдвиг месяцев. | `iso`, `ts_from_iso`, `parse_dt`, `shift_months`, `parse_since` |
| `textfilter.py` | Фильтр «олх»: версия правил и классификация поста (approved/rejected, что и где сработало). | `FILTER_VERSION`, `FILTER_RE`, `normalize_text`, `classify` |
| `geo.py` | Справочник городов/стран КЗ и УЗ (кириллица, латиница, падежи), `find_locations`; сигналы профиля (about_location, телефон WhatsApp, био, имя, темы), итог с приоритетом и флагом конфликта. | `DETECTOR_VERSION`, `find_locations`, `country_from_phone`, `profile_signals`, `resolve` |
| `fsio.py` | Чтение и запись файлов: JSON, JSONL, списки профилей. | `read_json`, `read_jsonl`, `write_jsonl_atomic`, `read_usernames`, `normalize_username` |

### `db/` — Postgres (схема `threads`)

| Модуль | Назначение | Основное содержимое |
|---|---|---|
| `pool.py` | Подключение: пул asyncpg, разовые запросы из синхронного кода. | `SCHEMA`, `DB_ERRORS`, `require_url`, `create_pool`, `connect`, `connection`, `run_sync` |
| `migrate.py` | Миграции `db/migrations/*.sql` по порядку, учёт в `schema_migrations`. | `list_migrations`, `migrate`, `status` |
| `accounts.py` | Таблица `accounts`: чтение, вход, обновление, итог запуска, импорт старого реестра. | `fetch_accounts`, `register_login`, `update_account`, `mark_run`, `import_legacy` |
| `posts.py` | Посты, комментарии, медиа, якоря и пропущенные посты профиля: запись и чтение в прежнем формате словарей. | `save_post`, `load_profile_posts`, `mark_skipped`, `save_anchor`, `about_fetched`, `upsert_topic` |
| `profile_data.py` | Данные профиля одним вызовом: страница, «Об этом профиле», шапка, `instagram_links` (одна транзакция), затем геосигналы `location_mentions` по строке из `RETURNING`. | `save_profile_data` |
| `profiles.py` | Хранилище профильного пайплайна поверх пула: общий для воркеров запуска. | `ProfileStore`, `ProfileState` |
| `search.py` | Поиск: очередь `search_jobs`, найденные посты, связи `post_hits`, пересчёт фильтра. | `ensure_job`, `start_job`, `finish_job`, `save_hits`, `add_keyword`, `refilter` |
| `geo.py` | Локации (`location_mentions`): запись при сборе профиля и поста, пересчёт `rescan`, выборки для CLI. | `save_geo`, `save_post_mentions`, `rescan`, `load_profile_geo` |
| `legacy.py` | Разовый перенос старых файлов (posts.json профилей, posts.jsonl и jobs.json поиска). | `normalize_post`, `import_profiles`, `import_search` |
| `runs.py` | Запуски: `runs`, `run_accounts`, `profile_runs`. | `start_run`, `finalize_run`, `finish_run`, `save_profile_runs`, `ensure_profile` |
| `stats.py` | Реестр отчётов (SQL + заголовок) и вывод таблицей. | `Report`, `REPORTS`, `run_report`, `format_table` |
| `migrations/019_drop_unused_columns.sql` | Удалены: таблица `topics`, `posts.topic_id`, `posts.geo_version`; в `profiles` бан/спам (`is_banned`, `ban_reason`, `banned_at`, `spam_*`), discover (`discovered_*`, `mentions_count`, `review_status` с триггером), гео-кэш (`about_country`, `geo_version`, `text_location`, `text_location_src`), `user_id`, `title`. Необратимо: перед запуском сделайте дамп. | |
| `migrations/001_init.sql` | Начальная схема (аккаунты, запуски, профили, посты, комментарии, медиа, поиск, темы). | |
| `migrations/005_categories.sql` | Справочник `categories` (7 категорий), колонки категории в `profiles`, (триггер переходов `review_status` убран в 019). | |
| `migrations/013_cleanup.sql` | Уборка схемы: из `search_jobs` убраны `origin`, `parent_job_id`, `topic_id`, `depth` и из `topics` `is_active` (задел под несуществующий `--follow-topics`), удалён индекс-дубль `location_mentions_entity`. | |
| `migrations/004_audit_runs.sql` | Удалена неиспользуемая `custom_topics`; `runs.kind` = `profiles`/`search`/`discover`/`classify`; комментарий к `skipped_posts`. | |

Подготовка: `DATABASE_URL` в `scripts/.env`, затем `uv run scripts/cli.py db migrate`;
состояние: `uv run scripts/cli.py db status`. Перенос идёт поэтапно: в БД уже живут
реестр аккаунтов, записи запусков (`runs`, `run_accounts`, `profile_runs`) и весь профильный
пайплайн (`profiles`, `posts`, `comments`, `media`, `profile_anchors`, `skipped_posts`) и поиск
(`search_jobs`, `posts`, `post_hits`). Файлы в `data/output/<ник>/` теперь только
`media/`, `captions.txt` и итоговый `summary.json`.

Поиск: готовое окно (задача `done`) не перезапускается без `--force`; пост, найденный несколькими
запросами, хранится один раз, а каждая находка записана в `post_hits`; все запросы, по которым найден пост, лежат списком в `posts.found_keywords` (нижний регистр, без повторов, в порядке находки; миграция 018 заполняет его по старым `post_hits`). Статус `approved/rejected`
считается при сохранении (`filter_version` — версия правил); после правки правил в
`core/textfilter.py` нужно поднять `FILTER_VERSION` и выполнить `uv run scripts/search.py --refilter`
(с `--force` пересчитываются все посты). Паузы поиска (`SEARCH_START_DELAY`, `SEARCH_PAGE_WAIT_MS`, `SEARCH_SCROLL_*_MS`, `SEARCH_STALE_SCROLLS`, `SEARCH_429_PAUSE`) настраиваются в `scripts/.env`, описание — в `.env.example`. Данные прежних запусков:
`uv run scripts/dbadmin.py import-search` (`posts.jsonl` и `jobs.json`; находки старых постов
привязываются к служебной задаче `legacy|запрос|режим`, потому что окно дат в файле не хранилось).

Аккаунты из старого `data/accounts.json` переносятся один раз:
`uv run scripts/dbadmin.py import-accounts` (только записи аккаунтов: имя, ник, кука,
статус, прокси). **История запусков из `accounts.json` (поле `runs[]` и счётчики) намеренно
не переносится** — это разовое решение, не техническое ограничение: `runs`/`run_accounts`/
`profile_runs` в БД начинают счёт заново с момента перехода на Postgres, старые цифры
остаются только в самом JSON-файле (его можно сохранить как архив или удалить). Данные
профилей из `data/output/<ник>/`: `uv run scripts/dbadmin.py
import-profiles [ник ...]` (посты, комментарии, пропущенные, якорь; уже имеющиеся в БД не перезаписываются).
Прокси аккаунта: `uv run scripts/accounts.py proxy NAME URL`.
Новый отчёт для `accounts.py list` и других команд добавляется записью в `db/stats.py`
(одна `register(Report(...))`, без правок в командах). Отчёты поиска: `uv run scripts/search.py
--stats` печатает таблицами общую картину (approved/rejected, авторы, средние лайки),
качество каждого запроса (доля approved среди найденного им в `post_hits`), темы
approved-постов и состояние очереди `search_jobs`. CSV не формируются.

### `browser/` — Браузер и аккаунт-воркер

| Модуль | Назначение | Основное содержимое |
|---|---|---|
| `net.py` | Сетевое состояние одного воркера. | `Net` |
| `launch.py` | Запуск Chromium: прокси и контекст. | `proxy_settings`, `account_proxy`, `launch_browser`, `new_context` |
| `session.py` | Сессия аккаунта: куки, проверка входа, определение имени пользователя. | `load_cookies`, `save_cookies`, `has_session`, `check_session`, `USER_ID_KEYS`, `NAV_PROFILE_JS` … |
| `account.py` | Аккаунт-воркер: браузер, куки, прокси. | `Account` |

### `registry/` — Реестр аккаунтов и команды `accounts.py`

| Модуль | Назначение | Основное содержимое |
|---|---|---|
| `store.py` | Реестр аккаунтов в Postgres (таблица `accounts`) и синхронизация файлов кук с ним; функции асинхронные. | `now_iso`, `load_registry`, `get_account`, `register_login`, `update_account`, `pick_accounts` … |
| `cookies.py` | Куки аккаунтов: разбор и нормализация чужих форматов. | `DEFAULT_USER_AGENT`, `SAMESITE`, `cookie_jar`, `normalize_cookie` |
| `login.py` | Ручной вход в аккаунт в окне браузера. | `confirm`, `dup_question`, `login_account`, `login_into` |
| `commands.py` | Команды accounts.py: add, relogin, check, import, export, list. | `cmd_add`, `cmd_relogin`, `cmd_check`, `import_blob`, `collect_bundle`, `cmd_import` … |

### `scrape/` — Парсинг профилей (`run.py`)

| Модуль | Назначение | Основное содержимое |
|---|---|---|
| `consts.py` | Константы обхода профилей и комментариев. | `OLD_STREAK_STOP`, `PINNED_LOOKAHEAD`, `UNLIMITED_DEPTH`, `SORT_QS`, `NODE_DELAY`, `STALL_LIMIT` … |
| `extract.py` | Разбор JSON-документов Threads: обход, медиа, текст поста. | `media_urls`, `caption_of`, `topic_of`, `is_video_obj`, `read_embedded` |
| `harvest.py` | Накопление постов из ответов сервера: пул, индекс ленты (дата, счётчики, текст веток), выбор дочерних узлов. | `Harvester`, `FeedIndex`, `pick_children` |
| `page.py` | Работа со страницей: ожидание сети, прокрутка, «ещё», открытие поста. | `settle`, `CLICK_MORE_JS`, `SCROLL_JS`, `scroll_down`, `page_infos`, `click_more` … |
| `capture.py` | Перехват JSON-ответов Threads вкладкой. | `make_handler` |
| `children.py` | Подгрузка ответов одного узла дерева комментариев. | `load_children` |
| `media.py` | Скачивание медиа через сессию аккаунта. | `fetch_bytes`, `download`, `save_comment_media` |
| `comments.py` | Сбор комментариев деревом. | `make_comment`, `root_node_from`, `node_from_comment`, `collect_thread`, `finalize_comments` |
| `feed.py` | Лента профиля: ссылки на посты с заглядыванием вперёд. | `Lookahead`, `iter_post_links` |
| `posts.py` | Сбор и обновление одного поста; читаемый captions.txt. | `comment_lines`, `write_captions`, `scrape_post`, `refresh_comments` … |
| `anchor.py` | Якорь профиля (хранится в `profile_anchors`): метка самого нового поста для режима «только новое». | `DEFAULT_STOP_AFTER`, `anchor_covers`, `compute_anchor` |
| `entry.py` | Вход в профиль: открытие ленты, проверки 404/429/логин, метаданные. Общий для `profile.py`, `threads.py` и `profile_only.py`. | `open_profile` |
| `about.py` | Окно «···» → «Об этом профиле»: имя, дата регистрации, местоположение (один раз на профиль, `profiles.about_*`, миграция 009). | `fetch_about` |
| `header.py` | Шапка профиля из DOM без кликов: темы (чипы), ссылки в био (Telegram, WhatsApp, Instagram), «недавние просмотры» (`profiles.topics/links/recent_views`, миграция 010). | `read_header` |
| `instagram.py` | Связь профиля с Instagram при открытии: ссылка в шапке > JSON страницы > био > догадка по нику (`instagram_links`, догадка помечена `guessed`). | `resolve_instagram` |
| `fill.py` | Разбор веток, собранных быстрым режимом (`--fill-threads`): ветки берутся из БД, не из ленты. | `run_fill` |
| `profile.py` | Обработка одного профиля (полный проход: ветки, посты под ветками, медиа). | `run_profile` |
| `profile_only.py` | Только метаданные профиля без обхода ленты и веток (`--profile-only`). | `run_profile_only` |
| `threads.py` | Быстрый сбор веток профиля за период только из ленты, без открытия веток (`--threads-only`). | `run_threads` |
| `report.py` | Итоги запуска: счётчики, записи журнала, сводка. | `new_counts`, `new_result`, `media_totals`, `profile_run_entry`, `print_summary` |
| `dispatch.py` | Очередь профилей и воркеры n x m; точка запуска run(). | `Dispatcher`, `RunState`, `worker_main`, `account_main`, `main_async`, `run` |

### `serp/` — Поиск по Threads (`search.py`)

| Модуль | Назначение | Основное содержимое |
|---|---|---|
| `tasks.py` | Задачи поиска: режимы, URL выдачи, окна дат, список запросов. | `MODES`, `DATE_FMT`, `parse_date`, `build_search_url`, `SearchTask`, `split_windows` … |
| `extract.py` | Извлечение постов и тем из ответов поиска. | `read_all_embedded`, `find_paths`, `NOISY_KEYS`, `harvest_post_pool`, `doc_is_useful`, `caption_text` … |
| `recon.py` | Разведка структуры выдачи (--recon). | `run_recon` |
| `store.py` | Хранилище поиска в Postgres: очередь задач, посты, связи с запросами; общее для воркеров запуска. | `job_key`, `SearchStore` |
| `runner.py` | Выполнение задач поиска воркерами. | `process_search_task`, `run_search_async`, `search_with_store`, `run_search` |

### `classify/` — Категории профилей через LLM (`cli.py classify`)

| Модуль | Назначение | Основное содержимое |
|---|---|---|
| `client.py` | HTTP-клиент к proxy API модели (формат OpenAI chat/completions), стандартная библиотека, повторы при 429/5xx/таймауте. | `LLMClient`, `LLMError`, `LLMAuthError` |
| `prompts.py` | Системный промпт с категориями из `categories`, пользовательский с профилем и ветками. | `PROMPT_VERSION`, `build_system_prompt`, `build_user_prompt` |
| `classifier.py` | Профиль и ветки -> категория; плохой JSON и неизвестный код дают `other` с пометкой. | `classify_profile`, `parse_answer`, `ClassifyResult` |
| `runner.py` | Воркеры: выборка `FOR UPDATE SKIP LOCKED`, запись категории, учёт в `runs`. | `run_classify` |
| `cli.py` | Подкоманда `classify`. | `main` |

### Единый стандарт кода

- Стиль и импорты проверяет `ruff` (настройки в `ruff.toml`): перед коммитом
  `ruff check --fix scripts && ruff format scripts`.
- Каждый модуль начинается с докстринга в одну строку; публичные функции — с типами.
- Имена полей поста одинаковы в обоих пайплайнах: `code`, `text`, `reply_count`
  (старые `posts.json` с `shortcode/caption/comments_count` приводятся к ним при импорте, `db/legacy.py`).
- Состояние воркера передаётся объектами (`Net`, `Account`, `Settings`), глобальных
  изменяемых переменных нет; метка лога и счётчики профиля живут в `contextvars` (`core/logs.py`).
- Слой хранения — Postgres, пакет `db/` (схема и структура — см. раздел «`db/` — Postgres»
  выше); профильный пайплайн получает `ProfileStore` и работает с теми же словарями постов,
  что раньше были в posts.json, поэтому логика обхода и комментариев не менялась. Поиск
  работает через `SearchStore` (`serp/store.py`).

## Установка

Нужен [uv](https://docs.astral.sh/uv/). Зависимости ставятся при первом `uv run`.
Если нет Chrome/Edge: `uv run playwright install chromium`.

## Быстрый старт

В `data/cookies/account_001.json` уже лежат ваши готовые куки: парсер сам заведёт их
в реестре.

```bash
uv run scripts/accounts.py check               # сессия жива?
uv run scripts/run.py olx_vetka_astana --limit 5
uv run scripts/run.py --since 1m --comments    # все профили из data/threads_profiles.txt
```

## Аккаунты и перенос на сервер

Вход проходится только в `accounts.py` (всегда с окном: капча, 2FA). Парсер браузером
не логинится, а читает куки из `data/cookies/`, поэтому на сервере окно не нужно.

| Команда | Что делает |
|---|---|
| `add N` | N аккаунтов: окна открываются по очереди, вы входите, куки сохраняются |
| `list` | аккаунты, ники, статусы и статистика из БД: запуски, профили, посты, комментарии, 429, ошибки авторизации |
| `proxy NAME [URL]` | прокси аккаунта (`--clear` сбрасывает; без URL показывает текущий) |
| `relogin NAME` | войти заново, когда сессия истекла |
| `check [NAME] [--headless]` | проверить, что сессии живы |
| `export [NAME...]` | все куки одним zip в `data/export/` |
| `import ИСТОЧНИК` | добавить аккаунты из zip, папки или одного файла кук |

```bash
# у себя
uv run scripts/accounts.py add 3
uv run scripts/accounts.py export

# на сервере
uv run scripts/accounts.py import accounts_<дата>.zip
uv run scripts/accounts.py check --headless
uv run scripts/run.py --headless --since 1m --comments   # все импортированные аккаунты
```

- **Один файл кук** (свой или выгрузка расширения Cookie-Editor):
  `uv run scripts/accounts.py import threads_cookies.json --user-agent "<UA вашего браузера>"`.
  Поля приводятся к формату Playwright. Нужны куки `sessionid` и `ds_user_id`.
- Или просто скопируйте файлы в `data/cookies/` на сервере: парсер сам заведёт их.
- Ник аккаунта (`username`) определяется сам: при `add`/`relogin`, при `check` и при первом
  запуске парсера, пока он пуст в реестре. Не определился, так и остаётся пустым (в `list`
  тогда виден `id:...`), на работу это не влияет.
- Повторный импорт того же аккаунта (по `ds_user_id`) заменяет сессию, дублей нет.
- Куки равноценны паролю, архив не зашифрован: не коммитьте, передавайте безопасно.
- Если IP сервера сильно отличается от вашего, Threads может сбросить сессию:
  `relogin` у себя и новый `export`.
- На сервере нужен браузер: `uv run playwright install chromium` и
  `uv run playwright install-deps chromium`.

## Сетка n × m

- **n** — аккаунты (`--accounts a,b` или `ACCOUNTS` в `.env`; по умолчанию все активные).
- **m** — воркеры на аккаунт (`--workers M` или `WORKERS_PER_ACCOUNT`). У аккаунта один
  браузер и одна сессия, а воркер это пара вкладок в нём: лента профиля и вкладка постов.

Парсер работает в одном процессе на `asyncio` (async API Playwright), поэтому вкладки
разных воркеров действительно идут параллельно. Профили из списка раздаются свободным
воркерам из общей очереди, одновременно идёт до `n × m` профилей. Если аккаунт получил
429 или checkpoint, его воркеры останавливаются, а профиль возвращается в очередь и
достаётся другому аккаунту. Профили, до которых дело не дошло, помечаются в итогах как
`not_run`: запустите ещё раз, сохранённое не перечитывается.

**Отличие от `insta_parser`:** там `m` это вкладки, делящие между собой посты одного
профиля. Здесь посты одного профиля идут строго по порядку: от него зависят определение
закреплённых, остановка по серии старых постов и дозагрузка комментариев. Поэтому
параллелятся профили, а не посты. Если профиль один, `n × m` ускорения не даст.

Сетевое состояние (журнал запросов, ответы сервера, счётчики) у каждого воркера своё, а не
общее на модуль, поэтому воркеры не мешают друг другу. Куки аккаунта пишутся один раз при
закрытии браузера, гонки записи файла нет.

Воркеры одного аккаунта используют одну и ту же сессию: не ставьте `m` слишком большим,
чтобы не получить 429 (разумно 1–2 на аккаунт). В режиме с окном вкладки одного браузера
по очереди выходят на передний план (без этого Threads не подгружает ленту), поэтому при
`m` > 1 надёжнее `--headless`.

## Термины: профиль, ветка, пост

Профиль (аккаунт) → ветки (записи верхнего уровня в ленте профиля) → посты (ответы под веткой).
В БД ветки лежат в таблице `posts`, посты под веткой в `comments`; названия таблиц
исторические и не меняются. Подробное ТЗ: `docs/TZ_threads.md`.

## Запуск `scripts/run.py`

```bash
uv run scripts/run.py olx_vetka_astana --limit 20 --comments
uv run scripts/run.py --since 2026-09-01 --comments --depth 3
uv run scripts/run.py --headless                 # профили из data/threads_profiles.txt
uv run scripts/run.py --accounts account_001,account_002 --workers 2 --since 1m --comments
```

Флаги: `--file`, `--accounts`, `--workers`, `--since` (30d, 4w, 1m, 1y или дата), `--limit`,
`--comments`, `--depth`, `--max-comments`, `--recheck-days`, `--full-rescan`, `--full`,
`--stop-after`, `--threads-only`, `--fill-threads`, `--profile-only`, `--only-unprocessed`, `--source`, `--with-videos`, `--no-block`, `--delay`, `--profile-delay`, `--headless`,
`--debug`, `--output`. Справка: `uv run scripts/run.py -h`.

### Продолжить с места остановки: `--only-unprocessed`

```bash
uv run scripts/run.py --source all --threads-only --only-unprocessed
uv run scripts/run.py --source all --profile-only --only-unprocessed
```

Из списка (профиль, `--file`, `--source`) убираются профили, уже обработанные до конца в
этом режиме или более глубоком. Отметка `profiles.processed_mode`
(`profile` < `threads` < `full`, миграция 015) ставится сразу по окончании каждого профиля,
поэтому переживает падение и Ctrl+C. Обработанным считается результат `ok`, `empty` и 404. Прерванные (429, ошибка браузера или БД) и профили, у которых не
хватило веток для класса, остаются необработанными и берутся снова. Режим глубже покрывает
мелкий: `--profile-only --only-unprocessed` пропустит и тех, у кого уже собраны ветки, а
`--threads-only --only-unprocessed` возьмёт профили, обработанные только `--profile-only`.
В `--fill-threads` флаг не нужен: он и так берёт только неразобранные ветки. Сколько осталось:
`scripts/db/progress.sql` (блок 5).

### Скорость и паузы

Время на профиль складывается из пауз и ожиданий страницы. Паузы по умолчанию: `PROFILE_DELAY=8`
(реально от 8 до 12 с между профилями одного воркера) и `DELAY=2` (от 2 до 4 с между ветками
в полном проходе); раньше было 20 и 4. Профиль открывается без фиксированных 3 с: ожидание
заканчивается, как только в ленте появилась первая ветка. Если в `scripts/.env` остались свои
`DELAY` и `PROFILE_DELAY`, они сильнее значений по умолчанию: поправьте их там. Сколько реально
уходит на профиль и сколько 429: `scripts/db/progress.sql` (блок 3). Ускорять дальше можно
`--workers` (воркеров на аккаунт) и режимом `--threads-only`: он не открывает
каждую ветку.

### Только профиль: `--profile-only`

```bash
uv run scripts/run.py olx_vetka_astana --profile-only
uv run scripts/run.py --source all --profile-only --headless
```

Открывает профиль и сохраняет только его данные: подписчиков, био, окно «Об этом профиле»,
шапку (темы, ссылки, просмотры), связь с Instagram, страну/город. Лента не прокручивается,
ветки не читаются и не пишутся, вторая вкладка не открывается. Несовместим с `--threads-only` и `--fill-threads`; `--since`, `--limit`,
`--full`, `--stop-after` и флаги про содержимое ветки игнорируются с предупреждением.
В `profile_runs` пишется `mode='profile'`, колонки `posts_*` нулевые.

### Быстрый сбор веток: `--threads-only`

```bash
uv run scripts/run.py olx_vetka_astana --threads-only --since 7d
uv run scripts/run.py --threads-only --since 1m          # профили из data/threads_profiles.txt
```

Проходит ленту профиля и сохраняет в `posts` ветки за период: текст, дату, лайки, число
ответов. Сами ветки не открываются, комментарии и медиа не собираются, якорь не читается и не
пишется. Такие ветки остаются с `last_refreshed_at IS NULL`; обычный проход (без
`--threads-only`) потом разберёт их полностью. Работают `--since`, `--limit` (веток на
профиль), `--full`, `--stop-after` (остановка после N подряд уже известных веток),
`--accounts`, `--workers`, `--delay`, `--headless`; флаги про содержимое ветки
(`--comments`, `--depth`, `--with-videos` и т. п.) игнорируются с предупреждением.
В `profile_runs` такой запуск пишется с `mode='threads'`; колонки `posts_*` в нём считают ветки.

### Категории профилей КЗ через Jev: `jev`

```bash
uv run scripts/cli.py jev --dry-run [--show 5]   # фильтр КЗ + поля профиля, счётчики, без запросов
uv run scripts/cli.py jev --limit 50             # пробный прогон
uv run scripts/cli.py jev --all [--recheck]      # полный прогон
uv run python tests/jev_check.py                 # проверка без БД и сети
```

Jev идёт через OpenRouter (`OPENROUTER_API_KEY`, `JEV_MODEL=typesafe/jev-1.14`), запросы через SOCKS5 `JEV_PROXY` (нужен `httpx[socks]`). Отбор: КЗ по `geo.resolve`, затем `about_name`, `bio` (иначе `og_description`), `topics`, местоположение «Казахстан». Берётся класс с наибольшей вероятностью, но при вероятности < `JEV_MIN_PROB`, confidence < `JEV_MIN_CONFIDENCE` или отрицательной Noul-проверке итог `other` (Прочее). Все вероятности лежат в `profiles.category_raw`. Ответ модели старше `JEV_MIN_VERSION` останавливает запуск.

### Категории постов КЗ через Jev: `jev-posts`

```bash
uv run scripts/cli.py db migrate                  # миграция 017: posts.category_*
uv run scripts/cli.py jev-posts --dry-run [--show 5]
uv run scripts/cli.py jev-posts --limit 100       # пробный прогон
uv run scripts/cli.py jev-posts --all [--recheck] # полный прогон
```

`posts JOIN profiles` по `lower(profiles.username) = lower(posts.author_username)`, фильтр `profiles.about_location = «Казахстан»`; классифицируется только `posts.text` (короче `--min-chars` пропускается). Категории, пороги, защита «Прочее» и прокси те же, что у `jev`. Результат: `posts.category_id`, `category_raw`, `category_model`, `category_prompt_version`. Пост с ошибкой запроса остаётся без категории и берётся в следующем запуске.

### Категории профилей: `classify`

```bash
uv run scripts/cli.py classify --limit 50                       # профили без категории
uv run scripts/cli.py classify --limit 200 --workers 4
uv run scripts/cli.py classify --limit 50 --source failed          # повтор неудачных (category_raw.error)
uv run scripts/cli.py classify --limit 50 --recheck             # переклассифицировать профили с категорией
uv run scripts/cli.py classify --stats                          # категории, время, расход запросов
```

Нужны `LLM_PROXY_URL`, `LLM_PROXY_API_KEY`, `LLM_MODEL` в `scripts/.env` (блок
`--- LLM-классификация ---` в `.env.example`). `--limit` обязателен. Модель получает `og_title`,
`og_description` и последние `LLM_SAMPLE_THREADS` веток профиля (посты под ветками не берутся) и
отвечает JSON `{"category", "confidence", "reason"}`. Результат: `profiles.category_id`,
`category_raw` (ответ, время, токены), `category_model`, `category_prompt_version`;
Статуса проверки нет, выборка идёт по самим данным: `--source new` (по умолчанию) = `category_id`
пуст и `category_raw.error` пуст; `--source failed` = `category_raw.error` не пуст (ответ не
разобран или запрос не прошёл); `--recheck` = категория уже есть. Профиль берётся `FOR UPDATE SKIP LOCKED` и остаётся
заблокированным до записи результата, поэтому воркеры и процессы не берут один профиль дважды.
Ошибки proxy API (таймаут, 429, 5xx, невалидный ответ) пишут `category_raw.error` и не останавливают
запуск; 401/403 останавливают его целиком, профиль остаётся как был. Запуск пишется в `runs`
(`kind='classify'`, итоги в `settings`).

### Выгрузка из БД в xlsx: `export.py`, `exporter/`

| Модуль | Назначение |
|---|---|
| `export.py` | CLI: выбор запросов, `--since`, `--limit`, `--out`; один запрос = один лист. |
| `exporter/queries.py` | Базовые запросы (`BASE_QUERIES`): добавить свой = добавить запись в словарь. |
| `exporter/sqlfile.py` | Разбор файла своих запросов, подстановка `:since` и `:limit`. |
| `exporter/xlsx.py` | Запись книги: шапка, фильтр, ширина колонок; JSON, списки и даты приводятся к ячейкам. |

Запросы идут в read-only транзакции Postgres: `INSERT`/`UPDATE`/`DELETE`/DDL завершатся ошибкой, данные не изменятся. Времена в файле в UTC. Текст, начинающийся с `=`, записывается как текст, а не как формула.

Базовые запросы (`--list`): `profiles`, `profiles-full`, `threads`, `top-authors`, `instagram`. Смысл `--since` у каждого указан в `--list`.

Лист `profiles` — только важное, в таком порядке: `id`, `название` (ник), `ссылка`, `подписчиков`, `веток` (`threads_count`, из og:description), `просмотров` (недавние просмотры из шапки), `bio`, `instagram`, `whatsapp`, `telegram`, `другие ссылки` (из `profiles.links`, ссылки в шапке; instagram: ещё подтверждённая связка из `instagram_links`), `дата создания` (`about_joined`), `локация из био` (`about_location`, «Об этом профиле»), `темы` (чипы шапки профиля), `веток за 7д/30д/90д (оценка)` (в конце листа: `веток` ÷ дней с даты создания × 7/30/90, не больше `веток`; день регистрации в источнике неизвестен, берётся 1-е число месяца; без даты создания — пусто). Прежний широкий лист (категория, `is_target`, `processed_mode`) — `profiles-full`.

**Свои запросы** лежат в `data/export_queries.sql` (путь: `EXPORT_QUERIES_FILE` или `--queries-file`):

```sql
-- name: my-query
-- desc: что выгружает
SELECT username, followers_count FROM profiles WHERE followers_count < 100 LIMIT :limit;
```

В тексте доступны `:since` (дата или NULL) и `:limit` (число или NULL). Имя совпало с базовым: побеждает файл. Разово: `--sql "SELECT ..."` или `--file запрос.sql`.

```bash
uv run scripts/cli.py export --list                              # что можно выгрузить
uv run scripts/cli.py export profiles                            # data/export/profiles_ДАТА.xlsx
uv run scripts/cli.py export profiles threads --since 7d         # два листа в одном файле
uv run scripts/cli.py export top-authors --since 1m --limit 500 --out data/export/top.xlsx
uv run scripts/cli.py export my-query --out D:\work\reports\   # в папку, имя файла автоматически
uv run scripts/cli.py export --sql "SELECT username FROM profiles WHERE NOT is_target" --sheet off
```

Нужен пакет `openpyxl` (в `pyproject.toml`; `uv run` подтянет его сам).

### Свои запросы на изменение БД: `sqlrun.py`, `data/db_tasks.sql`

Аналог `export.py`, но с записью. Запросы лежат в `data/db_tasks.sql` (`DB_TASKS_FILE`):
`-- name:`, `-- desc:`, параметры `-- param: имя=значение`, в тексте `:имя::тип`. Без `--apply`
запрос выполняется и откатывается (видно, сколько строк изменится); `UPDATE`/`DELETE` без `WHERE`
отклоняются; `SELECT` печатает до 20 строк.

```bash
uv run scripts/sqlrun.py --list
uv run scripts/sqlrun.py select-targets                                   # пробный прогон
uv run scripts/sqlrun.py select-targets --set min_followers=200 --set min_per_day=2 --apply
uv run scripts/sqlrun.py targets-stats                                    # сколько целевых профилей
```

`select-targets` ставит `profiles.is_target` по статистике: подписчиков не меньше
`min_followers` (100) и в среднем не меньше `min_per_day` (1) веток в день с даты регистрации
(`threads_count` / дни с `about_joined`). Профили без этих данных не меняются: сначала
`run.py --profile-only`. Парсер берёт профили по `--source` только с `is_target = true`.

### Разбор собранных веток: `--fill-threads` и выбор профилей `--source`

```bash
uv run scripts/run.py olx_vetka_astana --fill-threads --since 7d --comments --depth 3
uv run scripts/run.py --source new --threads-only --since 7d
uv run scripts/run.py --source all --fill-threads --since 7d --limit 50
```

`--fill-threads` не листает ленту: берёт из `posts` ветки профиля с `last_refreshed_at IS NULL`
за период (новые первыми, `--limit` = сколько веток на профиль) и разбирает каждую как полный
проход (текст, медиа, посты под веткой по `--comments`/`--depth`). Разобранная ветка получает
`last_refreshed_at`, поэтому повторный запуск её уже не открывает; видео-ветки при режиме
«только фото» попадают в `skipped_posts` и тоже не повторяются. Закреплённость не
проверяется, период применён при сборе. Якорь не пишется. Несовместим с `--threads-only`.

`--source` берёт профили из БД и добавляет их к профилю из командной строки и к `--file`:
`all` (все с `is_target`), `new` (профили, у которых ещё нет ни одной ветки), `ник1,ник2`.
Из БД берутся только профили с `is_target = true` (его ставит `sqlrun.py select-targets`).
Функция выбора — `list_targets()` в `db/profiles.py`.

### Настройки по умолчанию в `.env`

`LIMIT`, `SINCE`, `COMMENTS`, `DEPTH`, `MAX_COMMENTS`, `RECHECK_DAYS`, `WITH_VIDEOS`, `DELAY`,
`PROFILE_DELAY`, `STOP_AFTER_KNOWN`, `DEBUG` (плюс прежние аккаунты, воркеры, headless и пути).
Флаг в командной строке перекрывает `.env`; у переключателей есть обратная форма
(`--no-comments`, `--no-with-videos`, `--no-headless`, `--no-debug`).

### `--since`: месяцы календарные

`1m` и `1y` считаются по календарю: 15 октября минус `1m` это 15 сентября, а 31 марта
минус `1m` это 28 (29) февраля. `d` и `w` это дни и недели.

### Якорь и режим «только новое»

После первого успешного прохода по окну (`--limit` или `--since`, либо по всему профилю)
в таблицу `profile_anchors` пишется якорь. Дальше обход каждого профиля
останавливается, когда подряд встретилось `--stop-after` известных постов
(`STOP_AFTER_KNOWN`, по умолчанию 5). Закреплённые посты в серию не входят.

- Пост, которому нужна работа (новый, свежий под `--recheck-days`, вырос счётчик ответов),
  серию обнуляет. Поэтому типичный сценарий «первый раз окно, потом добираю новое и
  комментарии» делает ровно столько прокруток, сколько нужно.
- Якорь работает, только если окно не шире покрытого: после `--since 1m` запуск с
  `--since 1y` пройдёт профиль полностью и обновит якорь. Проход по якорю окно не расширяет.
- Якорь не пишется, если проход прервался (429, авторизация, ошибки). Если из
  БД пропал пост, на который ссылается якорь, делается полный проход.
- `--full` игнорирует якорь и проходит всё окно. Он нужен, чтобы дочитать ответы под
  старыми постами за пределами серии, а `--full-rescan` про другое: перечитывает ответы
  уже известных комментариев.
- В итогах и `summary.json` у профиля есть поле `mode`: `only_new`, `full`, `threads` или `fill`.

Логика парсинга постов, дерево комментариев и дочитывание только новых ответов перенесены из
исходного `threads_browser_parser.py` без изменений.

Автоматизация нарушает условия использования Threads/Meta: используйте паузы и небольшие
объёмы.

## Instagram: отчёт

`uv run scripts/cli.py instagram stats` (сколько профилей со связью, по источникам) и `instagram list [--source ...] [--confidence guessed] [--csv файл]` (`scripts/instagram.py`).

## Страна и город профилей (КЗ, УЗ)

Сигналы по убыванию надёжности: `about_location` («Об этом профиле»; не КЗ/УЗ сохраняется как `OTHER`) > телефон в WhatsApp-ссылке (998 → UZ; 7 + 6xx/7xx → KZ, российские и неоднозначные номера не считаются) > био > имя из about > темы шапки. Города в текстах веток (`post_text`) слабый сигнал: хранятся отдельно и в итог не входят.

В БД лежат только факты (`location_mentions`, миграция 008/011; страна «не КЗ/УЗ» восстанавливается из `profiles.about_location`); итог считается при чтении (`core/geo.py::resolve`), поэтому правила меняются без миграций. Если надёжные источники называют разные страны, итог берётся из самого надёжного, строка помечается КОНФЛИКТ и показывает все источники. Сигналы профиля пишутся в `open_profile` без запросов к Threads, упоминания постов, при сохранении поста; ошибки детектора только логируются.

### Починка кодировки био: `dbadmin.py fix-encoding`

Био, записанное до этой правки, могло лежать в БД как `\u041f\u043e\u043a\u0443...` (в JSON страницы кириллица и эмодзи экранированы, а перекодировка этого не разбирала). Источник исправлен (`core/biography.py`, `core/textfix.py`). Старые записи чинятся одной командой: она разбирает `bio`, `og_title`, `og_description`, `about_name`, `about_location`, после чего пересчитывает геосигналы этих профилей. Повторный запуск безопасен.

```bash
uv run scripts/dbadmin.py fix-encoding --dry-run   # сколько записей затронуто
uv run scripts/dbadmin.py fix-encoding
```

```bash
uv run scripts/cli.py geo rescan                           # пересчёт всех профилей и постов по данным в БД (после правки справочника: поднять DETECTOR_VERSION)
uv run scripts/cli.py geo stats                             # топ стран/городов по профилям и постам, без данных, источники
uv run scripts/cli.py geo list --country KZ --city Алматы [--csv файл]
uv run python tests/geo_check.py                            # проверка без сети и БД
uv run python tests/profile_data_check.py                   # кодировка био, число веток, лист выгрузки
```

### Дополнение профилей из старой БД: `dbadmin.py merge-old-profiles`

Заполняет пустые поля `threads.profiles` значениями из таблицы `threads.profiles` старой базы
`threads_old` (другая БД на том же сервере; схема и таблица называются так же). Адрес старой БД:
`DATABASE_URL` с именем базы `threads_old` либо `--old-url`. Профили сопоставляются по
`lower(username)`; берутся общие колонки с совместимым типом; заполненное не трогается. Служебные
колонки (id, ключи, состояние обработки) и колонки NOT NULL пропускаются.

```
uv run scripts/dbadmin.py merge-old-profiles --dry-run   # сколько значений по каждой колонке
uv run scripts/dbadmin.py merge-old-profiles
uv run scripts/dbadmin.py merge-old-profiles --old-url postgresql://USER:PASSWORD@localhost:5432/threads_old
uv run scripts/dbadmin.py merge-old-profiles --columns bio followers_count   # только эти
uv run scripts/dbadmin.py merge-old-profiles --exclude og_image              # кроме этих
uv run scripts/dbadmin.py merge-old-profiles --overwrite                     # и заполненные
```
