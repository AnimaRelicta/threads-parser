"""Выполнение задач поиска воркерами."""

import asyncio
import random
import time

from playwright.async_api import async_playwright

from browser.account import Account
from browser.net import Net
from core.logs import log
from db.pool import create_pool, run_sync
from db.runs import finalize_run, start_run
from db.search import release_running_jobs
from registry.store import pick_accounts
from scrape.capture import make_handler
from scrape.dispatch import Dispatcher
from scrape.page import scroll_down, settle
from serp.extract import doc_is_useful, harvest_post_pool, iter_search_posts, read_all_embedded
from serp.store import SearchStore
from serp.tasks import SearchTask


async def process_search_task(
    page,
    net,
    task: SearchTask,
    args,
    store: SearchStore,
    job_id: int,
) -> dict:
    """Обрабатывает одну задачу поиска; найденные посты сразу пишет в БД.

    Возвращает словарь:
    {
        "status": "ok" | "429" | "auth",
        "posts": int,
        "new_posts": int,
        "duplicates": int,
    }
    """

    # Небольшая пауза перед стартом задачи, чтобы не долбить сайт резко.
    await asyncio.sleep(1.5)

    net.captured.clear()

    url = task.url()
    resp = await page.goto(url, wait_until="domcontentloaded")

    if resp is not None and resp.status == 429:
        return {
            "status": "429",
            "posts": 0,
            "new_posts": 0,
            "duplicates": 0,
        }

    if "/login" in page.url:
        return {
            "status": "auth",
            "posts": 0,
            "new_posts": 0,
            "duplicates": 0,
        }

    # Дать странице дорисоваться и поднять первичные embedded JSON.
    await page.wait_for_timeout(2500)
    embedded = await read_all_embedded(page)

    max_posts = int(getattr(args, "max_posts", 0) or 0)
    max_scrolls = int(getattr(args, "max_scrolls", 0) or 0)

    # Сколько постов уже видно до прокруток.
    initial_useful = [d for d in embedded if doc_is_useful(d)]
    last_count = len(harvest_post_pool(initial_useful))

    scrolls = 0
    stale = 0

    while True:
        # Если задан явный лимит прокруток — уважаем его.
        # Если max_scrolls == 0, работаем до остановки по отсутствию новых постов
        # или до max_posts.
        if max_scrolls > 0 and scrolls >= max_scrolls:
            break

        await scroll_down(page)
        await settle(
            page,
            net.captured,
            min_ms=500,
            quiet_ms=1200,
            max_ms=6000,
        )

        scrolls += 1

        docs = embedded + net.captured
        useful = [d for d in docs if doc_is_useful(d)]
        pool = harvest_post_pool(useful)
        count = len(pool)

        if max_posts > 0 and count >= max_posts:
            break

        if count == last_count:
            stale += 1
            # Две пустые прокрутки подряд считаем достаточной остановкой.
            if stale >= 2:
                break
        else:
            stale = 0
            last_count = count

    docs = embedded + net.captured
    useful = [d for d in docs if doc_is_useful(d)]

    posts = iter_search_posts(useful, task.query, task.mode)

    # Если задан лимит постов на задачу, берём самые свежие.
    if max_posts > 0:
        posts.sort(key=lambda p: p.get("date_utc") or "", reverse=True)
        posts = posts[:max_posts]

    new_posts, duplicates = await store.save_posts(job_id, posts)

    return {
        "status": "ok",
        "posts": len(posts),
        "new_posts": new_posts,
        "duplicates": duplicates,
    }


async def run_search_async(st, args, tasks: list[SearchTask], outcome: dict) -> None:
    """outcome: сюда кладём аккаунты, touched и stats для записи запуска в БД (run_search)."""
    pool = await create_pool(st, max_size=10)
    try:
        await search_with_store(st, args, tasks, outcome, SearchStore(pool))
    finally:
        await pool.close()


