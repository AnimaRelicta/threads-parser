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
│   ├── cli.py         # единая точка входа: parse | search | accounts
│   ├── run.py         # CLI: парсинг профилей
│   ├── search.py      # CLI: поиск по выдаче Threads, --refilter и --stats (БД, без браузера)
│   ├── accounts.py    # CLI: аккаунты (add/list/relogin/check/export/import)
│   ├── dbadmin.py     # CLI: Postgres (migrate/status/import-accounts/import-profiles/import-search)
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
| `fsio.py` | Чтение и запись файлов: JSON, JSONL, списки профилей. | `read_json`, `read_jsonl`, `write_jsonl_atomic`, `read_usernames`, `normalize_username` |

### `db/` — Postgres (схема `threads`)

| Модуль | Назначение | Основное содержимое |
|---|---|---|
| `pool.py` | Подключение: пул asyncpg, разовые запросы из синхронного кода. | `SCHEMA`, `DB_ERRORS`, `require_url`, `create_pool`, `connect`, `connection`, `run_sync` |
| `migrate.py` | Миграции `db/migrations/*.sql` по порядку, учёт в `schema_migrations`. | `list_migrations`, `migrate`, `status` |
| `accounts.py` | Таблица `accounts`: чтение, вход, обновление, итог запуска, импорт старого реестра. | `fetch_accounts`, `register_login`, `update_account`, `mark_run`, `import_legacy` |
| `posts.py` | Посты, комментарии, медиа, якоря и пропущенные посты профиля: запись и чтение в прежнем формате словарей. | `save_post`, `load_profile_posts`, `mark_skipped`, `save_anchor`, `save_meta`, `upsert_topic` |
| `profiles.py` | Хранилище профильного пайплайна поверх пула: общий для воркеров запуска. | `ProfileStore`, `ProfileState` |
| `search.py` | Поиск: очередь `search_jobs`, найденные посты, связи `post_hits`, пересчёт фильтра. | `ensure_job`, `start_job`, `finish_job`, `save_hits`, `refilter` |
| `legacy.py` | Разовый перенос старых файлов (posts.json профилей, posts.jsonl и jobs.json поиска). | `normalize_post`, `import_profiles`, `import_search` |
| `runs.py` | Запуски: `runs`, `run_accounts`, `profile_runs`. | `start_run`, `finalize_run`, `save_profile_runs`, `ensure_profile` |
| `stats.py` | Реестр отчётов (SQL + заголовок) и вывод таблицей. | `Report`, `REPORTS`, `run_report`, `format_table` |
| `migrations/001_init.sql` | Начальная схема (аккаунты, запуски, профили, посты, комментарии, медиа, поиск, темы). | |

Подготовка: `DATABASE_URL` в `scripts/.env`, затем `uv run scripts/cli.py db migrate`;
состояние: `uv run scripts/cli.py db status`. Перенос идёт поэтапно: в БД уже живут
реестр аккаунтов, записи запусков (`runs`, `run_accounts`, `profile_runs`) и весь профильный
пайплайн (`profiles`, `posts`, `comments`, `media`, `profile_anchors`, `skipped_posts`) и поиск
(`search_jobs`, `posts`, `post_hits`, `topics`). Файлы в `data/output/<ник>/` теперь только
`media/`, `captions.txt` и итоговый `summary.json`.

Поиск: готовое окно (задача `done`) не перезапускается без `--force`; пост, найденный несколькими
запросами, хранится один раз, а каждая находка записана в `post_hits`. Статус `approved/rejected`
считается при сохранении (`filter_version` — версия правил); после правки правил в
`core/textfilter.py` нужно поднять `FILTER_VERSION` и выполнить `uv run scripts/search.py --refilter`
(с `--force` пересчитываются все посты). Данные прежних запусков:
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
| `harvest.py` | Накопление постов из ответов сервера: пул, индекс ленты, выбор дочерних узлов. | `Harvester`, `FeedIndex`, `pick_children` |
| `page.py` | Работа со страницей: ожидание сети, прокрутка, «ещё», открытие поста. | `settle`, `CLICK_MORE_JS`, `SCROLL_JS`, `scroll_down`, `page_infos`, `click_more` … |
| `capture.py` | Перехват JSON-ответов Threads вкладкой. | `make_handler` |
| `children.py` | Подгрузка ответов одного узла дерева комментариев. | `load_children` |
| `media.py` | Скачивание медиа через сессию аккаунта. | `fetch_bytes`, `download`, `save_comment_media` |
| `comments.py` | Сбор комментариев деревом. | `make_comment`, `root_node_from`, `node_from_comment`, `collect_thread`, `finalize_comments` |
| `feed.py` | Лента профиля: ссылки на посты с заглядыванием вперёд. | `Lookahead`, `iter_post_links` |
| `posts.py` | Сбор и обновление одного поста; читаемый captions.txt. | `comment_lines`, `write_captions`, `scrape_post`, `refresh_comments` … |
| `anchor.py` | Якорь профиля (хранится в `profile_anchors`): метка самого нового поста для режима «только новое». | `DEFAULT_STOP_AFTER`, `anchor_covers`, `compute_anchor` |
| `profile.py` | Обработка одного профиля. | `run_profile` |
| `report.py` | Итоги запуска: счётчики, записи журнала, сводка. | `media_totals`, `profile_run_entry`, `print_summary` |
| `dispatch.py` | Очередь профилей и воркеры n x m; точка запуска run(). | `Dispatcher`, `RunState`, `worker_main`, `account_main`, `main_async`, `run` |

### `serp/` — Поиск по Threads (`search.py`)

| Модуль | Назначение | Основное содержимое |
|---|---|---|
| `tasks.py` | Задачи поиска: режимы, URL выдачи, окна дат, список запросов. | `MODES`, `DATE_FMT`, `parse_date`, `build_search_url`, `SearchTask`, `split_windows` … |
| `extract.py` | Извлечение постов и тем из ответов поиска. | `read_all_embedded`, `find_paths`, `NOISY_KEYS`, `harvest_post_pool`, `doc_is_useful`, `caption_text` … |
| `recon.py` | Разведка структуры выдачи (--recon). | `run_recon` |
| `store.py` | Хранилище поиска в Postgres: очередь задач, посты, связи с запросами; общее для воркеров запуска. | `job_key`, `SearchStore` |
| `runner.py` | Выполнение задач поиска воркерами. | `process_search_task`, `run_search_async`, `search_with_store`, `run_search` |

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

## Запуск `scripts/run.py`

```bash
uv run scripts/run.py olx_vetka_astana --limit 20 --comments
uv run scripts/run.py --since 2026-09-01 --comments --depth 3
uv run scripts/run.py --headless                 # профили из data/threads_profiles.txt
uv run scripts/run.py --accounts account_001,account_002 --workers 2 --since 1m --comments
```

Флаги: `--file`, `--accounts`, `--workers`, `--since` (30d, 4w, 1m, 1y или дата), `--limit`,
`--comments`, `--depth`, `--max-comments`, `--recheck-days`, `--full-rescan`, `--full`,
`--stop-after`, `--with-videos`, `--no-block`, `--delay`, `--profile-delay`, `--headless`,
`--debug`, `--output`. Справка: `uv run scripts/run.py -h`.

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
- В итогах и `summary.json` у профиля есть поле `mode`: `only_new` или `full`.

Логика парсинга постов, дерево комментариев и дочитывание только новых ответов перенесены из
исходного `threads_browser_parser.py` без изменений.

Автоматизация нарушает условия использования Threads/Meta: используйте паузы и небольшие
объёмы.
