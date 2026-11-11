"""Команды `ig accounts`: add, relogin, check, import, export, list, proxy, cooldown, import-legacy."""

import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from browser.launch import launch_browser, new_context
from config import IG_COOKIE_DOMAIN, IgSettings
from db.pool import connection
from db.stats import format_table
from ig.browser.proxy import account_proxy
from ig.browser.session import check_session, load_cookies
from ig.consts import IG_SCHEMA
from ig.db import accounts as repo
from ig.registry.login import login_into
from ig.registry.store import (
    find_duplicate,
    load_registry,
    next_name,
    register_login,
    set_cooldown,
    sync_cookie_files,
    update_account,
)
from registry.commands import collect_bundle
from registry.cookies import DEFAULT_USER_AGENT, cookie_jar, normalize_cookie
from registry.login import confirm, dup_question

LIST_SQL = """
SELECT a.name,
       CASE WHEN a.username IS NOT NULL THEN '@' || a.username
            ELSE 'id:' || coalesce(a.user_id, '?') END AS "user",
       a.status,
       CASE WHEN a.cooldown_until > now()
            THEN to_char(a.cooldown_until AT TIME ZONE 'UTC', 'HH24:MI:SS') END AS cooldown_utc,
       coalesce(ra.runs, 0)        AS runs,
       coalesce(pr.profiles, 0)    AS profiles,
       coalesce(pr.posts, 0)       AS posts,
       coalesce(pr.comments, 0)    AS comments,
       coalesce(pr.media_files, 0) AS media,
       coalesce(ra.e429, 0)        AS "429",
       coalesce(ra.eauth, 0)       AS auth,
       coalesce(to_char(a.last_run_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI'), '-') AS last_run,
       left(coalesce(a.last_error, ''), 30) AS error
FROM accounts a
LEFT JOIN (
    SELECT account, count(*) AS runs, sum(errors_429) AS e429, sum(errors_auth) AS eauth
    FROM run_accounts GROUP BY account
) ra ON ra.account = a.name
LEFT JOIN (
    SELECT account,
           count(DISTINCT profile_id) FILTER (WHERE status IN ('ok', 'empty')) AS profiles,
           sum(posts_new) AS posts, sum(comments_new) AS comments,
           sum(media_new_files) AS media_files
    FROM profile_runs GROUP BY account
) pr ON pr.account = a.name
ORDER BY a.name
"""


async def cmd_add(st: IgSettings, count: int, prefix: str, assume_yes: bool) -> None:
    ok = 0
    async with async_playwright() as p:
        for i in range(count):
            name = next_name(st, await load_registry(st), prefix)
            print(f"\n=== Аккаунт {i + 1} из {count}: {name} ===", flush=True)
            if await login_into(p, st, name, assume_yes):
                ok += 1
            else:
                st.cookies_path(name).unlink(missing_ok=True)
    print(f"\n[✓] Сохранено: {ok} из {count}. Куки: {st.cookies_dir}")
    print("    Для сервера: cli.py ig accounts export")


async def cmd_relogin(st: IgSettings, names: list[str]) -> None:
    reg = await load_registry(st)
    async with async_playwright() as p:
        for name in names:
            if name not in reg:
                print(f"[!] Аккаунта «{name}» нет в реестре (cli.py ig accounts list)")
                continue
            print(f"\n=== Повторный вход: {name} ===", flush=True)
            await login_into(p, st, name)


