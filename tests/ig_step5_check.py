"""Проверка шага 5 Instagram (--posts-only) без БД, сети и браузера."""

import argparse
import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import asyncpg  # noqa: F401
except ImportError:
    fake = types.ModuleType("asyncpg")
    for name in ("Connection", "Pool", "PostgresError", "InterfaceError", "UniqueViolationError", "Record"):
        setattr(fake, name, type(name, (Exception,), {}))
    sys.modules["asyncpg"] = fake

from ig import parse_cli  # noqa: E402
from ig.db.posts import counters  # noqa: E402
from ig.feed import classify_feed, post_from_item  # noqa: E402
from ig.scrape import posts_light  # noqa: E402
from config import IgSettings  # noqa: E402

NOW = datetime.now(timezone.utc)


def item(code, days_ago, pinned=False, **kw):
    it = {
        "code": code,
        "pk": f"10{len(code)}",
        "media_type": 1,
        "taken_at": int((NOW - timedelta(days=days_ago)).timestamp()),
        "like_count": 5,
        "comment_count": 2,
        "caption": {"text": f"пост {code} #Tag1 #tag1 @Friend"},
    }
    if pinned:
        it["timeline_pin_info"] = {"time_pinned": 1}
    it.update(kw)
    return it


# --- разбор элемента ленты
p = post_from_item(
    item(
        "AbC",
        1,
        media_type=8,
        carousel_media_count=3,
        like_and_view_counts_disabled=True,
        location={"pk": 1, "name": "Алматы", "lat": 1.5, "junk": "x"},
        usertags={"in": [{"user": {"username": "tagged"}}]},
    )
)
assert p["kind"] == "carousel" and p["carousel_size"] == 3 and p["likes"] is None
assert p["hashtags"] == ["tag1"] and p["mentions"] == ["friend"] and p["tagged_users"] == ["tagged"]
assert p["location_name"] == "Алматы" and p["location"] == {"pk": 1, "name": "Алматы", "lat": 1.5}
assert p["url"].endswith("/p/AbC/") and p["taken_at"].tzinfo is not None
reel = post_from_item(item("R1", 1, media_type=2, product_type="clips", play_count=77))
assert reel["kind"] == "reel" and reel["views"] == 77 and "/reel/R1/" in reel["url"]
assert post_from_item(item("P1", 1, media_type=2))["kind"] == "video"
assert post_from_item({"pk": 1}) is None
assert post_from_item(item("P", 1, timeline_pin_info={"x": 1}))["is_pinned"] is True

# --- классификация ответа
assert classify_feed({"error": 429})[0] == "rate" and classify_feed({"error": 401})[0] == "auth"
assert classify_feed({"error": 403})[0] == "forbidden" and classify_feed({"error": 404})[0] == "not_found"
assert classify_feed({"error": "bad_json"})[0] == "error" and classify_feed(None)[0] == "error"
assert classify_feed({"status": "fail", "message": "login_required"})[0] == "auth"
assert classify_feed({"status": "fail", "message": "Please wait a few minutes"})[0] == "rate"
kind, page = classify_feed({"items": [{"code": "a"}], "next_max_id": 77, "more_available": True})
assert kind == "page" and page == {"items": [{"code": "a"}], "next": "77", "more": True}


# --- обход ленты на заглушках
class FakePage:
    def __init__(self, pages, status=200, forbidden_text=None, fail_after=None):
        self.pages, self.status, self.text, self.fail_after = pages, status, forbidden_text, fail_after
        self.urls: list[str] = []

    async def goto(self, url, wait_until=None):
        return types.SimpleNamespace(status=self.status)

    url = "https://www.instagram.com/shop/"

    async def wait_for_timeout(self, ms):
        pass

    async def wait_for_selector(self, selector, timeout=None):
        pass

    async def evaluate(self, js, arg=None):
        if arg is None:
            return self.text or ""
        self.urls.append(arg)
        if self.fail_after is not None and len(self.urls) > self.fail_after:
            return {"error": 429}
        if "error" in self.pages:
            return self.pages
        idx = int(arg.split("max_id=")[1]) if "max_id=" in arg else 0
        items, more = self.pages[idx]
        return {"items": items, "next_max_id": idx + 1 if more else None, "more_available": more}


class FakeStore:
    def __init__(self, known=None, anchor=None):
        self.known, self.anchor, self.saved, self.meta, self.errors = known or {}, anchor, [], [], []

    async def posts_context(self, username):
        return dict(self.known), self.anchor

    async def save_post_light(self, username, post, known):
        old, now = known.get(post["code"]), counters(post)
        if old == now:
            return "unchanged"
        known[post["code"]] = now
        self.saved.append(post["code"])
        return "refreshed" if old is not None else "new"

    async def save_anchor(self, username, anchor):
        self.anchor = anchor

    async def save_meta(self, username, status, meta, raw):
        self.meta.append((username, status, meta, raw))

    async def mark_error(self, username):
        self.errors.append(username)


def args(**kw):
    base = dict(cutoff=None, limit=0, stop_after=2, full=False, min_posts=0, delay=0.0)
    base.update(kw)
    return argparse.Namespace(**base)


