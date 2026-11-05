"""Сбор веток по списку ссылок из файла (data/threads_posts.txt).

Аналог поиска, только вместо выдачи по запросу открывается каждая указанная ветка.
Что уже лежит в posts, по умолчанию пропускается; --refresh открывает такие ветки
повторно и обновляет счётчики (текст и фильтр меняются только у неразобранных веток).
"""

import asyncio
import random
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from browser.account import Account
from browser.net import Net
from browser.session import USER_ID_KEYS
from config import TH
from core.consts import USERNAME_RE
from core.errors import AuthProblem, RateLimited
from core.logs import log
from core.timeutil import iso
from db.pool import DB_ERRORS, create_pool, run_sync
from db.postlist import existing_posts, save_listed_post
from db.runs import finalize_run, start_run
from registry.store import pick_accounts
from scrape.capture import make_handler
from scrape.consts import BLOCKED_RESOURCES
from scrape.dispatch import Dispatcher
from scrape.extract import caption_of, topic_of
from scrape.page import load_root

RATE_LIMIT_PAUSE = 30  # сек. паузы аккаунта после 429, умножается на номер попытки
SPEED_WINDOW = 40  # по скольки последним веткам считается скорость в строке прогресса

CODE = r"[A-Za-z0-9_-]+"
FULL_URL_RE = re.compile(rf"threads\.(?:com|net)/@([^/?#\s]+)/post/({CODE})")
SHORT_URL_RE = re.compile(rf"threads\.(?:com|net)/t/({CODE})")
USER_POST_RE = re.compile(rf"^@?([^/\s?#]+)/post/({CODE})")
BARE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")

Entry = tuple[str, str | None]  # (код ветки, ник автора или None)


@dataclass(frozen=True)
class PostTask:
    code: str
    username: str
    known: bool  # ветка уже есть в БД (идёт по --refresh)


def parse_line(line: str) -> Entry | None:
    """Строка файла -> (код, ник или None). None, если это не ветка."""
    if m := FULL_URL_RE.search(line):
        return m.group(2), m.group(1)
    if m := SHORT_URL_RE.search(line):
        return m.group(1), None
    if m := USER_POST_RE.match(line):
        return m.group(2), m.group(1)
    if BARE_CODE_RE.match(line):
        return line, None
    return None


def merge_entries(*groups: list[Entry]) -> list[Entry]:
    """Объединяет списки без повторов по коду; ник берётся из первой записи, где он есть."""
    out: dict[str, str | None] = {}
    for group in groups:
        for code, user in group:
            if code not in out or (out[code] is None and user):
                out[code] = user
    return list(out.items())


def parse_entries(lines: list[str]) -> list[Entry]:
    """Строки -> [(код, ник|None)] без повторов; # и пустые строки игнорируются."""
    found: list[Entry] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parsed = parse_line(line)
        if parsed is None or (parsed[1] and not USERNAME_RE.match(parsed[1])):
            log(f"[!] Не похоже на ветку, пропускаю: {line}")
            continue
        found.append(parsed)
    return merge_entries(found)


def load_entries(path: Path) -> list[Entry]:
    if not path.exists():
        raise SystemExit(f"Файл не найден: {path}")
    return parse_entries(path.read_text(encoding="utf-8-sig").splitlines())


async def plan_tasks(
    pool, entries: list[Entry], refresh: bool, stale_days: int
) -> tuple[list[PostTask], dict]:
    """Фильтр по БД: новые идут всегда, существующие только с --refresh.

    stale_days > 0 оставляет для обновления только ветки, не менявшиеся столько дней.
    """
    async with pool.acquire() as conn:
        have = await existing_posts(conn, [c for c, _ in entries])
    now = datetime.now(timezone.utc)
    tasks: list[PostTask] = []
    info = {"total": len(entries), "new": 0, "refresh": 0, "in_db": 0, "fresh": 0, "no_user": 0}
    for code, user in entries:
        row = have.get(code)
        if row is None:
            if not user:
                info["no_user"] += 1
                log(f"[!] {code}: нет ника в ссылке и ветки нет в БД, пропускаю (нужна ссылка)")
                continue
            tasks.append(PostTask(code, user, False))
            info["new"] += 1
            continue
        if not refresh:
            info["in_db"] += 1
            continue
        upd = row["updated_at"]
        if stale_days > 0 and upd is not None and now - upd < timedelta(days=stale_days):
            info["fresh"] += 1
            continue
        tasks.append(PostTask(code, user or row["username"], True))
        info["refresh"] += 1
    return tasks, info


