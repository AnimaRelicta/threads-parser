"""Проверка ядра отчёта без БД: разбор build_report.sql, фрагменты, параметры, типы.

Запуск: uv run python tests/report_check.py
"""

import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from reporter.runner import bind, check_sets, coerce, parse_sets, resolve_values  # noqa: E402
from reporter.spec import check_params, parse_report, placeholders  # noqa: E402

TEXT = """
-- name: base
-- kind: fragment
-- param: since=
(SELECT * FROM posts WHERE :since::timestamptz IS NULL OR date_utc >= :since::timestamptz)

-- name: home
-- kind: fragment
-- param: home_country=KZ
(SELECT * FROM {{base}} b WHERE b.country = :home_country::text AND b.note <> 'a:b')

-- name: one
-- desc: первый лист
-- sheet: Первый
-- param: limit=
SELECT * FROM {{home}} h LIMIT :limit::int; -- :commented

-- name: two
-- enabled: off
-- param: home_country=UZ
SELECT 1 AS x FROM {{base}} z WHERE z.c = :home_country::text;
"""

blocks = parse_report(TEXT)
assert [b.name for b in blocks] == ["one", "two"], "фрагменты не листы"
one, two = blocks
assert (one.sheet, two.sheet) == ("Первый", "two")
assert (one.enabled, two.enabled) == (True, False)
assert one.desc == "первый лист"
# параметры фрагментов приходят вместе с ним; свои главнее
assert one.params == {"limit": "", "home_country": "KZ", "since": ""}, one.params
assert two.params["home_country"] == "UZ" and two.params["since"] == ""
assert "{{" not in one.sql and "commented" not in one.sql
assert placeholders(one.sql) == ["limit", "home_country", "since"] or set(
    placeholders(one.sql)
) == {"limit", "home_country", "since"}
assert "a:b" not in placeholders(one.sql) and "b" not in placeholders(one.sql)
for b in blocks:
    check_params(b)

# bind: один :имя = один $n, ::каст и строки в кавычках не трогаются
sql, names = bind(one.sql)
assert "'a:b'" in sql and "::timestamptz" in sql and "$" in sql
assert names == placeholders(one.sql) and sql.count("$" + str(names.index("since") + 1)) == 2

# значения: умолчания, --set общий и `лист.имя`
sets = parse_sets(["home_country=UZ", "two.home_country=RU", "since=30d"])
assert resolve_values(one, sets)["home_country"] == "UZ"
assert resolve_values(two, sets)["home_country"] == "RU"
check_sets(blocks, sets)
try:
    check_sets(blocks, {"homecountry": "x"})
except SystemExit:
    pass
else:
    raise AssertionError("опечатка в --set должна падать")

# типы Postgres
assert coerce("int4", "5") == 5 and coerce("int8", "") is None
assert coerce("float8", "1,5") == 1.5 and coerce("numeric", "2.5") == Decimal("2.5")
assert coerce("bool", "да") is True and coerce("bool", "off") is False
assert coerce("text", " KZ ") == "KZ"
assert coerce("date", "2026-09-01") == date(2026, 9, 1)
assert isinstance(coerce("timestamptz", "30d"), datetime) and coerce("timestamptz", "30d").tzinfo
assert coerce("timestamp", "2026-09-01").tzinfo is None
for t, v in (("int4", "x"), ("bool", "maybe"), ("timestamptz", "вчера"), ("date", "31.12")):
    try:
        coerce(t, v)
    except SystemExit:
        continue
    raise AssertionError((t, v))


# ошибки описания
def fails(text: str) -> None:
    try:
        parse_report(text)
    except SystemExit:
        return
    raise AssertionError(text)


fails("-- name: a\nSELECT {{nope}};")
fails("-- name: f\n-- kind: fragment\n(SELECT {{f}})\n-- name: a\nSELECT {{f}};")
fails("-- name: a\n-- sheet: S\nSELECT 1;\n-- name: b\n-- sheet: s\nSELECT 2;")
fails("-- name: a\n-- enabled: maybe\nSELECT 1;")
fails("-- name: a\n-- kind: view\nSELECT 1;")
for bad in (
    "-- name: a\nSELECT :x::int;",  # не объявлен
    "-- name: a\n-- param: y=1\nSELECT 1;",  # объявлен, но не используется
):
    try:
        check_params(parse_report(bad)[0])
    except SystemExit:
        continue
    raise AssertionError(bad)

print("report_check: ok")
