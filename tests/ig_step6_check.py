"""Проверка шага 6 Instagram (полный проход, --fill, комментарии, медиа) без БД и сети."""

import argparse
import asyncio
import sys
import tempfile
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

from config import IgSettings  # noqa: E402
from core.errors import RateLimited  # noqa: E402
from ig import parse_cli, storage  # noqa: E402
from ig.db.posts import counters  # noqa: E402
from ig.feed import comment_from_dict, media_slides, post_from_item  # noqa: E402
from ig.scrape import fill, full, post_extras  # noqa: E402

NOW = datetime.now(timezone.utc)
post_extras.PAUSE = (0.0, 0.0)


def cand(url, w=1080, h=1080):
    return {"url": url, "width": w, "height": h}


def photo(code, days=1, **kw):
    it = {"code": code, "pk": "9" + code, "media_type": 1, "comment_count": 3, "like_count": 1,
          "taken_at": int((NOW - timedelta(days=days)).timestamp()),
          "image_versions2": {"candidates": [cand(f"https://cdn/{code}_small.jpg", 100, 100),
                                             cand(f"https://cdn/{code}.jpg")]}}
    it.update(kw)
    return it


reel = photo("R1", media_type=2, product_type="clips", video_duration=12.5,
             video_versions=[cand("https://cdn/R1.mp4?x=1", 720, 1280)])
carousel = photo("C1", media_type=8, carousel_media_count=2, carousel_media=[
    {"media_type": 1, "image_versions2": {"candidates": [cand("https://cdn/c1a.jpg")]}},
    {"media_type": 2, "video_versions": [cand("https://cdn/c1b.mp4")],
     "image_versions2": {"candidates": [cand("https://cdn/c1b.jpg")]}, "video_duration": 3}])

# --- медиа и комментарии: разбор
assert [(s["kind"], s["url"]) for s in media_slides(photo("P1"))] == [("image", "https://cdn/P1.jpg")]
rs = media_slides(reel)
assert rs[0]["kind"] == "video" and rs[0]["duration"] == 12.5
cs = media_slides(carousel)
assert [(s["position"], s["kind"]) for s in cs] == [(1, "image"), (2, "video")]
c = comment_from_dict({"pk": 5, "text": "hi", "created_at_utc": 1700000000, "comment_like_count": 2,
                       "child_comment_count": 3, "user": {"username": "u", "pk": 7}}, "P1", None)
assert c["id"] == "5" and c["depth"] == 1 and c["reply_count"] == 3 and c["author_id"] == "7"
assert comment_from_dict({"pk": 6}, "P1", "5")["depth"] == 2 and comment_from_dict({}, "P1", None) is None

# --- хранилище
assert storage.safe_nick("Kz.User_1") == "kz.user_1"
for bad_nick in ("..", "a/b", "../x", ".hid", "", "x" * 40):
    try:
        storage.safe_nick(bad_nick)
        raise AssertionError(bad_nick)
    except ValueError:
        pass
d = datetime(2026, 1, 5, tzinfo=timezone.utc)
assert storage.rel_path("User", d, "AbC", 2, "mp4") == "user/20260105_AbC_2.mp4"
assert storage.ext_for("https://x/a.jpeg?z=1", None, "image") == "jpg"
assert storage.ext_for("https://x/a", "video/mp4", "video") == "mp4" and storage.ext_for("u", None, "video") == "mp4"
assert storage.free_gb(Path("/nonexistent/dir")) > 0


class Resp:
    def __init__(self, ok=True, body=b"data", status=200):
        self.ok, self._b, self.status = ok, body, status

    async def body(self):
        return self._b


class FakePage:
    """Страница с контекстом для скачивания и ответами JS_FETCH по подстроке адреса."""

    def __init__(self, api=None, files=None, fail_urls=()):
        self.api, self.files, self.fail_urls, self.calls, self.got = api or {}, files or {}, set(fail_urls), [], []
        self.context = types.SimpleNamespace(request=types.SimpleNamespace(get=self._get))

    async def _get(self, url, timeout=None):
        self.got.append(url)
        if url in self.fail_urls:
            return Resp(ok=False, status=403)
        return Resp(body=self.files.get(url, b"x" * 10))

    async def evaluate(self, js, arg=None):
        self.calls.append(arg)
        for key, val in self.api.items():
            if key in arg:
                v = val.pop(0) if isinstance(val, list) and len(val) > 1 else (val[0] if isinstance(val, list) else val)
                if isinstance(v, Exception):
                    raise v
                return v
        return {"error": 404}


