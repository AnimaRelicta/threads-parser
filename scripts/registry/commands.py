"""Команды accounts.py: add, relogin, check, import, export, list."""

import json
import zipfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

from browser.launch import account_proxy, launch_browser, new_context
from browser.session import check_session, detect_username, load_cookies, session_user_id
from config import Settings
from registry.cookies import DEFAULT_USER_AGENT, cookie_jar, normalize_cookie
from registry.login import confirm, dup_question, login_into
from registry.store import (
    find_duplicate,
    load_registry,
    new_record,
    next_name,
    now_iso,
    save_registry,
    sync_cookie_files,
)


async def cmd_add(st: Settings, count: int, prefix: str, assume_yes: bool) -> None:

    ok = 0
    async with async_playwright() as p:
        for i in range(count):
            name = next_name(st, load_registry(st), prefix)
            print(f"\n=== Аккаунт {i + 1} из {count}: {name} ===", flush=True)
            if await login_into(p, st, name, assume_yes):
                ok += 1
            else:
                st.cookies_path(name).unlink(missing_ok=True)
    print(f"\n[✓] Сохранено: {ok} из {count}. Куки: {st.cookies_dir}")
    print("    Для сервера: uv run scripts/accounts.py export")


async def cmd_relogin(st: Settings, names: list[str]) -> None:

    reg = load_registry(st)
    async with async_playwright() as p:
        for name in names:
            if name not in reg:
                print(f"[!] Аккаунта «{name}» нет в реестре (accounts.py list)")
                continue
            print(f"\n=== Повторный вход: {name} ===", flush=True)
            await login_into(p, st, name)


async def cmd_check(st: Settings, names: list[str], headless: bool) -> None:

    sync_cookie_files(st)
    reg = load_registry(st)
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
                    reg[name]["status"] = "needs_login"
                continue
            browser = await launch_browser(p, headless, account_proxy(st, name))
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
                if nick:
                    reg[name]["username"] = nick
                if alive and reg[name].get("status") == "needs_login":
                    reg[name]["status"] = "active"
                elif not alive:
                    reg[name]["status"] = "needs_login"
    save_registry(st, reg)


def import_blob(
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

    reg = load_registry(st)
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
        reg[dup].update(status="active", last_login_at=now_iso(), last_error=None)
        print(f"[+] Сессия «{dup}» заменена на новую (id {user_id})")
    else:
        reg[target] = new_record(target, username, user_id)
        print(f"[+] Аккаунт добавлен как «{target}» (id {user_id or '?'})")
    save_registry(st, reg)
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


def cmd_import(
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
        target = import_blob(
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


def cmd_export(st: Settings, names: list[str], out: Path | None) -> None:
    sync_cookie_files(st)
    reg = load_registry(st)
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


def cmd_list(st: Settings) -> None:
    sync_cookie_files(st)
    reg = load_registry(st)
    if not reg:
        print("Реестр пуст: uv run scripts/accounts.py add 1")
        return
    cols = (
        "name",
        "user",
        "status",
        "runs",
        "profiles",
        "posts",
        "comments",
        "media",
        "429",
        "auth",
        "last_run",
        "error",
    )
    rows = []
    for n, r in reg.items():
        user = f"@{r['username']}" if r.get("username") else f"id:{r.get('user_id') or '?'}"
        rows.append(
            (
                n,
                user,
                r.get("status", "?"),
                (r.get("runs_total") if isinstance(r.get("runs"), list) else r.get("runs")) or 0,
                r.get("profiles") or 0,
                r.get("posts") or 0,
                r.get("comments") or 0,
                r.get("media_files") or 0,
                r.get("errors_429") or 0,
                r.get("errors_auth") or 0,
                (r.get("last_run_at") or "-")[:16].replace("T", " "),
                (r.get("last_error") or "")[:30],
            )
        )
    widths = [max(len(str(x)) for x in col) for col in zip(cols, *rows)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*cols))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*map(str, row)))
    print(
        f"\nВсего: {len(reg)}, активных: {sum(r.get('status') == 'active' for r in reg.values())}"
    )
    print(
        "runs: запуски (детали в поле runs у аккаунта); media: скачанные файлы; profiles: уникальные обработанные профили; posts/comments: новые посты и "
        "комментарии; 429 и auth: ответы 429 и проблемы авторизации"
    )
