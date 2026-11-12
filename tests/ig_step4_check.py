"""Проверка шага 4 Instagram без БД, сети и браузера: разбор профиля, очередь, выбор профилей."""

import argparse
import asyncio
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import asyncpg  # noqa: F401
except ImportError:
    fake = types.ModuleType("asyncpg")
    for name in ("Connection", "Pool", "PostgresError", "InterfaceError", "Record"):
        setattr(fake, name, type(name, (Exception,), {}))
    fake.UniqueViolationError = type("UniqueViolationError", (fake.PostgresError,), {})
    sys.modules["asyncpg"] = fake
try:
    import playwright  # noqa: F401
except ImportError:
    pw = types.ModuleType("playwright")
    api = types.ModuleType("playwright.async_api")
    api.Error = type("Error", (Exception,), {})
    api.async_playwright = lambda: None
    pw.async_api = api
    sys.modules.update({"playwright": pw, "playwright.async_api": api})

from config import IgSettings  # noqa: E402
from ig import cli as ig_cli  # noqa: E402
from ig import parse_cli  # noqa: E402
from ig.db import profiles as repo  # noqa: E402
from ig.db.store import IgStore  # noqa: E402
from ig.extract import (  # noqa: E402
    classify_api,
    find_embedded_user,
    meta_from_og,
    meta_from_text,
    not_found_from_text,
    parse_count,
    private_from_text,
    user_from_dict,
)
from ig import about as about_parse  # noqa: E402
from ig.scrape import about as about_scrape  # noqa: E402
from ig.scrape import dispatch, meta  # noqa: E402
from ig.scrape.report import new_result, profile_run_entry, write_summary  # noqa: E402

# --- ответ API веб-профиля (вид graphql) и вид users/{id}/info
web = {
    "data": {
        "user": {
            "id": "123",
            "username": "shop_kz",
            "full_name": "Shop KZ",
            "biography": "Алматы\nдоставка",
            "bio_links": [{"url": "https://x.kz", "title": "сайт"}, {"title": "без url"}],
            "external_url": "https://x.kz",
            "category_name": "Shopping",
            "edge_followed_by": {"count": 1500},
            "edge_follow": {"count": 10},
            "edge_owner_to_timeline_media": {"count": 42},
            "is_private": False,
            "is_verified": True,
            "is_business_account": True,
            "profile_pic_url_hd": "https://cdn/hd.jpg",
        }
    },
    "status": "ok",
}
kind, user = classify_api(web)
assert kind == "user"
m = user_from_dict(user)
assert (m["ig_user_id"], m["followers_count"], m["following_count"], m["posts_count"]) == (
    "123",
    1500,
    10,
    42,
)
assert m["bio_links"] == [{"url": "https://x.kz", "title": "сайт"}]
assert (m["is_private"], m["is_verified"], m["is_business"]) == (False, True, True)
assert m["category"] == "Shopping" and m["profile_pic_url"].endswith("hd.jpg")
flat = user_from_dict({"pk": 7, "follower_count": 5, "media_count": 0, "is_business": False})
assert (flat["ig_user_id"], flat["followers_count"], flat["posts_count"]) == ("7", 5, 0)
assert flat["is_business"] is False  # False не теряется при выборе из вариантов имён

assert classify_api({"error": 429}) == ("rate", None)
assert classify_api({"error": 401}) == ("auth", None)
assert classify_api({"error": 404}) == ("unknown", None)  # 404 эндпоинта — не «нет профиля»
assert classify_api({"error": "bad_json"}) == ("unknown", None)
assert classify_api({"data": {"user": None}, "status": "ok"}) == ("not_found", None)
assert classify_api({"user": {"username": "a"}})[0] == "user"
assert classify_api(None) == ("unknown", None) and classify_api({}) == ("unknown", None)

