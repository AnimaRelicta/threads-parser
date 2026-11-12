"""Очередь профилей и воркеры n × m для Instagram; точка запуска run()."""

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from config import IgSettings
from core.logs import PSTAT, TAG, log
from db.pool import DB_ERRORS, create_pool, run_sync
from ig.browser.account import IgAccount
from ig.consts import BLOCKED_RESOURCES, IG_SCHEMA
from ig.db.runs import finalize_run, start_run
from ig.db.store import IgStore
from ig.registry.store import now_iso, pick_accounts
from ig.scrape.fill import run_fill
from ig.scrape.full import run_full
from ig.scrape.meta import run_profile_only
from ig.scrape.posts_light import run_posts_only
from ig.scrape.report import new_result, print_summary, profile_run_entry, write_summary
from ig.scrape.select import select_profiles

MODE_TITLES = {
    "profile": "--profile-only: только метаданные профиля",
    "posts": "--posts-only: посты без комментариев и медиа",
    "full": "полный проход: посты + комментарии и медиа по флагам",
    "fill": "--fill: добор постов, собранных --posts-only",
}
DONE_STATUSES = ("ok", "empty", "private", "not_found")  # 429 и ошибки браузера/БД обработкой не считаются


class Dispatcher:
    """Общая очередь профилей для всех воркеров всех аккаунтов.

    active считает профили в работе: профиль может вернуться в очередь (429 на другом
    аккаунте), поэтому пустая очередь ещё не конец. Всё в одном потоке цикла, блокировок нет."""

    def __init__(self, names: list[str]):
        self.queue: deque = deque((n, 0) for n in names)
        self.active = 0

    async def take(self, halt: asyncio.Event):
        """Следующий (профиль, число попыток) или None, когда работы больше не будет."""
        while not halt.is_set():
            if self.queue:
                self.active += 1  # остаётся +1 до done()
                return self.queue.popleft()
            if self.active == 0:
                return None
            await asyncio.sleep(1)  # другой аккаунт может вернуть профиль
        return None

    def put(self, job) -> None:
        self.queue.append(job)

    def done(self) -> None:
        self.active -= 1


@dataclass
class RunState:
    """Итоги запуска: результаты профилей и то, что надо записать в БД по аккаунтам."""

    got: dict = field(default_factory=dict)  # ник из очереди -> результат
    touched: dict = field(default_factory=dict)  # аккаунт -> {"status", "error"}
    stats: dict = field(default_factory=dict)  # аккаунт -> {"e429", "eauth"}
    nicks: dict = field(default_factory=dict)  # аккаунт -> ник
    entries: dict = field(default_factory=dict)  # аккаунт -> [запись для profile_runs]
    ids: dict = field(default_factory=dict)  # ник -> profiles.id (общий словарь с IgStore)
    store: IgStore | None = None

    def add_stat(self, account: str, **kw) -> None:
        s = self.stats.setdefault(account, {})
        for k, v in kw.items():
            s[k] = s.get(k, 0) + v


async def block_heavy(route) -> None:
    """Картинки, видео и шрифты вкладке с метаданными не нужны."""
    try:
        if route.request.resource_type in BLOCKED_RESOURCES:
            await route.abort()
        else:
            await route.continue_()
    except Exception:
        pass


async def handle_abort(
    res: dict, acc: IgAccount, st: IgSettings, state: RunState, halt: asyncio.Event
) -> None:
    """Проблема на уровне аккаунта: все его воркеры останавливаются; 429 ставит паузу аккаунта."""
    halt.set()
    note = res.get("note", "")
    auth = res.get("abort_kind") == "auth"
    state.touched[acc.name] = {"status": "needs_login" if auth else None, "error": note[:200]}
    if not auth and st.pause_429:
        try:
            await state.store.set_cooldown(acc.name, st.pause_429)
            log(f"[!] Аккаунт {acc.name} отдыхает {st.pause_429} с (IG_429_PAUSE)")
        except DB_ERRORS as e:
            log(f"[!] Пауза аккаунта не записана: {str(e)[:150]}")


PROCESSED = {"profile": "profile", "posts": "posts", "full": "full", "fill": "full"}
RUNNERS = {
    "profile": run_profile_only,
    "posts": run_posts_only,
    "full": run_full,
    "fill": run_fill,
}


