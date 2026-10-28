"""Очередь профилей и воркеры n x m; точка запуска run()."""

import asyncio
import json
import random
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from browser.account import Account
from browser.net import Net
from browser.session import detect_username, session_user_id
from config import Settings
from core.consts import USERNAME_RE
from core.fsio import normalize_username, read_usernames
from core.logs import PSTAT, TAG, log
from core.timeutil import parse_since
from db.pool import DB_ERRORS, create_pool, run_sync
from db.profiles import ProfileStore
from db.runs import finalize_run, start_run
from registry.store import get_account, now_iso, pick_accounts
from scrape.capture import make_handler
from scrape.consts import BLOCKED_RESOURCES, UNLIMITED_DEPTH
from scrape.profile import run_profile
from scrape.report import print_summary, profile_run_entry


class Dispatcher:
    """Общая очередь профилей для всех воркеров всех аккаунтов.

    active считает профили, которые сейчас в работе: профиль может вернуться в
    очередь (429 на другом аккаунте), поэтому пустая очередь ещё не конец.
    Всё выполняется в одном потоке событийного цикла, блокировки не нужны.
    """

    def __init__(self, names: list[str]):
        self.queue: deque = deque((n, 0) for n in names)
        self.active = 0

    async def take(self, halt: asyncio.Event):
        """Следующий (профиль, число попыток) или None, когда работы больше не будет."""
        while not halt.is_set():
            if self.queue:
                job = self.queue.popleft()
                self.active += 1  # остаётся +1 до done()
                return job
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

    got: dict = field(default_factory=dict)  # ник профиля -> результат
    touched: dict = field(default_factory=dict)  # аккаунт -> {"status", "error"}
    stats: dict = field(default_factory=dict)  # аккаунт -> {"e429", "eauth"}
    nicks: dict = field(default_factory=dict)  # аккаунт -> найденный ник
    store: ProfileStore | None = None  # хранилище профилей (создаётся в main_async)
    json_fail: int = 0
    entries: dict = field(default_factory=dict)  # аккаунт -> [запись о запуске профиля]

    def add_stat(self, account: str, **kw) -> None:
        s = self.stats.setdefault(account, {})
        for k, v in kw.items():
            s[k] = s.get(k, 0) + v


async def worker_main(
    wid: int,
    acc: Account,
    grid_page,
    args,
    st: Settings,
    disp: Dispatcher,
    state: RunState,
    halt: asyncio.Event,
    n_accounts: int,
    stagger: float,
) -> None:
    """Один воркер: пара вкладок в браузере аккаунта, профили из общей очереди."""
    TAG.set(f"{acc.name}/w{wid}")
    account = acc.name
    net = Net(debug=bool(args.debug))
    post_page = None
    try:
        if stagger:
            await asyncio.sleep(stagger)  # разносим старт воркеров
        if grid_page is None:
            grid_page = await acc.ctx.new_page()
        post_page = await acc.ctx.new_page()  # вторая вкладка: лента профиля остаётся на месте
        post_page.on("response", make_handler(net.captured, net))
        grid_page.on("response", make_handler(net.grid_captured, net))
        if not args.no_block:

            async def block_heavy(route):
                try:
                    if route.request.resource_type in BLOCKED_RESOURCES:
                        await route.abort()
                    else:
                        await route.continue_()
                except Exception:
                    pass

            await post_page.route("**/*", block_heavy)

        first = True
        while True:
            job = await disp.take(halt)
            if job is None:
                break
            name, tries = job
            try:
                if not first:
                    await asyncio.sleep(
                        random.uniform(args.profile_delay, args.profile_delay * 1.5)
                    )
                first = False
                log(f"\n=== @{name} ===")
                ps: dict = {}
                PSTAT.set(ps)
                t0, started = time.monotonic(), now_iso()
                try:
                    res = await run_profile(acc, grid_page, post_page, net, name, args, state.store)
                except (PlaywrightError, *DB_ERRORS) as e:
                    is_db = not isinstance(e, PlaywrightError)
                    log(f"[!] Ошибка {'БД' if is_db else 'браузера'}: {str(e)[:200]}")
                    res = {
                        "username": name,
                        "status": "error",
                        "saved": 0,
                        "new_comments": 0,
                        "fresh_posts": 0,
                        "newest": None,
                        "note": "ошибка БД" if is_db else "ошибка браузера",
                    }
                state.add_stat(
                    account,
                    e429=1 if res.get("abort_kind") == "429" else 0,
                    eauth=1 if res.get("abort_kind") == "auth" else 0,
                )
                res["account"] = account
                res.update(
                    started_at=started,
                    finished_at=now_iso(),
                    duration_sec=round(time.monotonic() - t0, 1),
                    pstat=ps,
                )
                state.entries.setdefault(account, []).append(profile_run_entry(res))
                if res["status"] == "abort":
                    # проблема на уровне аккаунта: все его воркеры останавливаются,
                    # профиль уходит в очередь другому аккаунту
                    halt.set()
                    note = res.get("note", "")
                    state.touched[account] = {
                        "status": "needs_login"
                        if "авториз" in note or "недействит" in note
                        else None,
                        "error": note[:200],
                    }
                    if tries + 1 < n_accounts:
                        log(f"[!] @{name} вернётся в очередь другому аккаунту")
                        disp.put((name, tries + 1))
                        continue
                state.got[res["username"]] = res
            finally:
                disp.done()
    finally:
        state.json_fail += net.json_fail
        for pg in (grid_page, post_page):
            if pg is not None:
                try:
                    await pg.close()
                except Exception:
                    pass