def plan_sync(st, entries: list[Entry], refresh: bool, stale_days: int):
    """Синхронная обёртка плана для точки входа."""

    async def _run():
        pool = await create_pool(st, max_size=2)
        try:
            return await plan_tasks(pool, entries, refresh, stale_days)
        finally:
            await pool.close()

    return asyncio.run(_run())


def extract_listed(media: dict, username: str, code: str) -> dict:
    """Объект ветки со страницы -> словарь для save_listed_post (ник берём из данных ветки)."""
    user = media.get("user") if isinstance(media.get("user"), dict) else {}
    uname = user.get("username") or username
    user_id = next((str(user[k]) for k in USER_ID_KEYS if user.get(k) is not None), None)
    info = media.get("text_post_app_info") or {}
    return {
        "code": code,
        "url": f"{TH}/@{uname}/post/{code}",
        "username": uname,
        "user_id": user_id,
        "text": caption_of(media),
        "topic": topic_of(media),
        "date_utc": iso(media.get("taken_at")),
        "likes": media.get("like_count"),
        "reply_count": info.get("direct_reply_count"),
        "reposts": info.get("repost_count"),
        "quotes": info.get("quote_count"),
    }


async def process_post(page, net: Net, task: PostTask, pool) -> dict:
    """Открывает ветку и пишет её в posts. status: ok | gone | 429 | auth."""
    try:
        _, media = await load_root(page, net, task.username, task.code)
    except RateLimited:
        return {"status": "429"}
    except AuthProblem:
        return {"status": "auth"}
    if media is None:
        return {"status": "gone"}  # удалена, скрыта или данные не пришли
    post = extract_listed(media, task.username, task.code)
    async with pool.acquire() as conn:
        inserted = await save_listed_post(conn, post)
    return {"status": "ok", "inserted": inserted, "post": post}


class Progress:
    """Строка прогресса: обработано/осталось и скорость по последним SPEED_WINDOW веткам."""

    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self.marks: deque[float] = deque(maxlen=SPEED_WINDOW)

    def tick(self) -> str:
        self.done += 1
        self.marks.append(time.monotonic())
        speed = ""
        if len(self.marks) >= 2 and self.marks[-1] > self.marks[0]:
            per_min = (len(self.marks) - 1) / (self.marks[-1] - self.marks[0]) * 60
            speed = f" | {per_min:.0f} веток/мин (по последним {len(self.marks)})"
        return f"{self.done}/{self.total}, осталось {max(0, self.total - self.done)}{speed}"


async def run_postlist_async(st, args, tasks: list[PostTask], outcome: dict) -> None:
    pool = await create_pool(st, max_size=10)
    try:
        await run_tasks(st, args, tasks, outcome, pool)
    finally:
        await pool.close()


