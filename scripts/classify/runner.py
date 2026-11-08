"""Воркеры классификации профилей: выборка партии, запись категории, учёт в runs.

Профиль берётся `SELECT ... FOR UPDATE SKIP LOCKED` внутри транзакции, которая живёт до записи
результата: второй воркер (и второй процесс) его не возьмёт, а при падении транзакция
откатывается и профиль остаётся в прежнем статусе. Пул нужен размером не меньше числа воркеров.
Ошибки proxy API (таймаут, 429, 5xx, невалидный ответ) пишут category_raw.error, профиль
считается неудачным (source=failed) и цикл идёт дальше; 401/403 останавливают запуск целиком.
Статуса проверки нет: «новый» = category_id пуст и ошибки нет, «проверенный» = категория есть
и ошибки нет, «неудачный» = category_raw.error не пуст.
"""

import asyncio
from dataclasses import dataclass, field

import asyncpg

from classify.classifier import FALLBACK, classify_profile
from classify.client import LLMAuthError, LLMClient, LLMError
from classify.prompts import PROMPT_VERSION
from config import Settings
from db.pool import create_pool
from db.runs import finish_run, start_run

# source -> условие отбора (category_raw->>'error' пуст и у JSON null, и при отсутствии ключа)
SOURCE_WHERE = {
    "new": "category_id IS NULL AND (category_raw->>'error') IS NULL",
    "failed": "(category_raw->>'error') IS NOT NULL",
    "reviewed": "category_id IS NOT NULL AND (category_raw->>'error') IS NULL",  # --recheck
}
CLAIM_SQL = """
SELECT id, username, og_title, og_description
FROM profiles
WHERE is_target AND {where} AND id <> ALL($1::bigint[])
ORDER BY id
LIMIT 1
FOR UPDATE SKIP LOCKED
"""
THREADS_SQL = """
SELECT text FROM posts
WHERE profile_id = $1 AND coalesce(text, '') <> ''
ORDER BY date_utc DESC NULLS LAST, code
LIMIT $2
"""
SAVE_SQL = """
UPDATE profiles SET
    category_id = (SELECT id FROM categories WHERE code = $2),
    category_raw = $3::jsonb, category_model = $4, category_prompt_version = $5
WHERE id = $1
"""
FAIL_SQL = """
UPDATE profiles SET
    category_raw = $2::jsonb, category_model = $3, category_prompt_version = $4
WHERE id = $1
"""


@dataclass
class Context:
    client: LLMClient
    categories: list[tuple[str, str, str | None]]
    source: str
    sample: int
    remaining: int
    sem: asyncio.Semaphore
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    seen: list[int] = field(default_factory=list)  # уже взятые в этом запуске
    stats: dict = field(
        default_factory=lambda: {
            "processed": 0,
            "reviewed": 0,
            "failed": 0,
            "requests": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms_sum": 0,
        }
    )


async def _process(conn: asyncpg.Connection, ctx: Context, row: asyncpg.Record) -> None:
    pid, model = row["id"], ctx.client.model
    texts = [r["text"] for r in await conn.fetch(THREADS_SQL, pid, ctx.sample)]
    ctx.stats["processed"] += 1
    try:
        async with ctx.sem:
            res = await classify_profile(ctx.client, ctx.categories, dict(row), texts)
    except LLMAuthError:
        ctx.stats["processed"] -= 1
        ctx.stop.set()
        raise  # транзакция откатится, профиль останется как был
    except LLMError as e:
        ctx.stats["failed"] += 1
        ctx.stats["requests"] += e.requests
        raw = {"error": str(e)[:300], "requests": e.requests, "prompt_version": PROMPT_VERSION}
        await conn.execute(FAIL_SQL, pid, raw, model, PROMPT_VERSION)
        return
    ctx.stats["requests"] += res.raw["requests"]
    ctx.stats["prompt_tokens"] += res.raw["prompt_tokens"]
    ctx.stats["completion_tokens"] += res.raw["completion_tokens"]
    ctx.stats["latency_ms_sum"] += res.raw["latency_ms"]
    ctx.stats["failed" if res.error else "reviewed"] += 1
    await conn.execute(SAVE_SQL, pid, res.category, res.raw, model, PROMPT_VERSION)