# --- запасные пути: og:* и embedded JSON
og = meta_from_og(
    "Foo Bar (@foo.bar) • Instagram photos and videos",
    '1,234 Followers, 567 Following, 89 Posts - Foo Bar (@foo.bar) on Instagram: "hi\nthere"',
)
assert og == {
    "followers_count": 1234,
    "following_count": 567,
    "posts_count": 89,
    "full_name": "Foo Bar",
    "biography": "hi\nthere",
}
assert meta_from_og(None, None) == {}
assert meta_from_og("x", "1.2M Followers, 3 Following, 5 Posts")["followers_count"] == 1_200_000
assert (parse_count("12 345"), parse_count("1,5К"), parse_count("")) == (12345, 1500, None)
docs = [{"a": [{"username": "Other", "biography": "x"}, {"username": "Shop_KZ", "full_name": "S"}]}]
assert find_embedded_user(docs, "shop_kz") == {"username": "Shop_KZ", "full_name": "S"}
assert find_embedded_user(docs, "nobody") is None
assert private_from_text("This account is private") and not private_from_text("hello")
assert parse_count("9,2 млн") == 9_200_000 and parse_count("3 300") == 3300
assert not_found_from_text("Page Not Found • Instagram", "")
assert not_found_from_text(None, "Эта страница недоступна")
assert not not_found_from_text("Instagram", "garik | 3 300 публикаций")
HEADER = (
    "garikkharlamov\nГарик Харламов\n3 300 публикаций\n9,2 млн подписчиков\n282 подписок\n"
    "Юморист\nПо вопросам сотрудничества: +79037963079 Ксения\nt.me/garbul\ngarikkharlamov\n"
    "Подписаться\nОтправить сообщение\nMeta\nИнформация"
)
shown = meta_from_text(HEADER, "garikkharlamov")  # реальный текст страницы из диагностики
counts = (shown["posts_count"], shown["followers_count"], shown["following_count"])
assert counts == (3300, 9_200_000, 282)
assert shown["full_name"] == "Гарик Харламов"
assert shown["biography"] == "Юморист\nПо вопросам сотрудничества: +79037963079 Ксения"
assert shown["bio_links"] == [{"url": "https://t.me/garbul", "title": None}]
en = meta_from_text("bob\nBob B\n12 posts\n1,234 followers\n56 following\nFollow", "bob")
assert (en["posts_count"], en["followers_count"], en["following_count"]) == (12, 1234, 56)
assert en["full_name"] == "Bob B" and "biography" not in en
assert meta_from_text("ничего похожего", "x") == {}

# --- выбор профилей: порядок, дубли, only-unprocessed (заглушка соединения)
assert repo.MODE_RANK == {"profile": 1, "posts": 2, "full": 3}


class FakeConn:
    """Минимум для select_profiles: хранит профили и режим обработки."""

    def __init__(self):
        self.rows = {
            "a": {"id": 1, "username": "a", "processed_mode": "profile", "target": True},
            "b": {"id": 2, "username": "b", "processed_mode": None, "target": True},
            "c": {"id": 3, "username": "c", "processed_mode": "full", "target": True},
            "d": {"id": 4, "username": "d", "processed_mode": None, "target": False},
        }
        self.added: list[str] = []

    def transaction(self):
        class _Tx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        return _Tx()

    async def executemany(self, sql, rows):
        pass  # слияние источников у существующих строк здесь не проверяется

    async def fetch(self, sql, *args):
        if "FROM profiles WHERE is_target ORDER" in sql:
            return [r for r in self.rows.values() if r["target"]]
        if "lower(username) = ANY" in sql and "SELECT id, lower(username) AS nick" in sql:
            return [{**r, "nick": r["username"], "sources": [], "source": "cli",
                     "link_confidence": None, "link_source": None,
                     "threads_profile_id": None, "threads_username": None}
                    for r in self.rows.values() if r["username"] in args[0]]
        if sql.lstrip().startswith("INSERT INTO profiles"):
            fresh = [n for n in args[0] if n not in self.rows]
            for n in fresh:
                self.rows[n] = {"id": 10 + len(self.rows), "username": n,
                                "processed_mode": None, "target": True}
            self.added += fresh
            return [{"nick": n} for n in fresh]
        if "processed_mode" in sql and "id = ANY($1)" in sql:  # drop_processed
            ids, need = args
            rank = {None: 0, "profile": 1, "posts": 2, "full": 3}
            return [{"id": r["id"]} for r in self.rows.values()
                    if r["id"] in ids and rank[r["processed_mode"]] < need]
        raise AssertionError(sql)


def ns(**kw) -> argparse.Namespace:
    base = dict(nicks=[], file=None, source=None, only_unprocessed=False, dry_run=False)
    return argparse.Namespace(**{**base, **kw})


