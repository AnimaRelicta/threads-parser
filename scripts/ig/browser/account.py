"""Аккаунт-воркер Instagram: браузер, куки, прокси (по образцу browser/account.py)."""

from browser.launch import launch_browser, new_context, proxy_settings
from config import IgSettings
from core.logs import log
from ig.browser.proxy import account_proxy
from ig.browser.session import check_session, has_session, load_cookies, save_cookies


class IgAccount:
    """Один аккаунт Instagram: один браузер, один контекст с куками.

    Куки читаются из data/ig_cookies/<имя>.json. Вход здесь не выполняется: он вынесен
    в `cli.py ig accounts add|relogin`, чтобы парсер работал на сервере без окна."""

    def __init__(self, name: str, st: IgSettings, headless: bool):
        self.name = name
        self.st = st
        self.headless = headless
        self.cookies_file = st.cookies_path(name)
        self.browser = None
        self.ctx = None
        self.user_agent = None
        self.proxy = None
        self.nick: str | None = None  # ник аккаунта (None — не определён)

    async def open(self, p):
        """Поднимает сессию и возвращает первую вкладку (открыта главная Instagram).

        SystemExit с понятным сообщением: нет кук, сессия недействительна, браузер не открылся."""
        data = load_cookies(self.cookies_file)
        if not data:
            raise SystemExit(
                f"У аккаунта «{self.name}» нет рабочего файла кук ({self.cookies_file.name}): "
                f"cli.py ig accounts relogin {self.name}"
            )
        self.proxy = await account_proxy(self.st, self.name)
        if self.proxy:
            log(f"[*] {self.name}: прокси {proxy_settings(self.proxy)['server']}")
        self.browser = await launch_browser(p, self.headless, self.proxy)
        alive = page = failed = None
        try:
            self.ctx = await new_context(self.browser, data)
            page = await self.ctx.new_page()
            alive = await check_session(page)
        except Exception as e:
            failed = str(e).splitlines()[0][:150]
            log(f"[!] Не удалось проверить сессию: {failed}")
        if not alive:
            await self.browser.close()
            self.browser = self.ctx = None
            if failed:  # сеть/браузер: статус аккаунта не трогаем
                raise SystemExit(f"Не удалось проверить сессию аккаунта «{self.name}»: {failed}")
            raise SystemExit(
                f"Сессия аккаунта «{self.name}» недействительна: "
                f"cli.py ig accounts relogin {self.name}"
            )
        self.nick = alive if alive != "?" else None
        self.user_agent = data["user_agent"]
        log(f"[+] Сессия аккаунта «{self.name}» действует")
        return page

    async def close(self) -> None:
        """Обновляет куки (Instagram их ротирует) и закрывает браузер."""
        if self.ctx is not None and self.user_agent:
            try:
                if await has_session(self.ctx):
                    await save_cookies(self.ctx, self.user_agent, self.cookies_file)
            except Exception:
                pass
        if self.browser is not None:
            try:
                await self.browser.close()
            except Exception:
                pass
