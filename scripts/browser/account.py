"""Аккаунт-воркер: браузер, куки, прокси."""

from browser.launch import account_proxy, launch_browser, new_context, proxy_settings
from browser.session import check_session, has_session, load_cookies, save_cookies
from config import Settings
from core.logs import log


class Account:
    """Один аккаунт Threads: один браузер, один контекст с куками, m воркеров-пар вкладок.

    Куки читаются из data/cookies/<имя>.json. Вход в браузере здесь не выполняется:
    он вынесен в accounts.py (add / relogin), чтобы парсер работал на сервере без окна.
    """

    def __init__(self, name: str, st: Settings, args):
        self.name = name
        self.st = st
        self.args = args
        self.cookies_file = st.cookies_path(name)
        self.browser = None
        self.ctx = None
        self.user_agent = None
        self.proxy = None
        self.use_api = True  # прямая загрузка медиа; при сбое качаем через вкладку

    async def open(self, p):
        """Поднимает сессию. Возвращает первую вкладку (уже открыта главная Threads).

        Бросает SystemExit с понятным сообщением, если кук нет, сессия мертва или
        браузер не открылся.
        """
        data = load_cookies(self.cookies_file)
        if not data:
            raise SystemExit(
                f"У аккаунта «{self.name}» нет рабочего файла кук "
                f"({self.cookies_file.name}): "
                "uv run scripts/accounts.py relogin " + self.name
            )

        self.proxy = await account_proxy(self.st, self.name)
        if self.proxy:
            log(f"[*] {self.name}: прокси {proxy_settings(self.proxy)['server']}")
        self.browser = await launch_browser(p, self.args.headless, self.proxy)
        ok = None
        page = None
        failed = None
        try:
            self.ctx = await new_context(self.browser, data)
            page = await self.ctx.new_page()
            ok = await check_session(page)
        except Exception as e:
            failed = str(e).splitlines()[0][:150]
            log(f"[!] Не удалось проверить сессию: {failed}")
        if not ok:
            await self.browser.close()
            self.browser = self.ctx = None
            if failed:  # сеть/браузер: статус аккаунта не трогаем
                raise SystemExit(f"Не удалось проверить сессию аккаунта «{self.name}»: {failed}")
            raise SystemExit(
                f"Сессия аккаунта «{self.name}» недействительна: "
                f"uv run scripts/accounts.py relogin {self.name}"
            )
        self.user_agent = data["user_agent"]
        log(f"[+] Сессия аккаунта «{self.name}» действует")
        return page

    async def close(self) -> None:
        """Обновляет куки (Threads ротирует часть значений) и закрывает браузер."""
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
