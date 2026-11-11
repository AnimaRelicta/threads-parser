"""Проверка шага 3 Instagram без БД и сети: ники, файл, слияние источников, запрос к threads."""

import argparse
import asyncio
import sys
import tempfile
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
    sys.modules["asyncpg"] = fake

from ig import cli as ig_cli  # noqa: E402
from ig import profiles_cli  # noqa: E402
from ig.db import profiles as db_profiles  # noqa: E402
from ig.db.sources import (  # noqa: E402
    R_DUP,
    R_EMPTY,
    R_INVALID,
    R_RESERVED,
    Candidate,
    Rejected,
    collapse,
    normalize_nick,
    parse_lines,
    plan_update,
    read_nick_file,
    threads_candidates,
    threads_query,
    to_candidates,
)

# --- нормализация ника
ok = {
    "someone": "someone",
    "@Some.One_1": "some.one_1",
    "  @x  ": "x",
    "https://www.instagram.com/Kazakh.Shop/": "kazakh.shop",
    "instagram.com/shop?igsh=abc": "shop",
    "http://instagr.am/foo/#top": "foo",
}
for raw, want in ok.items():
    assert normalize_nick(raw) == (want, None), raw
bad = {
    "": R_EMPTY,
    "   ": R_EMPTY,
    "https://instagram.com/p/ABC123/": R_RESERVED,
    "https://www.instagram.com/reel/XYZ/": R_RESERVED,
    "instagram.com/explore/": R_RESERVED,
    "explore": R_RESERVED,
    "не ник": R_INVALID,
    "a" * 31: R_INVALID,
    "bad-nick": R_INVALID,
    "https://example.com/user": R_INVALID,
}
for raw, reason in bad.items():
    assert normalize_nick(raw) == (None, reason), raw

# --- файл: комментарии, пустые, BOM, ссылки, повторы
assert parse_lines(["# шапка", "", "  a  # хвост", "@b", "   "]) == ["a", "@b"]
with tempfile.TemporaryDirectory() as d:
    f = Path(d) / "ig_profiles.txt"
    body = "# список\nalpha\n@Beta\nhttps://instagram.com/gamma/\nALPHA\ninstagram.com/p/XX/\n\n"
    f.write_bytes(("\ufeff" + body).encode())
    raws = read_nick_file(f)
    rej = Rejected()
    cands = to_candidates(raws, "file", f.name, rej)
    assert [c.username for c in cands] == ["alpha", "beta", "gamma"]
    assert all(c.source == "file" and c.source_ref == "ig_profiles.txt" for c in cands)
    assert rej.counts == {R_DUP: 1, R_RESERVED: 1}, rej.counts
    assert rej.examples[R_DUP] == ["ALPHA"]
    try:
        read_nick_file(Path(d) / "нет.txt")
        raise AssertionError("ожидалась ошибка для отсутствующего файла")
    except SystemExit as e:
        assert "не найден" in str(e)

def thr(nick, ref, pid, tname, conf, link_source):
    return Candidate(
        nick,
        "threads",
        ref,
        threads_profile_id=pid,
        threads_username=tname,
        link_confidence=conf,
        link_source=link_source,
    )


# --- слияние в пакете: первый источник основной, связь по рангу
cli_c = Candidate("shop", "cli")
thr_g = thr("shop", "t#1", 10, "shop_th", "guessed", "username_guess")
thr_c = thr("shop", "t#2", 11, "shop_th2", "confirmed", "header_link")
m = collapse([cli_c, thr_g, thr_c, Candidate("other", "file")])
assert [c.username for c in m] == ["shop", "other"]
s = m[0]
assert (s.source, s.sources, s.source_ref) == ("cli", ("cli", "threads"), None)
assert (s.link_confidence, s.threads_profile_id, s.link_source) == ("confirmed", 11, "header_link")
# confirmed не затирается позже пришедшим guessed
back = collapse([thr_c, thr_g])[0]
assert (back.link_confidence, back.threads_profile_id) == ("confirmed", 11)
# равный ранг: остаётся первый (в ТЗ: threads_profile_id — первый)
two = collapse([thr_g, thr("shop", "t#3", 99, "zzz", "guessed", "username_guess")])
assert two[0].threads_profile_id == 10 and two[0].sources == ("threads",)

# --- слияние с существующей строкой БД
row = {
    "id": 5,
    "source": "file",
    "sources": ["file"],
    "link_confidence": None,
    "link_source": None,
    "threads_profile_id": None,
    "threads_username": None,
}
assert plan_update(row, Candidate("shop", "file", "x")) is None  # тот же источник
u = plan_update(row, thr_g)
assert u and u.sources_added and u.link_raised
assert u.values["sources"] == ["file", "threads"] and u.values["link_confidence"] == "guessed"
assert u.values["threads_profile_id"] == 10
# guessed -> confirmed: вверх можно
row2 = {**row, "source": "threads", "sources": ["threads"], "link_confidence": "guessed",
        "link_source": "username_guess", "threads_profile_id": 10, "threads_username": "shop_th"}
