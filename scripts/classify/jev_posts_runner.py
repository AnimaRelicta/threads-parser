"""Классификация постов авторов из Казахстана через Jev: отбор (dry-run) и полный прогон.

Отбор: posts JOIN profiles по нику автора (без учёта регистра), profiles.about_location =
«Казахстан», автор не забанен, у поста есть posts.text не короче --min-chars. Без --recheck
посты с category_id пропускаются. Для классификации берётся только posts.text.

Запись: posts.category_id, category_raw (все вероятности, пороги, причина «Прочее», токены),
category_model, category_prompt_version (миграция 017). Ошибка запроса пост не меняет: он
остаётся без категории и попадёт в следующий запуск. Защита от ложного класса та же, что у
профилей (classify/jev.py): слабый ответ = other.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass, field

import asyncpg

from classify.jev import Decision, Gate, build_questions, decide
from classify.jev_client import JevClient, JevError, JevFatalError
from classify.jev_runner import _load_categories, _raw
from config import Settings
from db.pool import create_pool
from db.runs import finish_run, start_run

POST_PROMPT_VERSION = 201  # посты Jev; профили Jev = 101, LLM-классификатор профилей = 1
COUNTRY_SQL = "казахстан"  # сравнение: lower(btrim(profiles.about_location))
MAX_CHARS = 3000  # длиннее текст режется: категорию задают первые абзацы
DEFAULT_MIN_CHARS = 10
CHARS_PER_TOKEN = 3.0
PRICE_PER_MTOK = 0.042

INSTRUCTIONS = (
    "Определи категорию автора по тексту его поста в Threads. Выбери ровно один вариант. "
    "Если по тексту нельзя уверенно отнести автора ни к одной категории (личное мнение, "
    'шутка, вопрос без признаков продавца или канала, слишком мало данных), выбери "other"; '
    "не подгоняй пост под ближайшую категорию."
)
NOUL_INSTRUCTIONS = (
    "Из текста поста явно видно, что автор магазин или бренд, барахолка, перекупщик, "
    "новостной канал, домовой чат или услуги. Ответ нет, если текст общий, личный, шуточный "
    "или слишком короткий."
)

FROM_WHERE = f"""
FROM posts po
JOIN profiles pr ON lower(pr.username) = lower(po.author_username)
WHERE lower(btrim(pr.about_location)) = '{COUNTRY_SQL}' AND NOT pr.is_banned
"""
USABLE = "length(btrim(coalesce(po.text, ''))) >= $1"
TODO = "AND (po.category_id IS NULL OR $2::boolean)"

COUNT_SQL = f"""
SELECT count(*) AS kz,
       count(*) FILTER (WHERE btrim(coalesce(po.text, '')) <> '') AS with_text,
       count(*) FILTER (WHERE {USABLE}) AS usable,
       count(*) FILTER (WHERE {USABLE} AND po.category_id IS NOT NULL) AS classified,
       count(DISTINCT po.author_username) FILTER (WHERE {USABLE}) AS authors,
       count(*) FILTER (WHERE {USABLE} {TODO}) AS todo,
       coalesce(sum(least(length(btrim(po.text)), {MAX_CHARS}))
                FILTER (WHERE {USABLE} {TODO}), 0) AS chars
{FROM_WHERE}
"""
ROWS_SQL = f"""
SELECT po.code, po.author_username, btrim(po.text) AS text
{FROM_WHERE} AND {USABLE} {TODO}
ORDER BY po.date_utc DESC NULLS LAST, po.code
LIMIT $3
"""
SAVE_SQL = """
UPDATE posts SET
    category_id = (SELECT id FROM categories WHERE code = $2),
    category_raw = $3::jsonb, category_model = $4, category_prompt_version = $5