def mode_of(args) -> str:
    """Режим запуска: profile | posts | fill | full (полный проход — по умолчанию)."""
    for flag, mode in (("profile_only", "profile"), ("posts_only", "posts"), ("fill", "fill")):
        if getattr(args, flag, False):
            return mode
    return "full"


async def process_job(acc, page, name, args, st, state) -> dict:
    """Один профиль: результат всегда словарь (ошибка браузера или БД — статус error)."""
    mode = mode_of(args)
    try:
        return await RUNNERS[mode](page, name, args, state.store, acc.name)
    except (PlaywrightError, OSError, *DB_ERRORS) as e:
        is_db = not isinstance(e, PlaywrightError)
        log(f"[!] Ошибка {'БД' if is_db else 'браузера'}: {str(e)[:200]}")
        try:
            await state.store.mark_error(name)
        except DB_ERRORS:
            pass
        return new_result(name, mode, "error", "ошибка БД" if is_db else "ошибка браузера")


async def worker_main(
    wid: int,
    acc: IgAccount,
    page,
    args,
    st: IgSettings,
    disp: Dispatcher,
    state: RunState,
    halt: asyncio.Event,
    n_accounts: int,
    stagger: float,
) -> None:
    """Одна вкладка аккаунта берёт профили из общей очереди (в --profile-only вкладки разные)."""
    TAG.set(f"{acc.name}/w{wid}")
    try:
        if stagger:
            await asyncio.sleep(stagger)  # разносим старт воркеров
        if page is None:
            page = await acc.ctx.new_page()
        await page.route("**/*", block_heavy)
        first = True
        while True:
            job = await disp.take(halt)
            if job is None:
                break
            name, tries = job
            try:
                if not first:
                    delay = args.profile_delay
                    await asyncio.sleep(random.uniform(delay, delay * 1.5))
                first = False
                log(f"\n=== @{name} ===")
                PSTAT.set({})
                t0, started = time.monotonic(), now_iso()
                res = await process_job(acc, page, name, args, st, state)
                state.add_stat(
                    acc.name,
                    e429=1 if res.get("abort_kind") == "429" else 0,
                    eauth=1 if res.get("abort_kind") == "auth" else 0,
                )
                res.update(
                    account=acc.name,
                    started_at=started,
                    finished_at=now_iso(),
                    duration_sec=round(time.monotonic() - t0, 1),
                )
                state.entries.setdefault(acc.name, []).append(profile_run_entry(res))
                if res["status"] == "abort":
                    await handle_abort(res, acc, st, state, halt)
                    if tries + 1 < n_accounts:
                        log(f"[!] @{name} вернётся в очередь другому аккаунту")
                        disp.put((name, tries + 1))
                        continue
                if res["status"] in DONE_STATUSES:
                    try:  # отметка сразу, не в конце запуска: переживает падение и Ctrl+C
                        await state.store.mark_processed(res["username"], PROCESSED[mode_of(args)])
                    except DB_ERRORS as e:
                        log(f"[!] Отметка «обработан» не записана: {str(e)[:150]}")
                state.got[name] = res
            finally:
                disp.done()
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def account_main(
    p, acc_name: str, index: int, args, st: IgSettings, disp, state, n_accounts: int
) -> None:
    """Жизненный цикл аккаунта: один браузер, m вкладок-воркеров, куки и закрытие в конце."""
    TAG.set(acc_name)
    acc = IgAccount(acc_name, st, args.headless)
    halt = asyncio.Event()
    m = args.tabs
    try:
        try:
            page0 = await acc.open(p)
        except SystemExit as e:
            log(f"[!] Аккаунт не запущен: {e}")
            bad = "недействительна" in str(e)
            state.add_stat(acc_name, eauth=1 if bad else 0)
            state.touched[acc_name] = {
                "status": "needs_login" if bad else None,
                "error": str(e)[:200],
            }
            return
        if acc.nick:
            state.nicks[acc_name] = acc.nick
        workers = [
            worker_main(
                w + 1,
                acc,
                page0 if w == 0 else None,
                args,
                st,
                disp,
                state,
                halt,
                n_accounts,
                (index * m + w) * random.uniform(2.0, 4.0),
            )
            for w in range(m)
        ]
        for o in await asyncio.gather(*workers, return_exceptions=True):
            if isinstance(o, Exception):
                log(f"[!] Воркер завершился с ошибкой: {type(o).__name__}: {str(o)[:200]}")
    finally:
        await acc.close()