async def cmd_check(st: IgSettings, names: list[str], headless: bool) -> None:
    await sync_cookie_files(st)
    reg = await load_registry(st)
    names = names or list(reg)
    if not names:
        print("Реестр пуст: cli.py ig accounts add 1")
        return
    print(f"[*] Проверка сессий, режим: {'без окна (headless)' if headless else 'с окном'}")
    async with async_playwright() as p:
        for name in names:
            data = load_cookies(st.cookies_path(name))
            if not data:
                print(f"  {name}: нет файла кук")
                if name in reg:
                    await update_account(st, name, status="needs_login")
                continue
            browser = await launch_browser(p, headless, await account_proxy(st, name))
            try:
                ctx = await new_context(browser, data)
                page = await ctx.new_page()
                user = await check_session(page)
            except Exception as e:
                print(f"  {name}: ошибка проверки ({str(e)[:120]})")
                user = None
            finally:
                await browser.close()
            if user:
                print(
                    f"  {name}: сессия действует "
                    + (f"(@{user})" if user != "?" else "(ник определить не удалось)")
                )
            else:
                print(f"  {name}: сессия не действует → relogin")
            if name in reg:
                fields: dict = {}
                if user and user != "?":
                    fields["username"] = user
                if user and reg[name].get("status") == "needs_login":
                    fields["status"] = "active"
                elif not user:
                    fields["status"] = "needs_login"
                if fields:
                    await update_account(st, name, **fields)