async def select_flow() -> None:
    from ig.scrape.select import select_profiles

    conn = FakeConn()
    names, ids = await select_profiles(conn, ns(source="all"), "profile")
    assert names == ["a", "b", "c"] and ids == {"a": 1, "b": 2, "c": 3}  # d не is_target
    names, ids = await select_profiles(conn, ns(source="all", only_unprocessed=True), "profile")
    assert names == ["b"] and ids == {"b": 2}  # a, c уже обработаны в profile и глубже
    names, ids = await select_profiles(
        conn, ns(nicks=["@New1", "b", "bad nick"], source="all"), "profile"
    )
    assert names[:2] == ["new1", "b"] and "new1" in conn.added and "b" not in conn.added
    conn2 = FakeConn()
    names, _ = await select_profiles(conn2, ns(nicks=["zzz"], dry_run=True), "profile")
    assert names == ["zzz"] and conn2.added == []  # dry-run ничего не пишет


asyncio.run(select_flow())

# --- очередь: возврат профиля, конец работы, остановка аккаунта
async def queue_flow() -> None:
    d = dispatch.Dispatcher(["x", "y"])
    halt = asyncio.Event()
    assert await d.take(halt) == ("x", 0)
    d.put(("x", 1))
    d.done()
    assert await d.take(halt) == ("y", 0)
    assert await d.take(halt) == ("x", 1)
    d.done()
    d.done()
    assert await d.take(halt) is None  # пусто и никто не работает
    halt.set()
    d.put(("z", 0))
    assert await d.take(halt) is None  # остановленный аккаунт ничего не берёт


asyncio.run(queue_flow())


# --- профиль-only на заглушке страницы и хранилища
class FakePage:
    def __init__(self, status=200, url="https://www.instagram.com/shop_kz/", api=None, page=None):
        self.status, self.url, self.api, self.page_info = status, url, api or {}, page
        self.calls: list[str] = []

    async def goto(self, url, wait_until=None):
        self.calls.append(url)
        return types.SimpleNamespace(status=self.status)

    async def wait_for_timeout(self, ms):
        pass

    async def wait_for_selector(self, selector, timeout=None):
        pass

    async def evaluate(self, js, arg=None):
        if arg is None:
            if "innerText" in js and "og:title" not in js:
                return (self.page_info or {}).get("text", "")
            if "og:title" in js:
                return self.page_info or {
                    "title": "Instagram",
                    "og_title": None,
                    "og_description": None,
                    "text": "",
                }
            return []
        self.calls.append(arg)
        for key, val in self.api.items():
            if key in arg:
                return val
        return {"error": "bad_json"}


class FakeStore:
    def __init__(self, stored_id=None):
        self.stored_id, self.saved, self.renamed, self.errors = stored_id, [], [], []

    async def stored_user_id(self, username):
        return self.stored_id

    async def save_meta(self, username, status, m, raw):
        self.saved.append((username, status, m, raw))

    async def rename(self, username, new):
        self.renamed.append((username, new))
        return True

    async def mark_error(self, username):
        self.errors.append(username)

    async def about_fetched(self, username):
        return getattr(self, "fetched", False)

    async def save_about(self, username, fields, raw):
        self.about = (username, fields, raw)


