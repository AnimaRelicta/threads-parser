"""Ручной вход в аккаунт в окне браузера."""

import shutil
import sys
import threading
import time

from browser.launch import account_proxy, launch_browser, new_context
from browser.session import detect_username, has_session, save_cookies
from config import TH, TH_URLS, Settings
from registry.store import find_duplicate, load_registry, register_login


def confirm(question: str, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("[!] Нужно подтверждение, а ввод недоступен: добавьте флаг --yes")
        return False
    return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes", "д", "да")


def dup_question(who: str, dup: str) -> str:
    return (
        f"Аккаунт {who} уже есть в реестре как «{dup}». Новая сессия заменит "
        "текущую. Вы уверены, что хотите добавить новую сессию?"
    )


async def login_account(p, st: Settings, name: str) -> tuple[str | None, str | None]:
    """Открывает чистый браузер, ждёт ручного входа, сохраняет куки.
    Возвращает (id аккаунта, ник или None)."""

    browser = await launch_browser(p, headless=False, proxy=await account_proxy(st, name))
    try:
        ctx = await new_context(browser)
        page = await ctx.new_page()
        user_agent = await page.evaluate("navigator.userAgent")
        await page.goto(f"{TH}/login", wait_until="domcontentloaded")
        print(
            f"[*] Войдите в Threads в открывшемся окне (до {st.login_timeout // 60} мин). "
            "Если вход не определится сам, нажмите Enter здесь, когда увидите ленту.",
            flush=True,
        )

        confirmed = threading.Event()

        def wait_enter() -> None:
            try:
                input()
            except Exception:
                return
            confirmed.set()

        threading.Thread(target=wait_enter, daemon=True).start()

        deadline = time.time() + st.login_timeout
        while time.time() < deadline:
            if await has_session(ctx) or confirmed.is_set():
                break
            await page.wait_for_timeout(1000)
        else:
            raise TimeoutError("время ожидания входа истекло")

        await page.wait_for_timeout(4000)  # дать странице выставить остальные куки
        cookies = await ctx.cookies(TH_URLS)
        user_id = next((c["value"] for c in cookies if c["name"] == "ds_user_id"), None)
        username = None
        try:  # ник берём с главной страницы уже залогиненной сессии
            await page.goto(f"{TH}/", wait_until="domcontentloaded")
            await page.wait_for_timeout(2500)
            username = await detect_username(page, user_id)
        except Exception as e:
            print(f"[*] Ник определить не удалось: {str(e)[:100]}")
        await save_cookies(ctx, user_agent, st.cookies_path(name))
        return user_id, username
    finally:
        await browser.close()


async def login_into(p, st: Settings, name: str, assume_yes: bool = False) -> bool:
    """Вход под именем name с проверкой дубля по id. True, если сессия сохранена."""
    try:
        user_id, username = await login_account(p, st, name)
    except Exception as e:
        print(f"[!] Не удалось войти: {str(e)[:200]}")
        return False
    reg = await load_registry(st)
    dup = find_duplicate(reg, user_id)
    if dup and dup != name:
        if not confirm(dup_question(f"с id {user_id}", dup), assume_yes):
            print("[!] Отменено: текущая сессия не изменена")
            return False
        shutil.move(st.cookies_path(name), st.cookies_path(dup))
        await register_login(st, dup, username, None)
        print(f"[+] Сессия «{dup}» заменена на новую (id {user_id})")
        return True
    await register_login(st, name, username, user_id)
    who = f"@{username}, " if username else ""
    print(f"[+] Сохранён «{name}» ({who}id {user_id or '?'})")
    return True
