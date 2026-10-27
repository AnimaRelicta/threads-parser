"""Команды accounts.py: add, relogin, check, import, export, list, proxy."""

import json
import zipfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from browser.launch import account_proxy, launch_browser, new_context
from browser.session import check_session, detect_username, load_cookies, session_user_id
from config import Settings
from db.pool import connection
from db.stats import REPORTS, format_table, run_report
from registry.cookies import DEFAULT_USER_AGENT, cookie_jar, normalize_cookie
from registry.login import confirm, dup_question, login_into
from registry.store import (
    find_duplicate,
    load_registry,
    next_name,
    register_login,
    sync_cookie_files,
    update_account,
)


async def cmd_add(st: Settings, count: int, prefix: str, assume_yes: bool) -> None:

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
    print("    Для сервера: uv run scripts/accounts.py export")


async def cmd_relogin(st: Settings, names: list[str]) -> None:

    reg = await load_registry(st)
    async with async_playwright() as p:
        for name in names:
            if name not in reg:
                print(f"[!] Аккаунта «{name}» нет в реестре (accounts.py list)")
                continue
            print(f"\n=== Повторный вход: {name} ===", flush=True)
            await login_into(p, st, name)


async def cmd_check(st: Settings, names: list[str], headless: bool) -> None:

    await sync_cookie_files(st)
    reg = await load_registry(st)
    names = names or list(reg)
    if not names:
        print("Реестр пуст: uv run scripts/accounts.py add 1")
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
            nick = None
            try:
                ctx = await new_context(browser, data)
                page = await ctx.new_page()
                alive = await check_session(page)
                if alive and name in reg and not reg[name].get("username"):
                    uid = await session_user_id(ctx) or reg[name].get("user_id")
                    nick = await detect_username(page, uid)
            except Exception as e:
                print(f"  {name}: ошибка проверки ({str(e)[:120]})")
                alive = None
            finally:
                await browser.close()
            print(
                f"  {name}: "
                + ("сессия действует" if alive else "сессия не действует → relogin")
                + (f" (@{nick})" if nick else "")
            )
            if name in reg:
                fields: dict = {}
                if nick:
                    fields["username"] = nick
                if alive and reg[name].get("status") == "needs_login":
                    fields["status"] = "active"
                elif not alive:
                    fields["status"] = "needs_login"
                if fields:
                    await update_account(st, name, **fields)


async def import_blob(
    st: Settings,
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
    cookies = [n for n in map(normalize_cookie, raw_cookies) if n]
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
    username = (meta or {}).get("username")
    if dup:
        await register_login(st, dup, None, None)
        print(f"[+] Сессия «{dup}» заменена на новую (id {user_id})")
    else:
        await register_login(st, target, username, user_id)
        print(f"[+] Аккаунт добавлен как «{target}» (id {user_id or '?'})")
    return target


def collect_bundle(file: Path) -> tuple[list[tuple[str, bytes]], dict]:
    """Файлы кук и реестр из архива, папки или одиночного файла."""
    items: list[tuple[str, bytes]] = []
    registry: dict = {}

    def take(stem: str, fname: str, raw: bytes) -> None:
        if fname == "accounts.json":
            try:
                reg = json.loads(raw.decode("utf-8"))
                if isinstance(reg, dict):
                    registry.update(reg)
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        elif fname.endswith(".json"):
            items.append((stem, raw))

    if file.is_dir():
        for f in sorted(file.rglob("*.json")):
            take(f.stem, f.name, f.read_bytes())
    elif zipfile.is_zipfile(file):
        with zipfile.ZipFile(file) as z:  # читаем в память, на диск не распаковываем
            for info in z.infolist():
                if not info.is_dir():
                    n = Path(info.filename)
                    take(n.stem, n.name, z.read(info))
    else:
        take(file.stem, file.name, file.read_bytes())
    return items, registry


async def cmd_import(
    st: Settings, file: Path, name: str | None, assume_yes: bool, user_agent: str | None
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
        # из архива/папки сохраняем имена файлов; одиночный файл получает свободное имя
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
        print("    Проверить сессии: uv run scripts/accounts.py check")


async def cmd_export(st: Settings, names: list[str], out: Path | None) -> None:
    await sync_cookie_files(st)
    reg = await load_registry(st)
    names = [n for n in (names or list(reg)) if st.cookies_path(n).exists()]
    if not names:
        raise SystemExit("Нет аккаунтов с файлами кук. Сначала: uv run scripts/accounts.py add 3")
    out = out or st.export_dir / f"accounts_{datetime.now():%Y%m%d_%H%M%S}.zip"
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
    print(f"    На сервере: uv run scripts/accounts.py import {out.name}")


async def cmd_list(st: Settings) -> None:
    await sync_cookie_files(st)
    report = REPORTS["accounts"]
    async with connection(st) as conn:
        cols, rows = await run_report(conn, report)
    if not rows:
        print("Реестр пуст: uv run scripts/accounts.py add 1")
        return
    print(format_table(cols, rows))
    status_i = cols.index("status")
    print(f"\nВсего: {len(rows)}, активных: {sum(r[status_i] == 'active' for r in rows)}")
    print(report.note)


async def cmd_proxy(st: Settings, name: str, url: str | None, clear: bool) -> None:
    """Прокси аккаунта (колонка accounts.proxy); без url и --clear показывает текущий."""
    reg = await load_registry(st)
    if name not in reg:
        raise SystemExit(f"Аккаунта «{name}» нет в реестре (accounts.py list)")
    if not url and not clear:
        print(f"{name}: {reg[name].get('proxy') or 'общий PROXY из .env (или без прокси)'}")
        return
    await update_account(st, name, proxy=None if clear else url.strip())
    print(f"[+] {name}: прокси " + ("сброшен" if clear else "задан"))