async def profile_flow() -> None:
    args = types.SimpleNamespace(debug=False, no_about=True)
    # обычный профиль
    page, store = FakePage(api={"web_profile_info": web}), FakeStore()
    r = await meta.run_profile_only(page, "shop_kz", args, store, "acc1")
    assert r["status"] == "ok" and r["followers"] == 1500 and r["stop_reason"] == "end"
    u, status, m, raw = store.saved[0]
    assert (u, status, m["ig_user_id"]) == ("shop_kz", "ok", "123")
    assert raw["source"] == "api" and raw["raw"]["username"] == "shop_kz"
    # закрытый профиль считается обработанным: статус private, мета сохранена
    priv = {"data": {"user": {**web["data"]["user"], "is_private": True}}}
    store = FakeStore()
    r = await meta.run_profile_only(FakePage(api={"web_profile_info": priv}), "s", args, store, "a")
    assert r["status"] == "private" and store.saved[0][1] == "private"
    # 429 и вход: abort, в БД ничего не пишется
    for resp, kind in ((429, "429"),):
        store = FakeStore()
        r = await meta.run_profile_only(FakePage(status=resp), "s", args, store, "a")
        assert (r["status"], r["abort_kind"]) == ("abort", kind) and not store.saved
    store = FakeStore()
    r = await meta.run_profile_only(
        FakePage(url="https://www.instagram.com/accounts/login/"), "s", args, store, "a"
    )
    assert (r["status"], r["abort_kind"]) == ("abort", "auth") and not store.saved
    r = await meta.run_profile_only(
        FakePage(api={"web_profile_info": {"error": 429}}), "s", args, FakeStore(), "a"
    )
    assert (r["status"], r["abort_kind"]) == ("abort", "429")
    r = await meta.run_profile_only(
        FakePage(api={"web_profile_info": {"error": 401}}), "s", args, FakeStore(), "a"
    )
    assert (r["status"], r["abort_kind"]) == ("abort", "auth")
    # 404 эндпоинта API при живой странице: данные берутся со страницы (случай из диагностики)
    page = FakePage(
        api={"web_profile_info": {"error": 404}},
        page={"title": "Instagram", "og_title": None, "og_description": None, "text": HEADER},
    )
    store = FakeStore()
    r = await meta.run_profile_only(page, "garikkharlamov", args, store, "a")
    assert r["status"] == "ok" and r["followers"] == 9_200_000 and "со страницы: text" in r["note"]
    assert store.saved[0][3]["source"] == "text" and "Гарик" in store.saved[0][3]["raw"]["text"]
    # не найден: 404 страницы и страница «недоступна» (200); без ig_user_id нет переименования
    gone = {"title": "Page Not Found • Instagram", "og_title": None, "og_description": None}
    gone["text"] = ""
    for pg in (FakePage(status=404), FakePage(page=gone)):
        store = FakeStore()
        r = await meta.run_profile_only(pg, "gone", args, store, "a")
        assert r["status"] == "not_found" and len(store.saved) == 1
        u, st, m, raw = store.saved[0]
        assert (u, st, m) == ("gone", "not_found", None)
        assert raw["source"] in ("http", "page") and raw["raw"]["text"], raw  # причина в meta_raw
    gone["text"] = "К сожалению, эта страница недоступна.\nВозможно, ссылка недействительна. Назад в Instagram.\nMeta\nО нас"
    store = FakeStore()
    await meta.run_profile_only(FakePage(page=gone), "gone", args, store, "a")
    assert store.saved[0][3] == {
        "source": "page",
        "raw": {"text": "К сожалению, эта страница недоступна.\nВозможно, ссылка недействительна. Назад в Instagram."},
    }, store.saved[0][3]
    # смена ника: старый ник 404, по сохранённому id находим новый
    page = FakePage(
        api={
            "/users/555/info": {"user": {"username": "new_nick", "id": "555"}},
        },
        page=gone,
    )
    store = FakeStore(stored_id="555")
    r = await meta.run_profile_only(page, "old_nick", args, store, "a")
    assert store.renamed == [("old_nick", "new_nick")] and r["username"] == "new_nick"
    assert "ник изменён" in r["note"]
    # запасной путь: API молчит, берём og:*
    page = FakePage(
        page={
            "title": "Instagram",
            "og_title": "Foo (@foo) • Instagram",
            "og_description": "10 Followers, 2 Following, 3 Posts - Foo (@foo)",
            "text": "",
        }
    )
    store = FakeStore()
    r = await meta.run_profile_only(page, "foo", args, store, "a")
    assert r["status"] == "ok" and r["followers"] == 10 and "со страницы: og" in r["note"]
    assert store.saved[0][3]["source"] == "og"
    # совсем нет данных: error, профиль не считается обработанным
    store = FakeStore()
    r = await meta.run_profile_only(FakePage(), "empty", args, store, "a")
    assert r["status"] == "error" and store.errors == ["empty"] and not store.saved


asyncio.run(profile_flow())

