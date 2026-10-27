"""Аккаунт-воркер: браузер, куки, прокси, статистика в реестре."""

from browser.launch import account_proxy, launch_browser, new_context, proxy_settings
from browser.session import check_session, has_session, load_cookies, save_cookies
from config import Settings
from core.logs import log
from registry.store import apply_stats, load_registry, now_iso, save_registry


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

        self.proxy = account_proxy(self.st, self.name)
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


def mark_account(
    st: Settings,
    name: str,
    status: str | None = None,
    error: str | None = None,
    stats: dict | None = None,
    username: str | None = None,
    profiles: set[str] | None = None,
    run: dict | None = None,
) -> None:
    """Отмечает в реестре запуск аккаунта: время, ошибку, статистику, найденный ник.

    profiles: ники, успешно обработанные в этом запуске. Счётчик «profiles» это число
    УНИКАЛЬНЫХ профилей за всё время (повторный прогон тех же профилей его не меняет),
    поэтому список ников хранится в поле profiles_seen.
    run: подробная запись об этом запуске (время, настройки, итоги, разбивка по
    профилям); добавляется в список runs, runs_total считает запуски числом.
    """
    reg = load_registry(st)
    if name in reg:
        rec = reg[name]
        rec["last_run_at"] = now_iso()
        rec["last_error"] = error
        apply_stats(rec, stats or {})
        if not isinstance(rec.get("runs"), list):  # старый формат: runs было числом
            rec["runs_total"] = int(rec.get("runs") or 0)
            rec["runs"] = []
        rec["runs_total"] = int(rec.get("runs_total") or 0) + 1
        if run is not None:
            rec["runs"].append(run)
        if profiles is not None:
            seen = {u.lower() for u in rec.get("profiles_seen") or []}
            seen |= {u.lower() for u in profiles}
            rec["profiles_seen"] = sorted(seen)
            rec["profiles"] = len(seen)
        if status:
            rec["status"] = status
        if username and not rec.get("username"):
            rec["username"] = username
        save_registry(st, reg)