class FakeStore:
    def __init__(self, states=None, known=None, todo=None):
        self.states, self.known_c, self.todo = states or {}, known or {}, todo or []
        self.comments, self.cstate, self.media, self.finished, self.updated, self.avatar = [], {}, {}, {}, [], None

    async def post_states(self, u):
        return dict(self.states)

    async def known_comments(self, code):
        return dict(self.known_c)

    async def save_comments(self, rows):
        self.comments += rows

    async def set_comments_state(self, code, status, depth):
        self.cstate[code] = (status, depth)

    async def save_media(self, code, rows):
        self.media[code] = rows

    async def finish_post(self, code, status, failed):
        self.finished[code] = (status, failed)

    async def posts_to_fill(self, u, comments, videos):
        return self.todo

    async def update_post_fields(self, post):
        self.updated.append(post["code"])

    async def avatar_url(self, u):
        return "https://cdn/ava.jpg"

    async def set_avatar(self, u, rel):
        self.avatar = rel

    async def save_meta(self, *a):
        pass


def args(tmp, **kw):
    base = dict(comments=True, depth=2, max_comments=0, images=True, videos=False, avatars=False,
                recheck_days=7, full_rescan=False, storage_dir=tmp, min_free_gb=0, delay=0.0)
    base.update(kw)
    return argparse.Namespace(**base)


def post_of(it):
    return post_from_item(it)


def run(coro):
    return asyncio.run(coro)


tmp = Path(tempfile.mkdtemp())
# --- скачивание: атомарно, повторно не качается, ошибка HTTP
pg = FakePage()
size, fresh = run(storage.download(pg, "https://cdn/a.jpg", tmp / "u" / "a.jpg"))
assert (size, fresh) == (10, True) and (tmp / "u" / "a.jpg").exists() and not list(tmp.rglob("*.part"))
assert run(storage.download(pg, "https://cdn/a.jpg", tmp / "u" / "a.jpg")) == (10, False) and len(pg.got) == 1
try:
    run(storage.download(FakePage(fail_urls=["https://cdn/b.jpg"]), "https://cdn/b.jpg", tmp / "u" / "b.jpg"))
    raise AssertionError("ждали OSError")
except OSError:
    assert not (tmp / "u" / "b.jpg").exists()

# --- media_status
def media_run(it, **kw):
    a = args(tmp, **kw)
    ex = post_extras.Extras(FakePage(), "user", a, FakeStore())
    status = run(ex.get_media(post_of(it), it))
    return status, ex

(st_, failed), ex = media_run(photo("P1"))
assert (st_, failed) == ("done", 0) and ex.media["new_images"] == 1 and ex.media["new_files"] == 1
assert ex.store.media["P1"][0]["file_path"].startswith("user/") and ex.store.media["P1"][0]["file_path"].endswith("_P1_1.jpg")
assert (tmp / ex.store.media["P1"][0]["file_path"]).exists()
assert media_run(reel)[0][0] == "skipped"                         # видео не качаем: пост сохранён, skipped
(st_, _), ex = media_run(reel, videos=True)
assert st_ == "done" and ex.media["new_videos"] == 1 and ex.media["new_images"] == 0
assert media_run(carousel)[0][0] == "partial"                      # фото есть, видео-слайд пропущен
assert media_run(carousel, videos=True)[0][0] == "done"
a = args(tmp, images=True)
ex = post_extras.Extras(FakePage(fail_urls=["https://cdn/F1.jpg"]), "user", a, FakeStore())
assert run(ex.get_media(post_of(photo("F1")), photo("F1"))) == ("failed", 1) and ex.media["download_failed"] == 1
assert run(ex.get_media(post_of(photo("N1")), {"code": "N1"})) == ("failed", 1)  # нет адресов

# --- комментарии: страницы, ответы, известные, лимит, 429
def top(ids, nxt=None, replies=None):
    return {"comments": [{"pk": i, "text": f"c{i}", "created_at_utc": 1700000000,
                          "child_comment_count": (replies or {}).get(i, 0), "user": {"username": "u"}}
                         for i in ids], "next_min_id": nxt}


