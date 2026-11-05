# threads-parser: инструкция по запуску

Пошаговое руководство по всем базовым сценариям: от установки до классификации профилей. Все команды выполняются из корня проекта.

## 1. Как устроен конвейер

Термины (в БД имена таблиц историческые):

- **Профиль** (`profiles`) → **ветки** (`posts`, записи верхнего уровня в ленте профиля) → **посты под веткой** (`comments`).

Этапы работы:

1. **Сбор.** Профили из списка парсятся браузером (Playwright): ветки, медиа, посты под ветками. Либо быстрый режим: только список веток за период.
2. **Поиск.** `search.py` ищет ветки по ключевым словам во всей выдаче Threads.
3. **Новые профили.** `discover` находит авторов, которых ещё нет в `profiles`, и заводит их со статусом `pending_review`.
4. **Классификация.** `classify` просит LLM определить категорию профиля (витрина, барахолка, перекупщик и т. д.).

Единая точка входа: `uv run scripts/cli.py <команда>`. Команды: `parse` (= `run.py`), `search`, `discover`, `classify`, `accounts`, `db`. Файлы `run.py`, `search.py`, `accounts.py`, `discover.py` можно запускать и напрямую; `classify` запускается только через `cli.py`.

## 2. Подготовка

### 2.1. Установка