WHERE code = $1
"""


def post_state(text: str) -> str:
    """Контекст для Jev: только posts.text (обрезанный)."""
    return text[:MAX_CHARS]


async def dry_run(conn: asyncpg.Connection, recheck: bool, min_chars: int, show: int) -> None:
    total = await conn.fetchval("SELECT count(*) FROM posts")
    c = await conn.fetchrow(COUNT_SQL, min_chars, recheck)
    tokens = int(c["chars"] / CHARS_PER_TOKEN)
    print(f"[*] Постов в базе всего:                         {total}")
    print(f"[*] Из них авторы с about_location = Казахстан:   {c['kz']}")
    print(f"    без текста:                                   {c['kz'] - c['with_text']}")
    print(f"    текст короче {min_chars} символов (пропуск):  {c['with_text'] - c['usable']}")
    print(f"[+] Подходят под критерии: {c['usable']} постов, авторов: {c['authors']}")
    skipped = "" if recheck else f", уже с категорией (пропуск без --recheck): {c['classified']}"
    print(f"[+] К обработке: {c['todo']}{skipped}")
    print(
        f"[*] Оценка: ~{tokens} токенов текста (без описаний категорий, они добавляются к "
        f"каждому запросу); при ${PRICE_PER_MTOK}/Мток это копейки"
    )
    for r in await conn.fetch(ROWS_SQL, min_chars, recheck, show):
        print(f"\n--- {r['code']} @{r['author_username']}\n{post_state(r['text'])[:500]}")


@dataclass
class Context:
    client: JevClient
    gate: Gate
    questions: dict
    queue: asyncio.Queue
    codes: set[str]
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    fatal: str | None = None
    stats: Counter = field(default_factory=Counter)
    by_category: Counter = field(default_factory=Counter)
    by_reason: Counter = field(default_factory=Counter)


async def _worker(pool: asyncpg.Pool, ctx: Context) -> None:
    st = ctx.stats
    while not ctx.stop.is_set():
        try:
            row = ctx.queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        state = post_state(row["text"])
        try:
            resp = await ctx.client.ask(state, ctx.questions)
        except JevFatalError as e:
            ctx.fatal = str(e)
            ctx.stop.set()
            return
        except JevError as e:
            st["failed"] += 1
            st["requests"] += e.requests
            continue  # пост не тронут: попадёт в следующий запуск
        d: Decision = decide(resp.answers, ctx.codes, ctx.gate)
        raw = _raw(d, resp, ctx.gate, len(state), POST_PROMPT_VERSION)
        await pool.execute(SAVE_SQL, row["code"], d.category, raw, resp.model, POST_PROMPT_VERSION)
        st["processed"] += 1
        st["requests"] += resp.requests
        st["input_tokens"] += resp.input_tokens
        st["output_tokens"] += resp.output_tokens
        st["latency_ms_sum"] += resp.latency_ms
        ctx.by_category[d.category] += 1
        if d.reason:
            ctx.by_reason[d.reason] += 1


def _summary(ctx: Context) -> dict:
    s = dict(ctx.stats)
    done = s.get("processed", 0)
    s["avg_latency_ms"] = round(s.pop("latency_ms_sum", 0) / done) if done else None
    s["by_category"] = dict(ctx.by_category)
    s["by_reason"] = dict(ctx.by_reason)
    return s


async def _close(pool, run_id: int, status: str, ctx: Context, error: str | None) -> None:
    async with pool.acquire() as conn:
        await finish_run(conn, run_id, status, _summary(ctx), error)


async def run_jev_posts(
    st: Settings,
    gate: Gate,
    limit: int | None,
    workers: int,
    recheck: bool,
    dry: bool,
    show: int,
    min_chars: int,
) -> dict | None:
    """dry=True: только отбор и отчёт, без запросов и записи. Иначе полный прогон."""
    pool = await create_pool(st, min_size=1, max_size=workers + 1)
    try:
        async with pool.acquire() as conn:
            try:
                categories = await _load_categories(conn)
                if dry:
                    await dry_run(conn, recheck, min_chars, show)
                    return None
                rows = await conn.fetch(ROWS_SQL, min_chars, recheck, limit or 2**31 - 1)
            except asyncpg.UndefinedColumnError as e:
                raise SystemExit(
                    "Нет колонок категории в posts. Примените миграцию 017: "
                    "uv run scripts/cli.py db migrate"
                ) from e
            print(
                f"[*] К обработке постов: {len(rows)} "
                f"(модель {st.jev_model}, воркеров {min(workers, max(1, len(rows)))})"
            )
            if not rows:
                return {"processed": 0}
            run_id = await start_run(
                conn,
                "classify",
                {
                    "engine": "jev_posts",
                    "country": "KZ",
                    "model": st.jev_model,
                    "min_version": st.jev_min_version,
                    "limit": limit,
                    "workers": workers,
                    "recheck": recheck,
                    "min_chars": min_chars,
                    "gate": vars(gate),
                    "prompt_version": POST_PROMPT_VERSION,
                },
            )
        client = JevClient(
            st.jev_url,
            st.jev_api_key,
            st.jev_model,
            st.jev_min_version,
            st.jev_timeout_sec,
            st.jev_max_retries,
            st.jev_proxy,
        )
        queue: asyncio.Queue = asyncio.Queue()
        for r in rows:
            queue.put_nowait(r)
        ctx = Context(
            client,
            gate,
            build_questions(categories, gate.noul_gate, INSTRUCTIONS, NOUL_INSTRUCTIONS),
            queue,
            {c[0] for c in categories},
        )
        status, error = "ok", None
        try:
            results = await asyncio.gather(
                *(_worker(pool, ctx) for _ in range(min(workers, len(rows)))),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        finally:
            if status != "ok":
                await _close(pool, run_id, status, ctx, None)
        crashed = [r for r in results if isinstance(r, BaseException)]
        if ctx.fatal:
            status, error = "error", ctx.fatal[:500]
        elif crashed:
            status, error = "error", f"{type(crashed[0]).__name__}: {crashed[0]}"[:500]
        await _close(pool, run_id, status, ctx, error)
        out = _summary(ctx)
        out.update(error=error, fatal=bool(ctx.fatal), left=ctx.queue.qsize())
        return out
    finally:
        await pool.close()