async def search_with_store(
    st, args, tasks: list[SearchTask], outcome: dict, store: SearchStore
) -> None:
    pending_jobs, skipped = await store.plan(tasks, args.force)
    if skipped:
        log(f"[*] Пропущено готовых задач: {skipped}")
    if not pending_jobs:
        log("[*] Все задачи уже выполнены (search_jobs). Используйте --force для перезапуска.")
        return

    job_ids = {task: job_id for task, job_id in pending_jobs}
    pending: list[SearchTask] = list(job_ids)
    # Общая очередь на все аккаунты/воркеров, порядок задач перемешан (11.1).
    random.shuffle(pending)

    accounts = await pick_accounts(st, args.accounts)
    outcome["accounts"] = accounts
    n_accounts = len(accounts)
    m = max(1, int(getattr(args, "workers", 1) or 1))
    run_id = outcome["run_id"]

    log(
        f"[*] Аккаунтов: {n_accounts} | воркеров на аккаунт: {m} "
        f"(одновременно до {n_accounts * m} задач) | задач: {len(pending)}"
    )

    disp = Dispatcher(pending)
    touched: dict[str, dict] = outcome["touched"]  # аккаунт -> {"status", "error"}
    stats: dict[str, dict] = outcome["stats"]  # аккаунт -> {"e429", "eauth"}
    closed: set[int] = set()  # id задач, закрытых в этом запуске
    totals = {"found": 0, "new": 0, "dupes": 0}

    def add_stat(account: str, **kw) -> None:
        s = stats.setdefault(account, {})
        for k, v in kw.items():
            s[k] = s.get(k, 0) + v

    async def worker(acc: "Account", wid: int, halt: asyncio.Event, pause_state: dict) -> None:
        """Один воркер: своя вкладка на задачу, общая очередь disp, пауза общая на
        аккаунт (чтобы 429 на одной вкладке не влиял на остальные вкладки других
        аккаунтов, но останавливал именно этот аккаунт — 11.1/11.2)."""
        net = Net(debug=bool(getattr(args, "debug", False)))
        tag = f"{acc.name}/w{wid}"
        while True:
            now = time.monotonic()
            if now < pause_state["until"]:
                await asyncio.sleep(pause_state["until"] - now)
            job = await disp.take(halt)
            if job is None:
                break
            task, tries = job
            job_id = job_ids[task]
            try:
                if not args.force and job_id in closed:
                    continue  # уже закрыта другим аккаунтом, пока эта вкладка ждала

                label = task.label()
                await store.start_job(job_id, run_id, acc.name)
                page = await acc.ctx.new_page()
                net.captured.clear()
                page.on("response", make_handler(net.captured, net))
                try:
                    res = await process_search_task(page, net, task, args, store, job_id)
                except Exception as e:
                    err = f"{type(e).__name__}: {str(e)[:150]}"
                    log(f"[!] {tag} {label}: ошибка браузера/БД: {err}")
                    res = {"status": "error", "posts": 0, "new_posts": 0, "duplicates": 0}
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass

                status = res.get("status")

                if status == "ok":
                    await store.finish_job(
                        job_id,
                        res.get("posts", 0),
                        res.get("new_posts", 0),
                        res.get("duplicates", 0),
                    )
                    closed.add(job_id)
                    totals["found"] += res.get("posts", 0)
                    totals["new"] += res.get("new_posts", 0)
                    totals["dupes"] += res.get("duplicates", 0)
                    log(
                        f"[+] {tag} {label}: постов {res.get('posts', 0)}, "
                        f"новых {res.get('new_posts', 0)}, дублей {res.get('duplicates', 0)}"
                    )

                elif status == "429":
                    add_stat(acc.name, e429=1)
                    wait = 60 * (tries + 1)
                    pause_state["until"] = max(pause_state["until"], time.monotonic() + wait)
                    if tries + 1 >= n_accounts:
                        await store.fail_job(
                            job_id, f"429: исчерпаны попытки ({tries + 1})", tries + 1
                        )
                        log(
                            f"[!] {tag} {label}: 429, попытки исчерпаны "
                            f"({tries + 1}/{n_accounts}) -> failed"
                        )
                    else:
                        await store.requeue_job(job_id, "429")
                        disp.put((task, tries + 1))
                        log(
                            f"[!] {tag} {label}: 429, пауза аккаунта {wait} сек, задача "
                            f"вернётся в очередь другому аккаунту (попытка {tries + 1}/{n_accounts})"
                        )

                elif status == "auth":
                    add_stat(acc.name, eauth=1)
                    halt.set()
                    touched[acc.name] = {
                        "status": "needs_login",
                        "error": "сессия недействительна (/login)",
                    }
                    await store.requeue_job(job_id, "сессия аккаунта недействительна", False)
                    disp.put((task, tries))  # не вина задачи — попытки не тратим
                    log(
                        f"[!] {tag} {label}: сессия недействительна. Воркеры аккаунта "
                        f"остановлены. Выполните: uv run scripts/accounts.py relogin {acc.name}"
                    )

                else:
                    await store.fail_job(job_id, f"ошибка: {status}", tries)
                    log(f"[!] {tag} {label}: неожиданный статус: {status}")
            finally:
                disp.done()

    async def run_account(p, acc_name: str) -> None:
        acc = Account(acc_name, st, args)
        halt = asyncio.Event()
        pause_state = {"until": 0.0}
        try:
            try:
                page0 = await acc.open(p)
            except SystemExit as e:
                log(f"[!] {acc_name}: аккаунт не запущен: {e}")
                bad = "недействительна" in str(e)
                add_stat(acc_name, eauth=1 if bad else 0)
                touched[acc_name] = {
                    "status": "needs_login" if bad else None,
                    "error": str(e)[:200],
                }
                return
            try:
                await page0.close()
            except Exception:
                pass

            workers = [worker(acc, w + 1, halt, pause_state) for w in range(m)]
            outcomes = await asyncio.gather(*workers, return_exceptions=True)
            for o in outcomes:
                if isinstance(o, Exception):
                    log(
                        f"[!] {acc_name}: воркер завершился с ошибкой: "
                        f"{type(o).__name__}: {str(o)[:200]}"
                    )
        finally:
            await acc.close()

    async with async_playwright() as p:
        outcomes = await asyncio.gather(
            *(run_account(p, acc_name) for acc_name in accounts),
            return_exceptions=True,
        )
    for o in outcomes:
        if isinstance(o, Exception):
            log(f"[!] Аккаунт завершился с ошибкой: {type(o).__name__}: {str(o)[:200]}")

    log(
        f"[✓] Найдено постов: {totals['found']} "
        f"(новых за запуск: {totals['new']}, повторов: {totals['dupes']})"
    )