async def account_main(
    p,
    acc_name: str,
    index: int,
    args,
    st: Settings,
    disp: Dispatcher,
    state: RunState,
    n_accounts: int,
    m: int,
) -> None:
    """Жизненный цикл аккаунта: один браузер, m воркеров, куки и закрытие в конце."""
    TAG.set(acc_name)
    acc = Account(acc_name, st, args)
    halt = asyncio.Event()
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

        try:  # ник аккаунта: пока пуст в реестре, определяем по главной странице
            if not ((await get_account(st, acc_name)) or {}).get("username"):
                nick = await detect_username(page0, await session_user_id(acc.ctx))
                if nick:
                    log(f"[+] Ник аккаунта: @{nick}")
                    state.nicks[acc_name] = nick
        except Exception as e:
            log(f"[*] Ник определить не удалось: {str(e)[:100]}")

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
        outcomes = await asyncio.gather(*workers, return_exceptions=True)
        for o in outcomes:
            if isinstance(o, Exception):
                log(f"[!] Воркер завершился с ошибкой: {type(o).__name__}: {str(o)[:200]}")
    finally:
        await acc.close()


async def main_async(
    accounts: list[str], usernames: list[str], args, st: Settings, state: RunState, m: int
) -> None:
    disp = Dispatcher(usernames)
    # Пул на весь запуск: воркеры берут соединения на время одной записи
    pool = await create_pool(st, max_size=max(5, len(accounts) * m + 2))
    state.store = ProfileStore(pool, Path(args.output))
    try:
        async with async_playwright() as p:
            outcomes = await asyncio.gather(
                *(
                    account_main(p, acc, ai, args, st, disp, state, len(accounts), m)
                    for ai, acc in enumerate(accounts)
                ),
                return_exceptions=True,
            )
    finally:
        await pool.close()
    for o in outcomes:
        if isinstance(o, Exception):
            log(f"[!] Аккаунт завершился с ошибкой: {type(o).__name__}: {str(o)[:200]}")


def run(args, st: Settings) -> None:
    usernames: list[str] = []
    if args.username:
        name = normalize_username(args.username)
        if not name or not USERNAME_RE.match(name):
            raise SystemExit(f"Не удалось определить профиль из: {args.username}")
        usernames.append(name)
    if args.file:
        for name in read_usernames(Path(args.file)):
            if name.lower() not in {u.lower() for u in usernames}:
                usernames.append(name)
    if not usernames:
        raise SystemExit("Укажите профиль или заполните файл со списком профилей")
    if args.depth == -1:
        args.depth = UNLIMITED_DEPTH
    elif args.depth < 1:
        raise SystemExit("--depth должен быть не меньше 1 (или -1 без ограничения)")

    args.cutoff = parse_since(args.since)
    accounts = asyncio.run(pick_accounts(st, args.accounts))
    m = max(1, args.workers)
    log(
        f"[*] Профилей: {len(usernames)} | аккаунтов: {len(accounts)} | "
        f"воркеров на аккаунт: {m} (одновременно до {len(accounts) * m} профилей)"
    )
    if args.cutoff:
        log(f"[*] Берём посты не старше {args.cutoff:%Y-%m-%d %H:%M} UTC")
    if getattr(args, "full", False):
        log("[*] --full: якорь игнорируется, полный проход по окну")
    if args.comments:
        log(
            f"[*] Комментарии: глубина "
            f"{'без ограничения' if args.depth == UNLIMITED_DEPTH else args.depth}"
            + (f", не больше {args.max_comments} на пост" if args.max_comments else "")
        )

    Path(args.output).mkdir(parents=True, exist_ok=True)
    state = RunState()
    run_settings = {
        "profiles_requested": len(usernames),
        "workers": m,
        "accounts": accounts,
        "since": args.since,
        "limit": args.limit,
        "comments": bool(args.comments),
        "depth": None if args.depth == UNLIMITED_DEPTH else args.depth,
        "max_comments": args.max_comments,
        "full": bool(getattr(args, "full", False)),
        "with_videos": bool(args.with_videos),
        "headless": bool(args.headless),
    }
    run_id = run_sync(st, lambda conn: start_run(conn, "profiles", run_settings))
    interrupted = False
    try:
        asyncio.run(main_async(accounts, usernames, args, st, state, m))
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
                    interrupted,
                ),
            )
        except (Exception, SystemExit) as e:
            log(f"[!] Итоги запуска не записаны в БД (запуск #{run_id}): {str(e)[:200]}")

    if state.json_fail:
        log(f"[!] Не удалось разобрать JSON в {state.json_fail} ответах сервера")
    results_list = []
    for u in usernames:
        results_list.append(
            state.got.get(u)
            or {
                "username": u,
                "status": "not_run",
                "saved": 0,
                "new_comments": 0,
                "fresh_posts": 0,
                "newest": None,
                "note": "не обработан: запустите ещё раз",
            }
        )
    print_summary(results_list, args.cutoff)
    serializable = [
        {
            **{k: v for k, v in r.items() if k != "pstat"},
            "newest": r["newest"].isoformat() if r["newest"] else None,
        }
        for r in results_list
    ]
    (Path(args.output) / "summary.json").write_text(
        json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"\n[✓] Сводка: {Path(args.output) / 'summary.json'}")