u = plan_update(row2, thr_c)
assert u and not u.sources_added and u.link_raised
assert (u.values["link_confidence"], u.values["threads_profile_id"]) == ("confirmed", 11)
# confirmed -> guessed: вниз нельзя; источник при этом дописывается
row3 = {**row2, "sources": ["file"], "link_confidence": "confirmed", "threads_profile_id": 11}
u = plan_update(row3, thr_g)
assert u and u.sources_added and not u.link_raised
assert u.values["link_confidence"] == "confirmed" and u.values["threads_profile_id"] == 11
assert u.values["sources"] == ["file", "threads"]
# равный ранг, источник уже есть: без изменений, `source` в значениях не фигурирует
assert plan_update({**row3, "sources": ["file", "threads"]}, thr_c) is None
assert "source" not in plan_update(row, thr_g).values

# --- запрос к threads: фильтры идут параметрами, читаем только threads.*
sql, args = threads_query()
assert args == ["confirmed"] and "l.confidence = $1" in sql and "LIMIT" not in sql
assert "FROM threads.instagram_links l JOIN threads.profiles p" in sql
sql, args = threads_query("all")
assert args == [] and "WHERE" not in sql
sql, args = threads_query("guessed", True, "Казахстан", 1000, 7, 50)
assert args == ["guessed", "Казахстан", 1000, 7, 50], args
for part in (
    "l.confidence = $1",
    "p.is_target",
    "p.about_location ILIKE '%' || $2 || '%'",
    "p.followers_count >= $3",
    "p.category_id = $4",
    "LIMIT $5",
):
    assert part in sql, part
assert "Казахстан" not in sql  # значения только через параметры

rej = Rejected()
rows = [
    {"link_id": 1, "instagram_username": "Shop_KZ", "confidence": "confirmed",
     "link_source": "header_link", "threads_profile_id": 3, "threads_username": "shop_kz"},
    {"link_id": 2, "instagram_username": "bad nick", "confidence": "guessed",
     "link_source": "username_guess", "threads_profile_id": 4, "threads_username": "x"},
]
tc = threads_candidates(rows, rej)
assert len(tc) == 1 and tc[0].username == "shop_kz"
assert tc[0].source_ref == "threads.instagram_links#1"
assert (tc[0].threads_profile_id, tc[0].link_confidence) == (3, "confirmed")
assert rej.counts == {R_INVALID: 1}


# --- занос поверх заглушки соединения (логика, не SQL)
class FakeConn:
    def __init__(self, rows: list[dict]):
        self.rows = {r["nick"]: r for r in rows}
        self.inserted: list[str] = []
        self.updated: list[tuple] = []
        self.locked = False

    def transaction(self):
        conn = self

        class _Tx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Tx()

    async def fetch(self, sql, *args):
        if "FROM profiles WHERE lower(username)" in sql:
            self.locked = "FOR UPDATE" in sql
            return [self.rows[n] for n in args[0] if n in self.rows]
        if sql.lstrip().startswith("INSERT INTO profiles"):
            fresh = [n for n in args[0] if n not in self.rows]
            self.inserted += fresh
            return [{"nick": n} for n in fresh]
        raise AssertionError(sql)

    async def executemany(self, sql, rows):
        assert sql.startswith("UPDATE profiles SET")
        self.updated += list(rows)


async def flow() -> None:
    existing = [{**row, "nick": "shop"}, {**row2, "id": 6, "nick": "old"}]
    cands = collapse([
        thr("shop", "t#1", 10, "shop_th", "guessed", "username_guess"),
        thr("old", "t#2", 11, "old_th", "confirmed", "header_link"),
        Candidate("new1", "file", "f.txt"),
        thr("new2", "t#3", 12, "n2", "confirmed", "threads_json"),
    ])
    conn = FakeConn(existing)
    rep = await db_profiles.add_candidates(conn, cands)
    assert conn.locked and conn.inserted == ["new1", "new2"]
    assert (rep.read, rep.added, rep.existed) == (4, 2, 2)
    assert (rep.sources_extended, rep.confidence_raised) == (1, 2)
    assert rep.by_source["file"]["added"] == 1 and rep.by_source["threads"]["existed"] == 2
    assert sorted(u[0] for u in conn.updated) == [5, 6]

    dry = FakeConn(existing)
    rep = await db_profiles.add_candidates(dry, cands, dry_run=True)
    assert not dry.locked and not dry.inserted and not dry.updated
    assert (rep.added, rep.existed) == (2, 2)
    assert (await db_profiles.add_candidates(FakeConn([]), [])).read == 0


asyncio.run(flow())

# --- разбор командной строки
ap = profiles_cli.build_parser()
a = ap.parse_args(["add", "x", "@y", "--file", "f.txt"])
assert a.nicks == ["x", "@y"] and a.file == Path("f.txt") and not a.from_threads
profiles_cli.check_add_args(ap, a)
for argv in (["add"], ["add", "x", "--threads-target-only"], ["add", "x", "--limit", "5"]):
    try:
        profiles_cli.check_add_args(ap, ap.parse_args(argv))
        raise AssertionError(argv)
    except SystemExit:
        pass
a = ap.parse_args(["add", "--from-threads", "--threads-location", "Казахстан", "--dry-run"])
profiles_cli.check_add_args(ap, a)
assert a.threads_confidence == "confirmed" and a.dry_run
assert profiles_cli._run_settings(a) == {
    "from_threads": True,
    "threads_confidence": "confirmed",
    "threads_location": "Казахстан",
}
assert ap.parse_args(["list", "--source", "threads", "--status", "new"]).limit == 50
assert "profiles" in ig_cli.COMMANDS

print("ig_step3_check: OK")