# --- «Об аккаунте»: разбор текста окна (текст со скриншота пользователя)
ABOUT_TEXT = (
    "Об аккаунте\ngarikkharlamov\nЧтобы поддержать подлинность в сообществе, мы показываем "
    "информацию о профилях в Instagram. Подробнее о том, почему это важно.\n"
    "Дата присоединения\nОктябрь 2013 г.\nМестоположение аккаунта\nКазахстан\n"
    "Аккаунт подтверждён\nМарт 2019 г.\nЗначок подтверждения показывает, что аккаунт "
    "подтверждён на основе действий\nПолучите значок подтверждения"
)
ab = about_parse.parse_about_text(ABOUT_TEXT)
assert (ab["joined_text"], str(ab["joined_date"])) == ("Октябрь 2013 г.", "2013-10-01")
assert ab["account_location"] == "Казахстан" and ab["is_verified"] is True
assert (ab["verified_text"], str(ab["verified_date"])) == ("Март 2019 г.", "2019-03-01")
assert [r[0] for r in ab["rows"]] == [
    "Дата присоединения",
    "Местоположение аккаунта",
    "Аккаунт подтверждён",
]
ru = about_parse.parse_about_text(ABOUT_TEXT.replace("Казахстан", "Российская Федерация"))
assert ru["is_kz"] is False and ru["account_location"] is None
assert not any(k in ru for k in ("joined_text", "joined_date", "verified_text", "is_verified"))
assert about_parse.is_about_text(ABOUT_TEXT) and not about_parse.is_about_text("Подписаться")
en = about_parse.parse_about_text(
    "About this account\nDate joined\nMarch 2020\nAccount based in\nKazakhstan"
)
assert (str(en["joined_date"]), en["account_location"]) == ("2020-03-01", "Казахстан")
assert about_parse.parse_about_text(
    "Дата присоединения Октябрь 2013 г. Местоположение аккаунта Казахстан Предыдущие имена пользователя 1"
)["account_location"] == "Казахстан"
assert "is_verified" not in en  # нет строки «подтверждён» — флаг не трогаем
sep = about_parse.parse_month_year("September 2011")
assert about_parse.parse_month_year("Сентября 2011") == sep
assert about_parse.parse_month_year("без даты") is None


class AboutPage:
    """Страница с «···» и окном: проверяем порядок действий fetch_about."""

    def __init__(self, with_item=True, window=ABOUT_TEXT):
        self.log, self.opened, self.window, self.with_item = [], False, window, with_item
        self.mouse = types.SimpleNamespace(click=self._click)
        self.keyboard = types.SimpleNamespace(press=self._press)

    async def _click(self, x, y):
        self.log.append(("click", x, y))

    async def _press(self, key):
        self.log.append(("key", key))
        self.opened = False

    async def wait_for_timeout(self, ms):
        pass

    async def goto(self, url, wait_until=None):
        self.log.append(("goto", url))
        self.opened = False

    async def evaluate(self, js, arg=None):
        if "scrollTo" in js:
            return None
        if "querySelectorAll('svg')" in js:
            return [
                {"x": 50, "y": 90, "vw": 1280, "header": False},  # левая панель: пропускается
                {"x": 900, "y": 300, "vw": 1280, "header": False},
                {"x": 1000, "y": 120, "vw": 1280, "header": True},
            ]
        return [{"text": self.window, "html": "<div>" + "x" * 50 + "</div>"}] if self.opened else []

    def locator(self, sel):
        return self

    def filter(self, has_text=None):
        return self

    def or_(self, other):
        return self

    def get_by_text(self, rx):
        return self

    @property
    def first(self):
        return self

    async def wait_for(self, state=None, timeout=None):
        if not self.with_item:
            raise about_scrape.PlaywrightError("нет пункта")

    async def click(self, timeout=None):
        self.log.append(("item",))
        self.opened = True


async def about_flow() -> None:
    page = AboutPage()
    got = await about_scrape.fetch_about(page, "https://www.instagram.com/g/")
    assert got["fields"]["account_location"] == "Казахстан"
    assert got["fields"]["is_verified"] is True and "rows" not in got["fields"]
    assert got["raw"]["text"] == ABOUT_TEXT and got["raw"]["html"].startswith("<div>")
    assert got["raw"]["html_truncated"] is False and len(got["raw"]["rows"]) == 3
    assert page.log[0] == ("click", 1000, 120)  # кнопка из <header>, левая панель не берётся
    assert ("item",) in page.log and page.log[-1] == ("key", "Escape")  # окно закрыто
    # длинный HTML обрезается с пометкой
    page = AboutPage()
    orig = about_scrape.HTML_LIMIT
    about_scrape.HTML_LIMIT = 10
    got = await about_scrape.fetch_about(page, "u")
    about_scrape.HTML_LIMIT = orig
    assert len(got["raw"]["html"]) == 10 and got["raw"]["html_truncated"] is True
    # нет пункта меню в обеих кнопках: None, окно не остаётся открытым
    page = AboutPage(with_item=False)
    assert await about_scrape.fetch_about(page, "u") is None
    assert [k for k in page.log if k[0] == "click"] == [("click", 1000, 120), ("click", 900, 300)]
    # окно открылось, но это не «Об аккаунте»: None
    assert await about_scrape.fetch_about(AboutPage(window="Пожаловаться"), "u") is None