async def _worker(pool: asyncpg.Pool, ctx: Context) -> None:
    while not ctx.stop.is_set() and ctx.remaining > 0:
        ctx.remaining -= 1
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(CLAIM_SQL.format(where=SOURCE_WHERE[ctx.source]), ctx.seen)
            if row is None:
                return
            ctx.seen.append(row["id"])
            await _process(conn, ctx, row)


async def _load_categories(conn: asyncpg.Connection) -> list[tuple[str, str, str | None]]:
    try:
        rows = await conn.fetch(
            "SELECT code, name_ru, description FROM categories WHERE is_active ORDER BY id"
        )
    except asyncpg.UndefinedTableError as e:
        raise SystemExit(
            "Нет таблицы categories. Примените миграции: uv run scripts/cli.py db migrate"
        ) from e
    cats = [(r["code"], r["name_ru"], r["description"]) for r in rows]
    if FALLBACK not in {c[0] for c in cats}:
        raise SystemExit(
            f"В categories нет активной категории «{FALLBACK}»: на неё падает неизвестный ответ"
        )
    return cats


async def run_classify(st: Settings, source: str, limit: int, workers: int, recheck: bool) -> dict:
    """Классифицирует до limit профилей из выборки source (new | failed | reviewed); возвращает итоги запуска."""
    client = LLMClient(
        st.llm_proxy_url,
        st.llm_proxy_api_key,
        st.llm_model,
        st.llm_timeout_sec,
        st.llm_max_retries,
    )
    workers = max(1, min(workers, limit))
    settings = {
        "source": source,
        "limit": limit,
        "workers": workers,
        "recheck": recheck,
        "model": st.llm_model,
        "prompt_version": PROMPT_VERSION,
        "sample_threads": st.llm_sample_threads,
    }
    pool = await create_pool(st, min_size=1, max_size=workers + 1)
    try:
        async with pool.acquire() as conn:
            categories = await _load_categories(conn)
            try:
                run_id = await start_run(conn, "classify", settings)
            except asyncpg.CheckViolationError as e:
                raise SystemExit(
                    "runs.kind не допускает classify. Примените миграцию 004: "
                    "uv run scripts/cli.py db migrate"
                ) from e
        ctx = Context(
            client,
            categories,
            source,
            st.llm_sample_threads,
            limit,
            asyncio.Semaphore(workers),
        )
        status, error = "ok", None
        try:
            results = await asyncio.gather(
                *(_worker(pool, ctx) for _ in range(workers)), return_exceptions=True
            )
        except asyncio.CancelledError:
            status = "interrupted"
            raise
        finally:
            if status != "ok":
                await _close(pool, run_id, status, ctx, None)
        failures = [r for r in results if isinstance(r, BaseException)]
        if failures:
            status, error = "error", f"{type(failures[0]).__name__}: {failures[0]}"[:500]
        await _close(pool, run_id, status, ctx, error)
        summary = _summary(ctx)
        summary["error"] = error
        summary["auth_error"] = any(isinstance(f, LLMAuthError) for f in failures)
        return summary
    finally:
        await pool.close()


def _summary(ctx: Context) -> dict:
    s = dict(ctx.stats)
    done = s["reviewed"] + s["failed"]
    s["avg_latency_ms"] = round(s.pop("latency_ms_sum") / done) if done else None
    return s


async def _close(pool, run_id: int, status: str, ctx: Context, error: str | None) -> None:
    async with pool.acquire() as conn:
        await finish_run(conn, run_id, status, _summary(ctx), error)