api = {"/comments/?": [top([1, 2], "cur1", {1: 2}), top([3], None)],
       "child_comments": {"child_comments": [{"pk": 11, "text": "r1", "user": {}}, {"pk": 12, "text": "r2", "user": {}}],
                          "has_more_child_comments": False}}
it = photo("K1")
store = FakeStore()
ex = post_extras.Extras(FakePage(api={k: list(v) if isinstance(v, list) else v for k, v in api.items()}), "user", args(tmp), store)
run(ex.get_comments(post_of(it)))
assert [r["id"] for r in store.comments] == ["1", "2", "11", "12", "3"], [r["id"] for r in store.comments]
assert {r["id"]: r["depth"] for r in store.comments}["11"] == 2 and store.comments[2]["parent_id"] == "1"
assert store.cstate["K1"] == ("done", 2) and ex.comments["new"] == 5
# depth 1: ответы не запрашиваются
store = FakeStore()
pg = FakePage(api={"/comments/?": [top([1], None, {1: 5})]})
ex = post_extras.Extras(pg, "user", args(tmp, depth=1), store)
run(ex.get_comments(post_of(it)))
assert not any("child_comments" in (c or "") for c in pg.calls) and store.cstate["K1"] == ("done", 1)
# страница целиком из известных: дальше не идём (без --full-rescan), с ним идём
known = {"1": 0, "2": 0}
for rescan, pages in ((False, 1), (True, 2)):
    pg = FakePage(api={"/comments/?": [top([1, 2], "cur1"), top([3], None)]})
    ex = post_extras.Extras(pg, "user", args(tmp, full_rescan=rescan), FakeStore(known=known))
    run(ex.get_comments(post_of(it)))
    assert len([c for c in pg.calls if "/comments/?" in c]) == pages, (rescan, pg.calls)
# лимит: partial, ответы входят в счёт
store = FakeStore()
pg = FakePage(api={"/comments/?": [top([1, 2, 3], "c")]})
ex = post_extras.Extras(pg, "user", args(tmp, max_comments=2), store)
run(ex.get_comments(post_of(it)))
assert len(store.comments) == 2 and store.cstate["K1"][0] == "partial"
# 429 посреди обхода: сохранённое остаётся, статус partial, исключение уходит наверх
store = FakeStore()
pg = FakePage(api={"/comments/?": [top([1], "c"), {"error": 429}]})
ex = post_extras.Extras(pg, "user", args(tmp), store)
try:
    run(ex.get_comments(post_of(it)))
    raise AssertionError("ждали RateLimited")
except RateLimited:
    assert [r["id"] for r in store.comments] == ["1"] and store.cstate["K1"][0] == "partial"

# --- needs(): что перепроверять у известных постов
young = {"last_refreshed_at": NOW, "media_status": "done", "comments_status": "done",
         "comments_collected": 3, "taken_at": NOW - timedelta(days=2)}
old = {**young, "taken_at": NOW - timedelta(days=60)}
p3 = post_of(photo("Z1"))
mk = lambda states, **kw: post_extras.Extras(FakePage(), "user", args(tmp, **kw), FakeStore(states=states))  # noqa: E731
e = mk({"Z1": young}); e.states = {"Z1": young}
assert e.needs(p3) == (False, True, False)                         # молодой пост: комментарии заново
e.states = {"Z1": old}
assert e.needs(p3) == (False, False, False)                        # старый и без роста: пропуск
e.states = {"Z1": {**old, "comments_collected": 1}}
assert e.needs(p3)[1] is True                                      # comments_count вырос
e.states = {"Z1": {**old, "media_status": "skipped"}}
assert e.needs(p3)[0] is False and mk({}, videos=True).needs(p3)[2] is True
e2 = mk({}, videos=True); e2.states = {"Z1": {**old, "media_status": "skipped"}}
assert e2.needs(p3)[0] is True                                     # --videos докачивает skipped
e3 = mk({}, recheck_days=0); e3.states = {"Z1": young}
assert e3.needs(p3)[1] is False                                    # recheck выключен
e4 = mk({}); e4.states = {"Z1": {**young, "last_refreshed_at": None}}
assert e4.needs(p3) == (True, True, True)                          # собран лёгким режимом
assert mk({}, comments=False, images=False).needs(post_of(photo("Z2"))) == (False, False, True)

# --- полный проход на заглушках: лента + комментарии + медиа + аватар
feed_items = [photo("A1"), photo("A2", days=2)]
feed_api = {"/feed/user/": {"items": feed_items, "more_available": False},
            "/comments/?": {"comments": [{"pk": 1, "text": "x", "user": {}}], "next_min_id": None}}


