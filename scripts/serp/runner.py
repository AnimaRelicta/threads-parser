"""Выполнение задач поиска воркерами."""

import asyncio
import random
import time
from pathlib import Path

from playwright.async_api import async_playwright

from browser.account import Account
from browser.net import Net
from core.logs import log
from db.pool import run_sync
from db.runs import finalize_run, start_run
from registry.store import pick_accounts
from scrape.capture import make_handler
from scrape.dispatch import Dispatcher
from scrape.page import scroll_down, settle
from serp.extract import doc_is_useful, harvest_post_pool, iter_search_posts, read_all_embedded
from serp.store import PostStore, is_job_done, load_jobs, mark_job_done, mark_job_failed, save_jobs
from serp.tasks import SearchTask


async def process_search_task(
    page,
    net,
    task: SearchTask,
    args,
    post_store: "PostStore",
) -> dict:
    """Обрабатывает одну задачу поиска.

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

    new_posts = 0
    duplicates = 0

    for post in posts:
        if post_store.add(post):
            new_posts += 1
        else:
            duplicates += 1

    if new_posts:
        post_store.save()

    return {
        "status": "ok",
        "posts": len(posts),
        "new_posts": new_posts,
        "duplicates": duplicates,
    }


async def run_search_async(st, args, tasks: list[SearchTask], outcome: dict) -> None:
    """outcome: сюда кладём аккаунты, touched и stats для записи запуска в БД (run_search)."""

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    jobs_path = out / "jobs.json"
    jobs = load_jobs(jobs_path)

    pending: list[SearchTask] = []
    skipped = 0

    for task in tasks:
        if is_job_done(jobs, task, args.force):
            skipped += 1
            continue
        pending.append(task)

    if skipped:
        log(f"[*] Пропущено готовых задач: {skipped}")

    if not pending:
        log("[*] Все задачи уже выполнены (см. jobs.json). Используйте --force для перезапуска.")
        return

    # Общая очередь на все аккаунты/воркеров, порядок задач перемешан (11.1).
    random.shuffle(pending)

    post_store = PostStore(out / "posts.jsonl")

    accounts = await pick_accounts(st, args.accounts)
    outcome["accounts"] = accounts
    n_accounts = len(accounts)
    m = max(1, int(getattr(args, "workers", 1) or 1))

    log(
        f"[*] Аккаунтов: {n_accounts} | воркеров на аккаунт: {m} "
        f"(одновременно до {n_accounts * m} задач) | задач: {len(pending)} | вывод: {out}"
    )

    disp = Dispatcher(pending)
    touched: dict[str, dict] = outcome["touched"]  # аккаунт -> {"status", "error"}
    stats: dict[str, dict] = outcome["stats"]  # аккаунт -> {"e429", "eauth"}
    jobs_lock = asyncio.Lock()

    def add_stat(account: str, **kw) -> None:
        s = stats.setdefault(account, {})
        for k, v in kw.items():
            s[k] = s.get(k, 0) + v

    async def save_jobs_locked() -> None:
        async with jobs_lock:
            save_jobs(jobs_path, jobs)

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
            try:
                if is_job_done(jobs, task, args.force):
                    continue  # уже закрыта другим аккаунтом, пока эта вкладка ждала

                label = task.label()
                page = await acc.ctx.new_page()
                net.captured.clear()
                page.on("response", make_handler(net.captured, net))
                try:
                    res = await process_search_task(page, net, task, args, post_store)
                except Exception as e:
                    log(f"[!] {tag} {label}: ошибка браузера: {type(e).__name__}: {str(e)[:200]}")
                    res = {"status": "error", "posts": 0, "new_posts": 0, "duplicates": 0}
                finally:
                    try:
                        await page.close()
                    except Exception:
                        pass

                status = res.get("status")

                if status == "ok":
                    mark_job_done(jobs, task, **res)
                    await save_jobs_locked()
                    log(
                        f"[+] {tag} {label}: постов {res.get('posts', 0)}, "
                        f"новых {res.get('new_posts', 0)}, дублей {res.get('duplicates', 0)}"
                    )

                elif status == "429":
                    add_stat(acc.name, e429=1)
                    wait = 60 * (tries + 1)
                    pause_state["until"] = max(pause_state["until"], time.monotonic() + wait)
                    if tries + 1 >= n_accounts:
                        mark_job_failed(jobs, task, f"429: исчерпаны попытки ({tries + 1})")
                        await save_jobs_locked()
                        log(
                            f"[!] {tag} {label}: 429, попытки исчерпаны "
                            f"({tries + 1}/{n_accounts}) -> failed"
                        )
                    else:
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
                    disp.put((task, tries))  # не вина задачи — попытки не тратим
                    log(
                        f"[!] {tag} {label}: сессия недействительна. Воркеры аккаунта "
                        f"остановлены. Выполните: uv run scripts/accounts.py relogin {acc.name}"
                    )

                else:
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

    if post_store.new:
        post_store.save()

    log(
        f"[✓] Постов в хранилище: {len(post_store.by_code)} "
        f"(новых за запуск: {post_store.new}, повторов: {post_store.dupes})"
    )
    log(f"[✓] posts.jsonl: {post_store.path}")
    log(f"[✓] jobs.json: {jobs_path}")


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
    outcome: dict = {"accounts": [], "touched": {}, "stats": {}}
    interrupted = False
    try:
        asyncio.run(run_search_async(st, args, tasks, outcome))
    except KeyboardInterrupt:
        interrupted = True
        print("\n[!] Остановлено пользователем: posts.jsonl и jobs.json сохранены.")
    finally:
        try:
            run_sync(
                st,
                lambda conn: finalize_run(
                    conn,
                    run_id,
                    outcome["accounts"],
                    outcome["touched"],
                    outcome["stats"],
                    {},
                    {},
                    interrupted,
                ),
            )
        except (Exception, SystemExit) as e:
            log(f"[!] Итоги запуска не записаны в БД (запуск #{run_id}): {str(e)[:200]}")