async def run_tasks(st, args, tasks: list[PostTask], outcome: dict, pool) -> None:
    accounts = await pick_accounts(st, args.accounts)
    outcome["accounts"] = accounts
    n_accounts = len(accounts)
    m = max(1, int(args.workers or 1))
    log(f"[*] Аккаунтов: {n_accounts} | вкладок на аккаунт: {m} | веток к сбору: {len(tasks)}")

    pending = list(tasks)
    random.shuffle(pending)  # нагрузка размазана по авторам, как в поиске
    disp = Dispatcher(pending)
    touched: dict[str, dict] = outcome["touched"]
    stats: dict[str, dict] = outcome["stats"]
    totals = outcome["totals"]
    progress = Progress(len(tasks))
    delay = max(0.0, args.delay)

    def add_stat(account: str, **kw) -> None:
        s = stats.setdefault(account, {})
        for k, v in kw.items():
            s[k] = s.get(k, 0) + v

    async def worker(acc: Account, wid: int, halt: asyncio.Event, pause_state: dict) -> None:
        net = Net(debug=bool(args.debug))
        tag = f"{acc.name}/w{wid}"
        page = await acc.ctx.new_page()  # одна вкладка на воркера: ветки идут подряд
        page.on("response", make_handler(net.captured, net))
        if not args.no_block:

            async def block_heavy(route):
                try:
                    if route.request.resource_type in BLOCKED_RESOURCES:
                        await route.abort()
                    else:
                        await route.continue_()
                except Exception:
                    pass

            await page.route("**/*", block_heavy)
        try:
            while True:
                wait = pause_state["until"] - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                job = await disp.take(halt)
                if job is None:
                    break
                task, tries = job
                label = f"@{task.username}/{task.code}"
                try:
                    try:
                        res = await process_post(page, net, task, pool)
                    except (PlaywrightError, *DB_ERRORS) as e:
                        res = {"status": "error", "err": f"{type(e).__name__}: {str(e)[:120]}"}
                    status = res["status"]

                    if status == "ok":
                        totals["new" if res["inserted"] else "updated"] += 1
                        p = res["post"]
                        log(
                            f"[+] {tag} {progress.tick()} | {label}: "
                            f"{'новая' if res['inserted'] else 'обновлена'}, "
                            f"лайков {p['likes']}, ответов {p['reply_count']}"
                        )
                    elif status == "gone":
                        totals["gone"] += 1
                        log(f"[-] {tag} {progress.tick()} | {label}: не открылась (удалена/скрыта)")
                    elif status == "429":
                        add_stat(acc.name, e429=1)
                        pause = RATE_LIMIT_PAUSE * (tries + 1)
                        pause_state["until"] = max(pause_state["until"], time.monotonic() + pause)
                        if tries + 1 >= n_accounts:
                            totals["failed"] += 1
                            progress.tick()
                            log(f"[!] {tag} {label}: 429, попытки исчерпаны ({tries + 1}) -> пропуск")
                        else:
                            disp.put((task, tries + 1))
                            log(f"[!] {tag} {label}: 429, пауза аккаунта {pause} сек, в очередь")
                    elif status == "auth":
                        add_stat(acc.name, eauth=1)
                        halt.set()
                        touched[acc.name] = {
                            "status": "needs_login",
                            "error": "сессия недействительна (/login)",
                        }
                        disp.put((task, tries))  # не вина ветки: попытку не тратим
                        log(
                            f"[!] {tag} {label}: сессия недействительна. Выполните: "
                            f"uv run scripts/accounts.py relogin {acc.name}"
                        )
                    else:
                        if tries < 1:  # одна повторная попытка на сбой браузера/БД
                            disp.put((task, tries + 1))
                        else:
                            totals["failed"] += 1
                            progress.tick()
                        log(f"[!] {tag} {label}: ошибка {res.get('err')}")
                finally:
                    disp.done()
                if delay:
                    await asyncio.sleep(random.uniform(delay * 0.5, delay * 1.5))
        finally:
            try:
                await page.close()
            except Exception:
                pass

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
            outs = await asyncio.gather(
                *(worker(acc, w + 1, halt, pause_state) for w in range(m)),
                return_exceptions=True,
            )
            for o in outs:
                if isinstance(o, Exception):
                    log(f"[!] {acc_name}: воркер упал: {type(o).__name__}: {str(o)[:200]}")
        finally:
            await acc.close()

    async with async_playwright() as p:
        outs = await asyncio.gather(*(run_account(p, a) for a in accounts), return_exceptions=True)
    for o in outs:
        if isinstance(o, Exception):
            log(f"[!] Аккаунт завершился с ошибкой: {type(o).__name__}: {str(o)[:200]}")

    left = len(disp.queue)
    log(
        f"[✓] Новых: {totals['new']}, обновлено: {totals['updated']}, "
        f"не открылись: {totals['gone']}, ошибок: {totals['failed']}"
        + (f", не обработано (остановка аккаунтов): {left}" if left else "")
    )


def run_postlist(st, args, tasks: list[PostTask], plan: dict) -> None:
    """Синхронная точка входа: запись запуска в runs, как у поиска."""
    settings = {
        "source": "postlist",
        "tasks": len(tasks),
        "refresh": bool(args.refresh),
        "stale_days": args.stale_days,
        "workers": args.workers,
        "headless": bool(args.headless),
        **{f"plan_{k}": v for k, v in plan.items()},
    }
    run_id = run_sync(st, lambda conn: start_run(conn, "search", settings))
    outcome: dict = {
        "run_id": run_id,
        "accounts": [],
        "touched": {},
        "stats": {},
        "totals": {"new": 0, "updated": 0, "gone": 0, "failed": 0},
    }
    interrupted = False
    try:
        asyncio.run(run_postlist_async(st, args, tasks, outcome))
    except KeyboardInterrupt:
        interrupted = True
        print("\n[!] Остановлено: собранные ветки уже в БД, повторный запуск дособерёт остальные.")
    finally:

        async def close_run(conn) -> None:
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