def run_search(st, args, tasks: list[SearchTask]) -> None:
    """Синхронная точка входа для обычного (не --recon) поиска."""
    settings = {
        "tasks": len(tasks),
        "modes": list(getattr(args, "modes", []) or []),
        "workers": args.workers,
        "max_posts": args.max_posts,
        "max_scrolls": args.max_scrolls,
        "force": bool(args.force),
        "headless": bool(args.headless),
    }
    run_id = run_sync(st, lambda conn: start_run(conn, "search", settings))
    outcome: dict = {"run_id": run_id, "accounts": [], "touched": {}, "stats": {}}
    interrupted = False
    try:
        asyncio.run(run_search_async(st, args, tasks, outcome))
    except KeyboardInterrupt:
        interrupted = True
        print("\n[!] Остановлено: найденное уже в БД, незавершённые задачи остались в очереди.")
    finally:

        async def close_run(conn) -> None:
            await release_running_jobs(conn, run_id)
            await finalize_run(
                conn,
                run_id,
                outcome["accounts"],
                outcome["touched"],
                outcome["stats"],
                {},
                {},
                interrupted,
            )

        try:
            run_sync(st, close_run)
        except (Exception, SystemExit) as e:
            log(f"[!] Итоги запуска не записаны в БД (запуск #{run_id}): {str(e)[:200]}")
