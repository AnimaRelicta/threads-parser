"""Классификация профилей КЗ через Jev: отбор (dry-run) и полный прогон.

Отбор: активные профили (is_target) -> страна КЗ по итогу core/geo.resolve (тот же
расчёт, что у `cli.py geo`) -> есть хоть что-то из about_name / bio|og_description / topics.
Без --recheck профили, у которых category_id уже есть, пропускаются.

Запись: category_id, category_raw (все вероятности, пороги, причина «Прочее», токены),
category_model (версия, которая ответила), category_prompt_version, review_status = reviewed
(через pending_review, как требует триггер переходов). Ошибка запроса = review_failed.
"""

import asyncio
from collections import Counter
from dataclasses import dataclass, field

import asyncpg

from classify.jev import (
    FALLBACK,
    PROMPT_VERSION,
    Decision,
    Gate,
    build_questions,
    build_state,
    decide,
)
from classify.jev_client import JevClient, JevError, JevFatalError
from config import Settings
from db.geo import load_profile_geo
from db.pool import create_pool
from db.runs import finish_run, start_run

KZ = "KZ"
PRICE_PER_MTOK = 0.042  # $ за миллион токенов (docs.typesafe.ai/models)
CHARS_PER_TOKEN = 3.0  # грубо для русского текста; только для оценки в dry-run

ROWS_SQL = """
SELECT id, username, about_name, bio, og_description, topics, category_id
FROM profiles
WHERE is_target AND id = ANY($1::bigint[])
ORDER BY id
"""
SAVE_SQL = """
UPDATE profiles SET
    category_id = (SELECT id FROM categories WHERE code = $2),
    category_raw = $3::jsonb, category_model = $4, category_prompt_version = $5,
    review_status = 'reviewed'
WHERE id = $1
"""
FAIL_SQL = """
UPDATE profiles SET
    review_status = 'review_failed', category_raw = $2::jsonb,
    category_model = $3, category_prompt_version = $4
WHERE id = $1
"""
# триггер допускает new|reviewed|review_failed -> pending_review -> reviewed|review_failed
TO_PENDING_SQL = (
    "UPDATE profiles SET review_status = 'pending_review' "
    "WHERE id = $1 AND review_status <> 'pending_review'"
)


@dataclass
class Candidate:
    id: int
    username: str
    state: str
    classified: bool


@dataclass
class Selection:
    active: int = 0  # активных профилей всего
    kz: int = 0  # из них КЗ
    no_text: int = 0  # КЗ без имени, описания и тем: нечем классифицировать
    with_text: list[Candidate] = field(default_factory=list)
    name: int = 0
    bio: int = 0
    og_description: int = 0
    topics: int = 0

    @property
    def classified(self) -> int:
        return sum(c.classified for c in self.with_text)

    def todo(self, recheck: bool) -> list[Candidate]:
        return [c for c in self.with_text if recheck or not c.classified]


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
        raise SystemExit(f"В categories нет активной категории «{FALLBACK}»")
    return cats


async def select_profiles(conn: asyncpg.Connection) -> Selection:
    """Фильтр КЗ, затем поля профиля: about_name, bio (иначе og_description), topics."""
    sel = Selection()
    sel.active = await conn.fetchval(
        "SELECT count(*) FROM profiles WHERE is_target"
    )
    kz_ids = [g.id for g in await load_profile_geo(conn) if g.resolved.country == KZ]
    for r in await conn.fetch(ROWS_SQL, kz_ids):
        sel.kz += 1
        state, info = build_state(r)
        if state is None:
            sel.no_text += 1
            continue
        sel.name += info["name"]
        sel.bio += info["bio_src"] == "bio"
        sel.og_description += info["bio_src"] == "og_description"
        sel.topics += bool(info["topics"])
        sel.with_text.append(Candidate(r["id"], r["username"], state, r["category_id"] is not None))
    return sel


def dry_run(sel: Selection, recheck: bool, show: int) -> None:
    todo = sel.todo(recheck)
    chars = sum(len(c.state) for c in todo)
    tokens = int(chars / CHARS_PER_TOKEN)
    print(f"[*] Активных профилей (is_target): {sel.active}")
    print(f"[*] Из них Казахстан (итог geo.resolve = KZ):    {sel.kz}")
    print(f"    без имени, описания и тем (пропуск):         {sel.no_text}")
    print(f"[+] Подходят под критерии:                       {len(sel.with_text)}")
    print(
        f"    about_name есть: {sel.name}; bio: {sel.bio}; "
        f"og_description вместо bio: {sel.og_description}; topics есть: {sel.topics}"
    )
    skipped = "" if recheck else f", уже с категорией (пропуск без --recheck): {sel.classified}"
    print(f"[+] К обработке: {len(todo)}{skipped}")
    print(
        f"[*] Оценка контекста: ~{tokens} токенов профилей (без описаний категорий, "
        f"они добавляются к каждому запросу); при ${PRICE_PER_MTOK}/Мток это копейки"
    )
    for c in todo[:show]:
        print(f"\n--- @{c.username} (id {c.id})\n{c.state}")


