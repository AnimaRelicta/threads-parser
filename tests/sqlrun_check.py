"""Проверка sqlrun.py без БД: параметры, защита от UPDATE без WHERE, разбор файла задач."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from exporter.sqlfile import Query, load_queries_file  # noqa: E402
from sqlrun import bind, check_safe, coerce, declared_params, parse_sets  # noqa: E402

assert (coerce("100"), coerce("1.5"), coerce("Казахстан")) == (100, 1.5, "Казахстан")
assert parse_sets(["a=1", "b = x y"]) == {"a": "1", "b": "x y"}

sql, args = bind("UPDATE t SET a = :x::int, b = :y WHERE c = :x::int -- :ignored", {"x": "5", "y": "z"})
assert sql.count("$1") == 2 and "$2" in sql and "ignored" not in sql and args == [5, "z"], (sql, args)
try:
    bind("SELECT :nope", {})
except SystemExit:
    pass
else:
    raise AssertionError("неизвестный параметр должен падать")

for bad in ("UPDATE profiles SET is_target = true", "DELETE FROM posts", "WITH a AS (SELECT 1) UPDATE p SET x = 1"):
    try:
        check_safe(Query("t", "", bad))
    except SystemExit:
        continue
    raise AssertionError(bad)
for good in ("UPDATE profiles SET is_target = true WHERE id = 1", "SELECT 1"):
    check_safe(Query("t", "", good))

tasks = load_queries_file(ROOT / "data" / "db_tasks.sql")
assert {"select-targets", "targets-stats", "targets-on"} <= set(tasks), set(tasks)
assert declared_params(tasks["select-targets"].sql) == {"min_followers": "100", "min_per_day": "1"}
for q in tasks.values():
    check_safe(q)
    sql, _ = bind(q.sql, declared_params(q.sql))
    assert ":" not in sql.replace("::", ""), q.name
print("sqlrun_check: OK")