class RunPage(FakePage):
    url = "https://www.instagram.com/user/"

    async def goto(self, url, wait_until=None):
        return types.SimpleNamespace(status=200)

    async def wait_for_timeout(self, ms):
        pass

    async def wait_for_selector(self, selector, timeout=None):
        pass


class RunStore(FakeStore):
    def __init__(self):
        super().__init__()
        self.saved = []

    async def posts_context(self, u):
        return {}, None

    async def save_post_light(self, u, post, known):
        known[post["code"]] = counters(post)
        self.saved.append(post["code"])
        return "new"

    async def save_anchor(self, u, anchor):
        pass


tmp2 = Path(tempfile.mkdtemp())
a = args(tmp2, avatars=True, cutoff=None, limit=0, stop_after=4, full=False, min_posts=0)
store = RunStore()
res = run(full.run_full(RunPage(api=feed_api), "user", a, store, "acc"))
assert res["status"] == "ok" and res["mode"] == "full" and res["posts"]["new"] == 2, res
assert res["comments"]["new"] == 2 and res["media"]["new_files"] == 2
assert set(store.finished) == {"A1", "A2"} and store.finished["A1"] == ("done", 0)
assert store.avatar == "user/avatar.jpg" and (tmp2 / "user" / "avatar.jpg").exists()
# 429 на комментариях: профиль уходит другому аккаунту, ничего лишнего не помечено
store = RunStore()
res = run(full.run_full(RunPage(api={**feed_api, "/comments/?": {"error": 429}}), "user", a, store, "acc"))
assert (res["status"], res["abort_kind"]) == ("abort", "429")
assert "A1" not in store.finished  # пост без комментариев не помечен обработанным

# --- --fill: берём посты по media_id, обновляем поля, снимаем комментарии/медиа
todo = [{"code": "A1", "media_id": "9A1", "taken_at": NOW, "comments_count": 3, "comments_disabled": False,
         "comments_status": "none", "comments_collected": 0, "media_status": "pending", "last_refreshed_at": None}]
store = RunStore()
store.todo = todo
store.states = {"A1": todo[0]}
info = {"/media/9A1/info/": {"items": [photo("A1")]}, "/comments/?": feed_api["/comments/?"]}
a2 = args(tmp2, cutoff=None)
res = run(fill.run_fill(RunPage(api=info), "user", a2, store, "acc"))
assert res["status"] == "ok" and res["mode"] == "fill" and res["posts"] == {"refreshed": 1}, res
assert store.updated == ["A1"] and "A1" in store.finished and res["comments"]["new"] == 1
res = run(fill.run_fill(RunPage(api={"/media/9A1/info/": {"error": 429}}), "user", a2, store, "acc"))
assert (res["status"], res["abort_kind"]) == ("abort", "429")
res = run(fill.run_fill(RunPage(api={"/media/9A1/info/": {"error": 404}}), "user", a2, store, "acc"))
assert res["status"] == "ok" and "не получено" in res["note"]

# --- CLI
st = IgSettings()
ap = parse_cli.build_parser(st)


def parse(*argv):
    ns = ap.parse_args(list(argv))
    parse_cli.resolve_args(ap, ns, st)
    return ns


def bad(*argv):
    try:
        parse(*argv)
    except SystemExit:
        return True
    return False


a = parse("--source", "all", "--comments", "--videos", "--no-images", "--depth", "1")
assert (a.comments, a.videos, a.images, a.depth) == (True, True, False, 1) and not a.fill
assert parse("--fill", "--source", "all").fill and bad("--fill", "--posts-only", "--source", "all")
assert bad("--fill", "--source", "all", "--since", "30d") and bad("--source", "all", "--min-posts", "5")
assert not bad("--source", "all", "--since", "30d", "--limit", "5") and bad("--source", "all", "--depth", "3")
assert bad("--source", "all", "--no-about") and not bad("--posts-only", "--source", "all", "--comments")
b = parse("--source", "all")
assert (b.comments, b.images, b.videos, b.avatars, b.recheck_days) == (st.comments, st.images, st.videos, st.avatars, st.recheck_days)
assert b.storage_dir == st.storage_dir and b.min_free_gb == st.min_free_gb

print("ig_step6_check: OK")