def run(page, store, **kw):
    return asyncio.run(posts_light.run_posts_only(page, "shop", args(**kw), store, "acc"))


A = lambda *its: list(its)  # noqa: E731
feed = {
    0: (A(item("p0", 40, pinned=True), item("n1", 1), item("n2", 2)), True),
    1: (A(item("n3", 5), item("n4", 9)), True),
    2: (A(item("n5", 20), item("n6", 30)), False),
}
# 1. первый проход без окна: вся лента, остановка end, якорь «весь профиль»
st = FakeStore()
r = run(FakePage(feed), st)
assert r["status"] == "ok" and r["stop_reason"] == "end" and r["posts"]["new"] == 7, r
assert r["pages_opened"] == 3 and st.anchor["newest_code"] == "n1" and st.anchor["covered_from"] is None
assert "anchor" not in r and r["posts"]["newest"]
# 2. окно --since: закреплённый старый пропускается без серии, дальше два старых подряд = old
st = FakeStore()
r = run(FakePage(feed), st, cutoff=NOW - timedelta(days=10))
assert r["stop_reason"] == "old" and r["posts"]["new"] == 4 and r["posts"]["skipped_old"] == 3, r
assert st.anchor["covered_from"] is not None
# 3. повтор по якорю: серия известных (2) останавливает обход; без якоря серии нет
known = {p["code"]: counters(post_from_item(p)) for pg in feed.values() for p in pg[0]}
page = FakePage(feed)
r = run(page, FakeStore(known=known, anchor={"newest_code": "n1", "newest_date": None,
                                              "covered_from": None, "known_posts": 7}))
assert r["stop_reason"] == "known" and r["posts"]["unchanged"] == 3 and len(page.urls) == 1, r
r = run(FakePage(feed), FakeStore(known=known))  # якоря нет: идём до конца
assert r["stop_reason"] == "end" and r["posts"]["unchanged"] == 7
r = run(FakePage(feed), FakeStore(known=known, anchor={"newest_code": "n1", "newest_date": None,
        "covered_from": None, "known_posts": 7}), full=True)  # --full игнорирует якорь
assert r["stop_reason"] == "end"
r = run(FakePage(feed), FakeStore(known=known, anchor={"newest_code": "n1", "newest_date": None,
        "covered_from": None, "known_posts": 7}), min_posts=20)  # мало постов: серия не стоп
assert r["stop_reason"] == "end"
# 4. --limit
r = run(FakePage(feed), FakeStore(), limit=3)
assert r["stop_reason"] == "limit" and r["posts"]["in_period"] == 3
# 5. счётчики известного поста изменились -> refreshed
changed = {**known, "n1": (999, 2, None, False)}
r = run(FakePage(feed), FakeStore(known=changed), full=True)
assert r["posts"]["refreshed"] == 1 and r["posts"]["unchanged"] == 6
# 6. 429 посреди ленты: посты первой страницы остались, якорь не тронут
st = FakeStore()
r = run(FakePage(feed, fail_after=1), st)
assert (r["status"], r["abort_kind"]) == ("abort", "429") and st.anchor is None and st.saved, r
# 7. закрытый профиль (403 + текст) и 403 без текста (проблема сессии)
r = run(FakePage({"error": 403}, forbidden_text="Это закрытый аккаунт"), FakeStore())
assert r["status"] == "private", r
r = run(FakePage({"error": 403}, forbidden_text="что-то"), FakeStore())
assert (r["status"], r["abort_kind"]) == ("abort", "auth"), r
# 8. пустой профиль и сбой ленты
r = run(FakePage({0: ([], False)}), FakeStore())
assert r["status"] == "empty" and r["stop_reason"] == "end"
st = FakeStore()
r = run(FakePage({"error": "bad_json"}), st)
assert r["status"] == "error" and st.errors == ["shop"]
# 9. 404 страницы: not_found с причиной в meta_raw
st = FakeStore()
r = run(FakePage(feed, status=404), st)
assert r["status"] == "not_found" and st.meta[0][1] == "not_found" and st.meta[0][3]["source"] == "http"

# --- CLI: режимы и флаги ленты
st_ = IgSettings()
ap = parse_cli.build_parser(st_)


def parse(*argv):
    a = ap.parse_args(list(argv))
    parse_cli.resolve_args(ap, a, st_)
    return a


def bad(*argv):
    try:
        parse(*argv)
    except SystemExit:
        return True
    return False


a = parse("--posts-only", "--source", "all", "--since", "30d", "--limit", "5")
assert a.posts_only and a.limit == 5 and a.cutoff is not None and a.stop_after == st_.stop_after_known
assert not bad("--source", "all") and bad("--profile-only", "--source", "all", "--since", "30d")
assert bad("--profile-only", "--posts-only", "--source", "all")
assert bad("--posts-only", "--source", "all", "--no-about") and bad("--posts-only", "x", "--limit", "-1")
assert bad("--posts-only", "x", "--stop-after", "0") and not bad("--posts-only", "x", "--full")
assert not bad("--profile-only", "x")

print("ig_step5_check: OK")
