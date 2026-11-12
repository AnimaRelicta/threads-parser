"""Проверка шага 7 Instagram (ig export, ig sql, общий движок) без БД и сети."""

import argparse
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
    sys.modules["asyncpg"] = fake

import export  # noqa: E402
import sqlrun  # noqa: E402
from exporter import runner, tasks  # noqa: E402
from exporter.sqlfile import Query, bind, declared_params, load_queries_file, used_params  # noqa: E402
from ig import cli, export_cli, sql_cli  # noqa: E402
from ig.consts import IG_SCHEMA  # noqa: E402
from ig.export_queries import BASE_QUERIES  # noqa: E402

# 1. Движок параметризован: threads и instagram разделены по схеме, файлу и имени команды
ig_spec = runner.ExportSpec(
    "cli.py ig export", IG_SCHEMA, BASE_QUERIES, ROOT / "data" / "ig_export_queries.sql", ROOT
)
assert IG_SCHEMA == "instagram"
assert set(BASE_QUERIES) == {
    "ig-profiles", "ig-posts", "ig-comments", "ig-media", "ig-sources", "ig-coverage"
}, set(BASE_QUERIES)  # fmt: skip
for name, q in BASE_QUERIES.items():
    assert q.desc and q.source == "base", name
    assert used_params(q.sql) == {"since", "limit"}, (name, used_params(q.sql))
    assert ";" not in q.sql, name
    sql, args = bind(q.sql, {"since": None, "limit": None})
    assert len(args) == 2 and ":since" not in sql and ":limit" not in sql, name
    assert "threads" not in sql.lower().replace("threads_", ""), name  # только схема instagram

# 2. Свои запросы ig: примеры из data/ig_export_queries.sql
own = load_queries_file(ROOT / "data" / "ig_export_queries.sql")
assert {"top-profiles", "posts-period", "posts-no-files"} <= set(own), set(own)
for name, q in own.items():
    assert used_params(q.sql) == {"since", "limit"}, name
assert set(runner.available(ig_spec, ROOT / "data" / "ig_export_queries.sql")) >= set(BASE_QUERIES)

# 3. Разбор флагов: у ig export те же --since/--limit/--out; ошибки называют свою команду
ap = runner.build_parser(ig_spec)
a = ap.parse_args(["ig-profiles", "ig-posts", "--since", "1m", "--limit", "5", "--out", "x/"])
assert a.queries == ["ig-profiles", "ig-posts"] and a.limit == 5 and a.out == "x/"
try:
    runner.collect(ig_spec, argparse.Namespace(queries=["нет"], file=None, sql=None, sheet=None),
                   ig_spec.queries_file)  # fmt: skip
except SystemExit as e:
    assert "cli.py ig export --list" in str(e), e
else:
    raise AssertionError("неизвестный запрос должен падать")

# 4. Threads не изменился: тот же набор базовых запросов, имя команды в ошибках
from exporter.queries import BASE_QUERIES as TH_QUERIES  # noqa: E402

assert {"profiles", "threads", "top-authors", "instagram"} <= set(TH_QUERIES)
th_spec = runner.ExportSpec("export.py", "threads", TH_QUERIES, ROOT / "data" / "export_queries.sql", ROOT)
try:
    runner.collect(th_spec, argparse.Namespace(queries=["нет"], file=None, sql=None, sheet=None),
                   th_spec.queries_file)  # fmt: skip
except SystemExit as e:
    assert "export.py --list" in str(e), e
assert callable(export.main) and callable(export_cli.main) and callable(sql_cli.main)

# 5. ig sql: файл задач, параметры, защита от UPDATE/DELETE без WHERE
tf = load_queries_file(ROOT / "data" / "ig_db_tasks.sql")
need = {
    "targets-on", "targets-off", "set-target-by-followers", "reset-unprocessed",
    "retry-failed-media", "unprivate", "drop-guessed-not-found", "drop-profile", "targets-stats",
}  # fmt: skip
assert need <= set(tf), need - set(tf)
assert declared_params(tf["set-target-by-followers"].sql) == {"min_followers": "100"}
assert declared_params(tf["targets-off"].sql) == {"status": "not_found"}
for q in tf.values():
    assert q.desc, q.name
    tasks.check_safe(q)
    values = {**declared_params(q.sql), "username": "nick"}
    sql, args = tasks.bind(q.sql, values)
    assert ":" not in sql.replace("::", ""), (q.name, sql)
sql, args = tasks.bind(tf["set-target-by-followers"].sql, {"min_followers": "500"})
assert args == [500] and "$1::int" in sql
sql, args = tasks.bind(tf["drop-profile"].sql, {"username": "Nick"})
assert args == ["Nick"] and sql.count("$1") == 1
try:
    tasks.bind(tf["drop-profile"].sql, declared_params(tf["drop-profile"].sql))
except SystemExit as e:
    assert "username" in str(e)
else:
    raise AssertionError("drop-profile без --set username должен падать")
# каскад: перед удалением профиля чистятся profile_runs (FK без CASCADE)
for name in ("drop-profile", "drop-guessed-not-found"):
    assert "DELETE FROM profile_runs" in tf[name].sql, name
for bad in ("UPDATE profiles SET is_target = true", "DELETE FROM posts"):
    try:
        tasks.check_safe(Query("t", "", bad))
    except SystemExit:
        continue
    raise AssertionError(bad)

# 6. sqlrun.py: те же имена и общий движок
assert sqlrun.bind is tasks.bind and sqlrun.check_safe is tasks.check_safe

# 7. Команды зарегистрированы, модули импортируются
assert {"export", "sql"} <= set(cli.COMMANDS)
assert "export" in cli.usage() and "sql" in cli.usage()

# 8. SQL прогресса: четыре блока, только чтение
prog = (ROOT / "scripts" / "ig" / "db" / "progress.sql").read_text(encoding="utf-8")
assert prog.count("\\echo") == 4 and "SET search_path = instagram" in prog
for word in ("UPDATE ", "DELETE ", "INSERT ", "DROP ", "ALTER "):
    assert word not in prog, word

print("ig_step7_check: OK")
