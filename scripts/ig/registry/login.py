"""Ручной вход в аккаунт Instagram в окне браузера."""

import shutil

from browser.launch import launch_browser, new_context
from config import IG, IgSettings
from ig.browser.proxy import account_proxy
from ig.browser.session import check_session, has_session, save_cookies, session_user_id
from ig.registry.store import find_duplicate, load_registry, register_login
from registry.login import confirm, dup_question


async def login_account(p, st: IgSettings, name: str) -> tuple[str | None, str | None]:
    """Чистый браузер с окном, ожидание ручного входа, сохранение кук.

    Возвращает (id аккаунта, ник или None)."""
    browser = await launch_browser(p, headless=False, proxy=await account_proxy(st, name))
    try:
        ctx = await new_context(browser)
        page = await ctx.new_page()
        user_agent = await page.evaluate("navigator.userAgent")
        await page.goto(f"{IG}/accounts/login/", wait_until="domcontentloaded")
        print(
            f"[*] Войдите в Instagram в открывшемся окне (до {st.login_timeout // 60} мин)...",
            flush=True,
        )
        for _ in range(st.login_timeout):
            if await has_session(ctx):
                break
            await page.wait_for_timeout(1000)
        else:
            raise TimeoutError("время ожидания входа истекло")

        await page.wait_for_timeout(4000)  # дать странице выставить остальные куки
        username = await check_session(page)
        user_id = await session_user_id(ctx)
        await save_cookies(ctx, user_agent, st.cookies_path(name))
        return user_id, (None if username in (None, "?") else username)
    finally:
        await browser.close()


async def login_into(p, st: IgSettings, name: str, assume_yes: bool = False) -> bool:
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