asyncio.run(about_flow())


async def about_in_profile() -> None:
    async def fake_fetch(page, url):
        return {"fields": ab_fields, "raw": {"text": "t"}}

    ab_fields = {k: v for k, v in ab.items() if k != "rows"}
    real = meta.fetch_about
    meta.fetch_about = fake_fetch
    try:
        on = types.SimpleNamespace(debug=False, no_about=False, refresh_about=False)
        page = FakePage(api={"web_profile_info": web})
        store = FakeStore()
        r = await meta.run_profile_only(page, "shop_kz", on, store, "a")
        assert r["status"] == "ok" and r["verified"] is True
        assert store.about[0] == "shop_kz" and store.about[1]["account_location"]
        # уже собрано: окно не открываем; --refresh-about собирает заново
        store = FakeStore()
        store.fetched = True
        await meta.run_profile_only(FakePage(api={"web_profile_info": web}), "s", on, store, "a")
        assert not hasattr(store, "about")
        again = types.SimpleNamespace(debug=False, no_about=False, refresh_about=True)
        await meta.run_profile_only(FakePage(api={"web_profile_info": web}), "s", again, store, "a")
        assert hasattr(store, "about")
        # --no-about: окно не трогаем
        off = types.SimpleNamespace(debug=False, no_about=True, refresh_about=False)
        store = FakeStore()
        await meta.run_profile_only(FakePage(api={"web_profile_info": web}), "s", off, store, "a")
        assert not hasattr(store, "about")

        async def no_window(page, url):
            return None

        meta.fetch_about = no_window  # окно не собрано: профиль всё равно ok, в примечании пометка
        store = FakeStore()
        page = FakePage(api={"web_profile_info": web})
        r = await meta.run_profile_only(page, "s", on, store, "a")
        assert r["status"] == "ok" and "не собрано" in r["note"] and not hasattr(store, "about")
        # закрытый профиль: окно доступно и ему
        closed = {"data": {"user": {**web["data"]["user"], "is_private": True}}}
        store = FakeStore()
        page = FakePage(api={"web_profile_info": closed})
        r = await meta.run_profile_only(page, "s", on, store, "a")
        assert r["status"] == "private" and "не собрано" in r["note"]
    finally:
        meta.fetch_about = real


asyncio.run(about_in_profile())

# --- запись о запуске и сводка
res = new_result("a", "profile", "ok", started_at="2026-11-10T10:00:00+00:00", duration_sec=1.5)
e = profile_run_entry(res)
assert e["mode"] == "profile" and e["posts"] == {} and e["duration_sec"] == 1.5
assert dispatch.DONE_STATUSES == ("ok", "private", "not_found")
import json  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    path = write_summary([{**res, "pstat": {"x": 1}}], Path(d) / "out")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[0]["username"] == "a" and "pstat" not in data[0]

# --- IgStore: словарь ников обновляется при смене ника
async def store_flow() -> None:
    class Pool:
        def acquire(self):
            raise AssertionError("не должно обращаться к БД")

    s = IgStore(Pool(), {"old": 5})
    assert s.pid("OLD") == 5
    s.ids["new"] = s.ids.pop("old")
    assert s.pid("new") == 5


asyncio.run(store_flow())

# --- командная строка
st = IgSettings(tabs=3, headless=False, delay=4.0, profile_delay=8.0)
ap = parse_cli.build_parser(st)
a = ap.parse_args(["--profile-only", "--source", "all", "--only-unprocessed", "--headless"])
parse_cli.resolve_args(ap, a, st)
assert (a.tabs, a.headless, a.delay, a.profile_delay) == (3, True, 4.0, 8.0)
a = ap.parse_args(["--profile-only", "x", "--no-headless", "--tabs", "5", "--accounts", "a, b"])
st2 = IgSettings(headless=True)
parse_cli.resolve_args(ap, a, st2)
assert (a.tabs, a.headless, a.accounts) == (5, False, ["a", "b"])
assert not a.no_about and not a.refresh_about
for argv in (
    ["x"],
    ["--profile-only"],
    ["--profile-only", "--tabs", "0", "x"],
    ["--profile-only", "x", "--no-about", "--refresh-about"],
):
    try:
        parse_cli.resolve_args(ap, ap.parse_args(argv), st)
        raise AssertionError(argv)
    except SystemExit:
        pass
assert "parse" in ig_cli.COMMANDS

print("ig_step4_check: OK")