Нужен [uv](https://docs.astral.sh/uv/). Зависимости ставятся при первом `uv run`. Браузер: если нет Chrome/Edge, выполните `uv run playwright install chromium`. На сервере дополнительно `uv run playwright install-deps chromium`.

### 2.2. Применение патчей (если ещё не применены)

Патчи накладываются строго по порядку: шаг 4, шаг 5, шаг 6.

```bash
git apply --check step4_discovery.patch && git apply step4_discovery.patch
git apply --check step5_audit_runs.patch && git apply step5_audit_runs.patch
git apply --check step6_classify.patch && git apply step6_classify.patch
```

Если `--check` ругается на пробелы или окончания строк, добавьте `--ignore-whitespace` (часть файлов проекта с CRLF). Перед началом убедитесь, что рабочее дерево чистое.

### 2.3. Настройки `scripts/.env`

Файл создаётся копией `scripts/.env.example` и в git не попадает. Минимум:

- `DATABASE_URL=postgresql://USER:PASSWORD@localhost:5432/DBNAME`: без него не работает ничего.
- Для классификации: `LLM_PROXY_URL`, `LLM_PROXY_API_KEY`, `LLM_MODEL`. Остальное (`LLM_TIMEOUT_SEC`, `LLM_MAX_RETRIES`, `LLM_WORKERS`, `LLM_SAMPLE_THREADS`) имеет значения по умолчанию.
- Необязательно: `SINCE` (период по умолчанию), `LIMIT`, `COMMENTS`, `DEPTH`, `HEADLESS`, `PROXY`, `WORKERS_PER_ACCOUNT`.

`LLM_PROXY_URL` может быть базой (`https://host/v1`) или полным адресом `.../chat/completions`: клиент ожидает формат OpenAI chat/completions. Ключ и адрес храните только в `scripts/.env`.

### 2.4. Миграции

```bash
uv run scripts/cli.py db migrate
uv run scripts/cli.py db status
```

В `status` в списке применённых должны быть `001_init`, `002_threads_light`, `003_discovery`, `004_audit_runs`, `005_categories`. Миграция 004 удаляет неиспользуемую таблицу `custom_topics`; миграция 005 создаёт справочник `categories` (7 категорий) и триггер, который проверяет допустимые переходы `review_status`. Перед первым прогоном на рабочей базе сделайте её копию.

Если у вас ещё остались данные в старых JSON-файлах, разово: `uv run scripts/dbadmin.py import-accounts`, `import-profiles`, `import-search`.

### 2.5. Аккаунты Threads

Вход выполняется только в `accounts.py` (с окном браузера: капча, 2FA). Парсер сам не логинится, а читает куки из `data/cookies/`.

```bash
uv run scripts/accounts.py add 3            # завести 3 аккаунта, окна по очереди
uv run scripts/accounts.py list             # аккаунты, статусы, статистика
uv run scripts/accounts.py check            # сессии живы?
uv run scripts/accounts.py relogin account_002   # сессия истекла
uv run scripts/accounts.py proxy account_002 socks5://127.0.0.1:12334
```

Если файл кук уже лежит в `data/cookies/`, парсер сам заведёт аккаунт в реестре.

### 2.6. Списки

- `data/threads_profiles.txt`: профили, по одному в строке (ник, `@ник` или ссылка; строки с `#` игнорируются).
- `data/threads_queries.txt`: поисковые запросы, по одному в строке.

## 3. Сценарий A: проверка, что всё работает

Пробный прогон на одном профиле:

```bash
uv run scripts/accounts.py check
uv run scripts/run.py olx_vetka_astana --limit 5
```

`--limit 5`: взять пять подходящих веток профиля. Результат смотрите в `data/output/<ник>/` (медиа, `summary.json`) и в БД (`posts`, `profiles`). На сервере добавляйте `--headless`.

## 4. Сценарий B: обычный парсинг профилей

Один проход по ленте профиля с открытием каждой ветки: текст, медиа и (по флагу) посты под ветками.

```bash
uv run scripts/run.py olx_vetka_astana --since 7d --comments --depth 2
uv run scripts/run.py --since 1m --comments        # все профили из data/threads_profiles.txt
uv run scripts/run.py --since 30d --accounts account_001,account_002 --workers 2
```

Основные флаги:

| Флаг | Смысл |
| --- | --- |
| `--since` | период: `30d`, `4w`, `1m` (календарный месяц), `1y` или дата `2026-09-01` |
| `--limit` | веток на профиль, `0` = все |
| `--comments`, `--depth` | собирать посты под веткой; глубина `1` = ответы на ветку, `2` = и ответы на ответы, `-1` = всё |
| `--max-comments` | максимум постов под веткой, `0` = все |
| `--with-videos` | брать и ветки с видео (по умолчанию только текст и фото) |
| `--full` | полный проход, игнорируя якорь |
| `--accounts`, `--workers` | сетка: аккаунтов n × воркеров m |
| `--headless` | без окна браузера (сервер) |

Обычный повторный запуск использует **якорь**: если профиль уже проходился, обход останавливается после `--stop-after` (по умолчанию 5) подряд известных веток. Нужно перепройти всё окно: добавьте `--full`. Свежие ветки (младше `RECHECK_DAYS`) проверяются на новые посты под ними при каждом запуске.

**Важно для следующих шагов:** шаг «новые профили» (сценарий E) берёт авторов из постов под ветками, поэтому хотя бы часть профилей должна быть пройдена с `--comments`.

## 5. Сценарий C: два шага, быстро и потом глубоко

Быстрый режим стоит одного прохода по ленте на профиль, а дорогой разбор можно запускать позже и выборочно.

```bash
# 1) только список веток за период: без открытия веток, комментариев и медиа
uv run scripts/run.py --threads-only --since 7d

# 2) разобрать собранные ветки: открыть, скачать медиа, собрать посты под веткой
uv run scripts/run.py olx_vetka_astana --fill-threads --since 7d --comments --depth 3
```

- `--fill-threads` берёт из БД ветки профиля, у которых ещё нет разбора, и разбирает их. Повторный запуск уже разобранные ветки не открывает.
- Якорь в этих режимах не пишется. Режимы `--threads-only` и `--fill-threads` несовместимы друг с другом.
- Флаги про содержимое ветки (`--comments`, `--depth` и т. д.) в `--threads-only` игнорируются с предупреждением.

### Выбор профилей из БД: `--source`

Вместо файла можно брать профили из базы:

```bash
uv run scripts/run.py --source new --threads-only --since 7d     # профили без единой ветки
uv run scripts/run.py --source all --fill-threads --since 7d --limit 50
uv run scripts/run.py --source olx_vetka_astana,auyz__tea --since 7d
uv run scripts/run.py --source authors --threads-only --since 7d  # найденные discover по авторам постов
uv run scripts/run.py --source search --threads-only --since 7d   # найденные discover по авторам из поиска
```

Значения: `all` (все с `is_target`), `new`, `search`, `authors` или ники через запятую. Профили, заведённые командой `discover`, тоже имеют `is_target = true`, поэтому входят и в `--source all`. Чтобы обработать только их, используйте `authors` или `search`.

## 6. Сценарий D: поиск по Threads

Поиск находит ветки по ключевым словам, независимо от профилей. Работает через браузер и аккаунты.

```bash
uv run scripts/search.py "олх ветка астана" --since 7d
uv run scripts/search.py --file data/threads_queries.txt --since 7d
uv run scripts/search.py "олх ветка" --after 2026-10-20 --before 2026-10-27 --window-days 3 --modes recent top
```

| Флаг | Смысл |
| --- | --- |
| `--since` или `--after`/`--before` | период (даты `YYYY-MM-DD`); явные даты важнее `--since` |
| `--modes` | `top`, `recent` или оба |
| `--window-days` | нарезать период на окна по N дней |
| `--max-posts`, `--max-scrolls` | лимиты на задачу, `0` = без лимита |
| `--workers`, `--accounts`, `--headless` | параллельность и аккаунты |
| `--force` | перезапустить уже готовые окна (без него готовое не повторяется) |

Дополнительные режимы, которые работают с БД без браузера:

```bash
uv run scripts/search.py --stats      # качество запросов, авторы, очередь задач
uv run scripts/search.py --refilter   # пересчитать approved/rejected после правки фильтра
```

`--recon` нужен для разведки структуры выдачи и в обычной работе не используется. Каждая найденная ветка хранится один раз; кто её нашёл, видно по `post_hits`.

## 7. Сценарий E: новые профили по авторам

Команда работает только с БД, браузер не нужен. Она ищет ники, которых нет в `profiles`:

- авторов постов под ветками (`comments.author`), тип `author`;
- авторов веток из поиска (`posts.author_username` без профиля), тип `search`.

Автор, пишущий только под своей веткой, не считается. Если ник встретился в обоих местах, берётся `search`.

```bash
uv run scripts/cli.py discover --since 7d --dry-run     # только показать кандидатов
uv run scripts/cli.py discover --since 7d               # завести профили
uv run scripts/cli.py discover --since 1m --min-mentions 3   # только авторы с 3+ встречами
uv run scripts/cli.py discover --since 7d --dry-run --show 0   # показать всех кандидатов
uv run scripts/cli.py discover --stats                  # очередь, ветки без разбора, запуски
```

Сначала всегда делайте `--dry-run`: комментаторов может быть очень много, и каждый станет профилем с `is_target = true` и статусом `pending_review`. Параметр `--min-mentions` отсекает случайных авторов. Повторный запуск ничего не добавляет. Реальные запуски пишутся в `runs` (`kind='discover'`), пробные нет.

Чтобы найденные профили были пригодны для классификации, соберите их ветки:

```bash
uv run scripts/run.py --source authors --threads-only --since 30d
uv run scripts/run.py --source search --threads-only --since 30d
```

## 8. Сценарий F: категории профилей через LLM

Модель получает название и описание профиля и последние ветки (`LLM_SAMPLE_THREADS`, по умолчанию 10); посты под ветками не передаются. Ответ: категория, уверенность, короткая причина. Если у профиля нет ни описания, ни веток, контекст почти пустой, и модель, скорее всего, ответит `other`: сначала соберите ветки (сценарий E или C).

```bash
uv run scripts/cli.py classify --limit 20                       # профили в pending_review
uv run scripts/cli.py classify --limit 200 --workers 4
uv run scripts/cli.py classify --limit 50 --source new          # профили из списка
uv run scripts/cli.py classify --limit 50 --source review_failed   # повтор неудачных
uv run scripts/cli.py classify --limit 50 --recheck             # переклассифицировать проверенные
uv run scripts/cli.py classify --stats                          # итоги
```

Правила:

- `--limit` обязателен (кроме `--stats`). Начните с малого числа, например 3–5, и проверьте результат.
- `--source` принимает `pending_review` (по умолчанию), `new`, `review_failed`. `--recheck` с `--source` не сочетается.
- Профили из `threads_profiles.txt` имеют статус `new`: они не попадут в классификацию, пока вы явно не укажете `--source new`.
- Допустимые переходы статуса: `new → pending_review → reviewed | review_failed`; `reviewed → pending_review` (`--recheck`); `review_failed → pending_review` (повтор). Всё остальное БД запрещает триггером.
- Ошибка запроса (таймаут, 429, 5xx, плохой ответ) переводит профиль в `review_failed`, остальные продолжаются. Неизвестная категория или невалидный JSON дают `other` с пометкой и тоже `review_failed`.
- Ошибка доступа 401/403 останавливает запуск целиком; профиль, на котором она произошла, остаётся как был. Проверьте ключ и `LLM_PROXY_URL`.
- Воркеры не берут один профиль дважды (`FOR UPDATE SKIP LOCKED`), поэтому можно запускать несколько процессов параллельно.

После запуска смотрите `classify --stats`: распределение по категориям, среднее время на профиль и расход запросов и токенов по дням.

## 9. Сквозной сценарий «с нуля до категорий»

```bash
# 0. подготовка
uv run scripts/cli.py db migrate
uv run scripts/accounts.py check

# 1. сбор профилей из списка вместе с постами под ветками
uv run scripts/run.py --since 1m --comments --depth 2

# 2. поиск по ключевым словам
uv run scripts/search.py --file data/threads_queries.txt --since 1m

# 3. кандидаты в новые профили: сначала посмотреть, потом завести
uv run scripts/cli.py discover --since 1m --dry-run
uv run scripts/cli.py discover --since 1m --min-mentions 2

# 4. ветки новых профилей (быстро)
uv run scripts/run.py --source authors --threads-only --since 1m
uv run scripts/run.py --source search --threads-only --since 1m

# 5. классификация: сначала пробный прогон
uv run scripts/cli.py classify --limit 5
uv run scripts/cli.py classify --stats
uv run scripts/cli.py classify --limit 200 --workers 4

# 6. повтор неудачных
uv run scripts/cli.py classify --limit 50 --source review_failed
```

По желанию разберите ветки отобранных профилей глубже: `uv run scripts/run.py --source <ники> --fill-threads --since 30d --comments`.

## 10. Регулярная работа

Пример ежедневного цикла (cron на сервере):

```bash
# 03:00 обновить профили из списка (якорь остановит обход на уже известном)
0 3 * * *  cd /path/threads-parser && uv run scripts/run.py --headless --since 7d --comments
# 04:30 поиск за последнюю неделю
30 4 * * *  cd /path/threads-parser && uv run scripts/search.py --file data/threads_queries.txt --since 7d --headless
# 05:30 новые авторы и классификация небольшими порциями
30 5 * * *  cd /path/threads-parser && uv run scripts/cli.py discover --since 7d --min-mentions 2
45 5 * * *  cd /path/threads-parser && uv run scripts/cli.py classify --limit 100
```

Перенос на сервер: у себя `accounts.py add N` и `accounts.py export`, на сервере `accounts.py import <архив>`, затем `accounts.py check --headless`. Куки равноценны паролю: не коммитьте их и передавайте безопасно.

## 11. Как посмотреть результат

Отчёты из командной строки:

```bash
uv run scripts/accounts.py list          # аккаунты и их статистика
uv run scripts/search.py --stats         # поиск
uv run scripts/cli.py discover --stats   # очередь профилей на проверку, ветки без разбора
uv run scripts/cli.py classify --stats   # категории, время, расход LLM
uv run scripts/cli.py db status          # миграции и размеры таблиц
```

Полезные SQL-запросы (схема `threads`):

```sql
-- состояние профилей по происхождению и статусу
SELECT discovered_via, review_status, count(*) FROM threads.profiles GROUP BY 1, 2 ORDER BY 1, 2;

-- профили с категориями
SELECT p.username, c.name_ru, p.category_raw->>'confidence' AS conf, p.category_raw->>'reason' AS reason
FROM threads.profiles p JOIN threads.categories c ON c.id = p.category_id
ORDER BY p.id DESC LIMIT 50;

-- последние запуски по типам
SELECT id, kind, status, started_at, settings FROM threads.runs ORDER BY id DESC LIMIT 20;
```

## 12. Частые проблемы

| Симптом | Причина и действие |
| --- | --- |
| «Не задан DATABASE\_URL» | заполните `DATABASE_URL` в `scripts/.env` |
| «нет колонок поиска по авторам» или `--source search/authors` не работает | не применена миграция 003: `db migrate` |
| `runs.kind не допускает discover/classify` | не применена миграция 004: `db migrate` |
| «Нет таблицы categories» | не применена миграция 005: `db migrate` |
| Не заданы `LLM_PROXY_URL`, `LLM_PROXY_API_KEY`, `LLM_MODEL` | добавьте в `scripts/.env` |
| Запуск `classify` остановлен: доступ отклонён | неверный ключ или адрес; профиль остался в прежнем статусе, после исправления запустите снова |
| Все профили `review_failed` | проверьте URL и формат API (нужен OpenAI chat/completions), затем `--source review_failed` |
| `discover` ничего не находит | нет собранных постов под ветками (запускайте парсинг с `--comments`) или слишком короткий `--since` |
| Профиль с категорией `other` и почти пустым контекстом | у профиля нет веток: сначала `run.py --source authors --threads-only`, затем `classify --recheck` |
| Много 429 или слетают сессии | уменьшите `--workers`, увеличьте `--delay` и `--profile-delay`, добавьте аккаунты, проверьте `accounts.py check`, при необходимости `relogin` |
| `--fill-threads` ничего не делает | у профиля нет неразобранных веток за указанный `--since` |
| Профиль не нашёлся (`not_found`) | ник неверный или профиль закрыт; проверьте в браузере |

## 13. Шпаргалка

| Задача | Команда |
| --- | --- |
| Проверить сессии | `accounts.py check` |
| Один профиль, пробно | `run.py <ник> --limit 5` |
| Все профили из файла | `run.py --since 1m --comments` |
| Только список веток | `run.py --threads-only --since 7d` |
| Разобрать собранные ветки | `run.py <ник> --fill-threads --since 7d --comments` |
| Профили из БД | `run.py --source new\|all\|search\|authors\|ники` |
| Поиск | `search.py --file data/threads_queries.txt --since 7d` |
| Кандидаты в профили | `cli.py discover --since 7d --dry-run` |
| Завести профили | `cli.py discover --since 7d --min-mentions 2` |
| Классификация | `cli.py classify --limit 50` |
| Повтор неудачных | `cli.py classify --limit 50 --source review_failed` |
| Переклассификация | `cli.py classify --limit 50 --recheck` |
| Отчёты | `discover --stats`, `classify --stats`, `search.py --stats` |

*Примечание.* Шаги 4–6 (`discover`, `classify`, миграции 003–005) на момент написания проверены чтением кода и тестами отдельных частей; полный прогон на вашей БД делайте сначала на копии и малыми порциями.
