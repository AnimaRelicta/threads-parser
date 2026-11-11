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
    parse_count,
    private_from_text,
    user_from_dict,
)
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
assert classify_api({"error": 404}) == ("not_found", None)
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

    async def evaluate(self, js, arg=None):
        if arg is None:
            if "og:title" in js:
                return self.page_info or {"og_title": None, "og_description": None, "text": ""}
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


async def profile_flow() -> None:
    args = types.SimpleNamespace(debug=False)
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
    # не найден: 404 страницы и пустой data.user; ig_user_id неизвестен — переименования нет
    for pg in (FakePage(status=404), FakePage(api={"web_profile_info": {"data": {"user": None}}})):
        store = FakeStore()
        r = await meta.run_profile_only(pg, "gone", args, store, "a")
        assert r["status"] == "not_found" and store.saved == [("gone", "not_found", None, None)]
    # смена ника: старый ник 404, по сохранённому id находим новый
    page = FakePage(
        api={
            "web_profile_info": {"data": {"user": None}},
            "/users/555/info": {"user": {"username": "new_nick", "id": "555"}},
        }
    )
    store = FakeStore(stored_id="555")
    r = await meta.run_profile_only(page, "old_nick", args, store, "a")
    assert store.renamed == [("old_nick", "new_nick")] and r["username"] == "new_nick"
    assert "ник изменён" in r["note"]
    # запасной путь: API молчит, берём og:*
    page = FakePage(
        page={
            "og_title": "Foo (@foo) • Instagram",
            "og_description": "10 Followers, 2 Following, 3 Posts - Foo (@foo)",
            "text": "",
        }
    )
    store = FakeStore()
    r = await meta.run_profile_only(page, "foo", args, store, "a")
    assert r["status"] == "ok" and r["followers"] == 10 and "запасной путь: og" in r["note"]
    assert store.saved[0][3]["source"] == "og"
    # совсем нет данных: error, профиль не считается обработанным
    store = FakeStore()
    r = await meta.run_profile_only(FakePage(), "empty", args, store, "a")
    assert r["status"] == "error" and store.errors == ["empty"] and not store.saved


asyncio.run(profile_flow())

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
for argv in (["x"], ["--profile-only"], ["--profile-only", "--tabs", "0", "x"]):
    try:
        parse_cli.resolve_args(ap, ap.parse_args(argv), st)
        raise AssertionError(argv)
    except SystemExit:
        pass
assert "parse" in ig_cli.COMMANDS

print("ig_step4_check: OK")