async def import_blob(
    st: IgSettings,
    raw: bytes,
    src: str,
    name: str | None,
    assume_yes: bool,
    explicit: bool = False,
    user_agent: str | None = None,
    meta: dict | None = None,
) -> str | None:
    """Добавляет один аккаунт из содержимого файла кук. Возвращает имя или None."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"[!] {src}: не JSON ({e}), пропуск")
        return None
    jar = cookie_jar(data)
    if not jar.get("sessionid"):
        print(f"[!] {src}: нет куки sessionid (сессия не выполнена или истекла), пропуск")
        return None
    raw_cookies = data["cookies"] if isinstance(data, dict) else data
    cookies = [n for n in (normalize_cookie(c, IG_COOKIE_DOMAIN) for c in raw_cookies) if n]
    user_id = jar.get("ds_user_id")
    user_agent = (
        user_agent
        or (data.get("user_agent") if isinstance(data, dict) else None)
        or DEFAULT_USER_AGENT
    )

    reg = await load_registry(st)
    dup = find_duplicate(reg, user_id)
    if dup and not confirm(dup_question(f"с id {user_id} ({src})", dup), assume_yes):
        print(f"[!] {src}: отменено, текущая сессия не изменена")
        return None
    target = dup or name or next_name(st, reg)
    if not dup and target in reg:
        if explicit:
            raise SystemExit(f"Имя «{target}» уже занято в реестре")
        target = next_name(st, reg)

    st.cookies_dir.mkdir(parents=True, exist_ok=True)
    st.cookies_path(target).write_text(
        json.dumps({"user_agent": user_agent, "cookies": cookies}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    username = (meta or {}).get("username") or (meta or {}).get("instagram_username")
    if dup:
        await register_login(st, dup, None, None)
        print(f"[+] Сессия «{dup}» заменена на новую (id {user_id})")
    else:
        await register_login(st, target, username, user_id)
        print(f"[+] Аккаунт добавлен как «{target}» (id {user_id or '?'})")
    return target


async def cmd_import(
    st: IgSettings, file: Path, name: str | None, assume_yes: bool, user_agent: str | None
) -> None:
    if not file.exists():
        raise SystemExit(f"Не найдено: {file}")
    items, registry = collect_bundle(file)
    if not items:
        raise SystemExit("В источнике не нашлось файлов кук (*.json)")
    if name and len(items) > 1:
        raise SystemExit("--name можно указать только для одного файла")
    done = []
    for stem, raw in items:
        target = await import_blob(
            st,
            raw,
            stem,
            name or (stem if len(items) > 1 else None),
            assume_yes,
            explicit=bool(name),
            user_agent=user_agent,
            meta=registry.get(stem),
        )
        if target:
            done.append(target)
    print(f"\n[✓] Импортировано: {len(done)} из {len(items)}")
    if done:
        print("    Проверить сессии: cli.py ig accounts check")


async def cmd_export(st: IgSettings, names: list[str], out: Path | None) -> None:
    await sync_cookie_files(st)
    reg = await load_registry(st)
    names = [n for n in (names or list(reg)) if st.cookies_path(n).exists()]
    if not names:
        raise SystemExit("Нет аккаунтов с файлами кук. Сначала: cli.py ig accounts add 3")
    out = out or st.export_dir / f"ig_accounts_{datetime.now():%Y%m%d_%H%M%S}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        n: {"username": reg.get(n, {}).get("username"), "user_id": reg.get(n, {}).get("user_id")}
        for n in names
    }
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for n in names:
            z.write(st.cookies_path(n), f"cookies/{n}.json")
        z.writestr("accounts.json", json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"[✓] Выгружено аккаунтов: {len(names)} → {out}")
    print("    Архив не зашифрован, а куки равноценны паролю: передавайте безопасно.")
    print(f"    На сервере: cli.py ig accounts import {out.name}")


async def cmd_list(st: IgSettings) -> None:
    await sync_cookie_files(st)
    async with connection(st, IG_SCHEMA) as conn:
        rows = [tuple(r.values()) for r in await conn.fetch(LIST_SQL)]
    if not rows:
        print("Реестр пуст: cli.py ig accounts add 1")
        return
    cols = [
        "name", "user", "status", "cooldown_utc", "runs", "profiles", "posts",
        "comments", "media", "429", "auth", "last_run", "error",
    ]  # fmt: skip
    print(format_table(cols, rows))
    print(f"\nВсего: {len(rows)}, активных: {sum(r[2] == 'active' for r in rows)}")
    print(
        "cooldown_utc: аккаунт отдыхает после 429 до этого времени; остальное как в accounts.py list"
    )


async def cmd_proxy(st: IgSettings, name: str, url: str | None, clear: bool) -> None:
    """Прокси аккаунта (колонка accounts.proxy); без url и --clear показывает текущий."""
    reg = await load_registry(st)
    if name not in reg:
        raise SystemExit(f"Аккаунта «{name}» нет в реестре (cli.py ig accounts list)")
    if not url and not clear:
        print(f"{name}: {reg[name].get('proxy') or 'общий PROXY из .env (или без прокси)'}")
        return
    await update_account(st, name, proxy=None if clear else url.strip())
    print(f"[+] {name}: прокси " + ("сброшен" if clear else "задан"))


async def cmd_cooldown(st: IgSettings, name: str, seconds: int | None, clear: bool) -> None:
    """Ручная пауза аккаунта (или снятие): по умолчанию IG_429_PAUSE секунд."""
    reg = await load_registry(st)
    if name not in reg:
        raise SystemExit(f"Аккаунта «{name}» нет в реестре (cli.py ig accounts list)")
    await set_cooldown(st, name, 0 if clear else seconds)
    print(f"[+] {name}: пауза " + ("снята" if clear else "установлена"))


async def cmd_import_legacy(st: IgSettings, file: Path | None, cookies_dir: Path | None) -> None:
    """Разовый перенос реестра insta-parser (accounts.json) и, по желанию, файлов кук.

    Только записи аккаунтов; статистика и история запусков не переносятся."""
    path = file or st.accounts_file
    if not path.exists():
        raise SystemExit(f"Файл не найден: {path} (укажите путь к accounts.json insta-parser)")
    try:
        reg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path}: не JSON ({e})") from e
    if not isinstance(reg, dict):
        raise SystemExit(f"{path}: ожидался объект {{имя: запись}}")
    async with connection(st, IG_SCHEMA) as conn:
        added, skipped = await repo.import_legacy(conn, reg)
    print(f"[+] Аккаунтов добавлено: {added}, уже были в БД: {skipped}")
    if cookies_dir:
        st.cookies_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for name in reg:
            src, dst = cookies_dir / f"{name}.json", st.cookies_path(name)
            if src.exists() and not dst.exists():
                shutil.copy2(src, dst)
                copied += 1
        print(f"[+] Файлов кук скопировано: {copied} → {st.cookies_dir}")
    else:
        print(
            f"[*] Куки не копировались: положите файлы в {st.cookies_dir} или укажите --cookies-dir"
        )
    print("    Проверить сессии: cli.py ig accounts check")