@dataclass
class Context:
    client: JevClient
    categories: list[tuple[str, str, str | None]]
    gate: Gate
    questions: dict
    queue: asyncio.Queue
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    fatal: str | None = None
    codes: set[str] = field(default_factory=set)
    stats: Counter = field(default_factory=Counter)
    by_category: Counter = field(default_factory=Counter)
    by_reason: Counter = field(default_factory=Counter)


def _raw(
    d: Decision, resp, gate: Gate, state_chars: int, prompt_version: int = PROMPT_VERSION
) -> dict:
    return {
        "engine": "jev",
        "category": d.category,
        "top": d.top,  # класс с наибольшей вероятностью до защиты
        "top_prob": d.top_prob,
        "fallback_reason": d.reason,  # None = класс принят; иначе почему «Прочее»
        "confidence": d.confidence,
        "noul": d.noul,
        "probabilities": d.probabilities,
        "gate": {
            "min_prob": gate.min_prob,
            "min_confidence": gate.min_confidence,
            "noul_gate": gate.noul_gate,
            "noul_min": gate.noul_min,
        },
        "model": resp.model,
        "state_chars": state_chars,
        "requests": resp.requests,
        "input_tokens": resp.input_tokens,
        "output_tokens": resp.output_tokens,
        "latency_ms": resp.latency_ms,
        "prompt_version": prompt_version,
    }


async def _worker(pool: asyncpg.Pool, ctx: Context) -> None:
    st, model = ctx.stats, ctx.client.model
    while not ctx.stop.is_set():
        try:
            cand: Candidate = ctx.queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        try:
            resp = await ctx.client.ask(cand.state, ctx.questions)
        except JevFatalError as e:
            ctx.fatal = str(e)
            ctx.stop.set()
            return
        except JevError as e:
            st["failed"] += 1
            st["requests"] += e.requests
            raw = {"engine": "jev", "error": str(e)[:300], "prompt_version": PROMPT_VERSION}
            async with pool.acquire() as conn, conn.transaction():
                await conn.execute(TO_PENDING_SQL, cand.id)
                await conn.execute(FAIL_SQL, cand.id, raw, model, PROMPT_VERSION)
            continue
        d = decide(resp.answers, ctx.codes, ctx.gate)
        raw = _raw(d, resp, ctx.gate, len(cand.state))
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(TO_PENDING_SQL, cand.id)
            await conn.execute(SAVE_SQL, cand.id, d.category, raw, resp.model, PROMPT_VERSION)
        st["processed"] += 1
        st["requests"] += resp.requests
        st["input_tokens"] += resp.input_tokens
        st["output_tokens"] += resp.output_tokens
        st["latency_ms_sum"] += resp.latency_ms
        ctx.by_category[d.category] += 1
        if d.reason:
            ctx.by_reason[d.reason] += 1


async def run_jev(
    st: Settings, gate: Gate, limit: int | None, workers: int, recheck: bool, dry: bool, show: int
) -> dict | None:
    """dry=True: только отбор и отчёт, без запросов и записи. Иначе полный прогон."""
    pool = await create_pool(st, min_size=1, max_size=workers + 1)
    try:
        async with pool.acquire() as conn:
            categories = await _load_categories(conn)
            sel = await select_profiles(conn)
            if dry:
                dry_run(sel, recheck, show)
                return None
            todo = sel.todo(recheck)[: limit or None]
            print(
                f"[*] КЗ: {sel.kz}, подходят: {len(sel.with_text)}, к обработке: {len(todo)} "
                f"(модель {st.jev_model}, воркеров {min(workers, max(1, len(todo)))})"
            )
            if not todo:
                return {"processed": 0}
            settings = {
                "engine": "jev",
                "country": KZ,
                "model": st.jev_model,
                "min_version": st.jev_min_version,
                "limit": limit,
                "workers": workers,
                "recheck": recheck,
                "gate": vars(gate),
                "prompt_version": PROMPT_VERSION,
            }
            run_id = await start_run(conn, "classify", settings)
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
        for c in todo:
            queue.put_nowait(c)
        ctx = Context(
            client,
            categories,
            gate,
            build_questions(categories, gate.noul_gate),
            queue,
            codes={c[0] for c in categories},
        )
        status, error = "ok", None
        try:
            results = await asyncio.gather(
                *(_worker(pool, ctx) for _ in range(min(workers, len(todo)))),
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
        out["error"] = error
        out["fatal"] = bool(ctx.fatal)
        out["by_category"] = dict(ctx.by_category)
        out["by_reason"] = dict(ctx.by_reason)
        out["left"] = ctx.queue.qsize()
        return out
    finally:
        await pool.close()


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