async def main_async(accounts: list[str], names: list[str], args, st, state: RunState) -> None:
    disp = Dispatcher(names)
    # Пул на весь запуск: воркеры берут соединения на время одной записи (n × m + 2)
    pool = await create_pool(st, max_size=max(5, len(accounts) * args.tabs + 2), schema=IG_SCHEMA)
    state.store = IgStore(pool, state.ids)
    try:
        async with async_playwright() as p:
            outcomes = await asyncio.gather(
                *(
                    account_main(p, acc, i, args, st, disp, state, len(accounts))
                    for i, acc in enumerate(accounts)
                ),
                return_exceptions=True,
            )
    finally:
        await pool.close()
    for o in outcomes:
        if isinstance(o, Exception):
            log(f"[!] Аккаунт завершился с ошибкой: {type(o).__name__}: {str(o)[:200]}")


def run(args, st: IgSettings) -> None:
    """ig parse (--profile-only | --posts-only): выбор профилей, браузеры, итоги в БД и summary.json."""
    mode = mode_of(args)
    names, ids = run_sync(st, lambda conn: select_profiles(conn, args, PROCESSED[mode]), schema=IG_SCHEMA)
    if args.only_unprocessed:
        log(f"[*] --only-unprocessed: к обработке {len(names)}")
    if args.dry_run:
        log(f"[*] Пробный прогон: режим {mode}, профилей {len(names)}, браузер не запускается")
        for n in names:
            log(f"    @{n}")
        return
    if not names:
        log("[✓] Профилей для обработки нет")
        return
    accounts = asyncio.run(pick_accounts(st, args.accounts))
    log(
        f"[*] Режим {MODE_TITLES[mode]} | профилей: {len(names)} | "
        f"аккаунтов: {len(accounts)} | вкладок на аккаунт: {args.tabs} "
        f"(одновременно до {len(accounts) * args.tabs} профилей)"
    )
    state = RunState(ids=ids)
    settings = {
        "mode": mode,
        "profiles_requested": len(names),
        "accounts": accounts,
        "tabs": args.tabs,
        "headless": bool(args.headless),
        "delay": args.delay,
        "profile_delay": args.profile_delay,
        "only_unprocessed": bool(args.only_unprocessed),
        "about": not args.no_about,
        "refresh_about": bool(args.refresh_about),
    }
    if mode in ("full", "fill"):
        settings.update(
            comments=bool(args.comments),
            depth=args.depth,
            max_comments=args.max_comments,
            images=bool(args.images),
            videos=bool(args.videos),
            avatars=bool(args.avatars),
            recheck_days=args.recheck_days,
        )
    if mode in ("posts", "full"):
        settings.update(
            since=args.since,
            limit=args.limit,
            full=bool(args.full),
            stop_after=args.stop_after,
            min_posts=getattr(args, "min_posts", 0),
        )
    run_id = run_sync(st, lambda conn: start_run(conn, "parse", settings), schema=IG_SCHEMA)
    interrupted = False
    try:
        asyncio.run(main_async(accounts, names, args, st, state))
    except KeyboardInterrupt:
        interrupted = True
        log("\n[!] Остановлено пользователем: сохранённое не потеряно, браузеры закрыты.")
    finally:
        try:
            run_sync(
                st,
                lambda conn: finalize_run(
                    conn,
                    run_id,
                    accounts,
                    state.touched,
                    state.stats,
                    state.entries,
                    state.nicks,
                    state.ids,
                    interrupted,
                ),
                schema=IG_SCHEMA,
            )
        except (Exception, SystemExit) as e:
            log(f"[!] Итоги запуска не записаны в БД (запуск #{run_id}): {str(e)[:200]}")
    results = [
        state.got.get(n) or new_result(n, mode, "not_run", "не обработан: запустите ещё раз")
        for n in names
    ]
    print_summary(results)
    log(f"\n[✓] Сводка: {write_summary(results, args.output)}")
