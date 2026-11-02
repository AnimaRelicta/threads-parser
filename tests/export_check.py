"""Проверка выгрузки без БД: разбор файла запросов, параметры, запись xlsx.

Запуск: uv run python tests/export_check.py
"""

import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from openpyxl import load_workbook  # noqa: E402

from exporter.queries import BASE_QUERIES  # noqa: E402
from exporter.sqlfile import bind, parse_queries, used_params  # noqa: E402
from exporter.xlsx import cell_value, sheet_title, write_workbook  # noqa: E402

# 1. Файл запросов
text = """
-- комментарий до первого запроса
-- name: a
-- desc: первый
SELECT 1 AS x;

-- name: b
SELECT username
FROM profiles
WHERE followers_count < 100::int AND date_utc >= :since
LIMIT :limit;
-- name: empty
"""
q = parse_queries(text)
assert list(q) == ["a", "b"], list(q)
assert q["a"].desc == "первый" and q["a"].sql == "SELECT 1 AS x"
assert q["b"].sql.endswith("LIMIT :limit") and q["b"].source == "file"

# 2. Параметры: порядок появления, повтор = один $n, ::каст не трогается
sql, args = bind(
    "SELECT :limit::int, :since, :since::timestamptz, 5::int", {"since": 7, "limit": 3}
)
assert sql == "SELECT $1::int, $2, $2::timestamptz, 5::int", sql
assert args == [3, 7]
assert bind("SELECT 1", {"since": 1}) == ("SELECT 1", [])
assert used_params("a :since b ::since c :limit") == {"since", "limit"}, used_params("::since")

# 3. Базовые запросы: у всех есть описание и оба параметра
assert {"spam-classes", "banned", "profiles", "threads", "top-authors", "instagram"} <= set(
    BASE_QUERIES
)
for name, bq in BASE_QUERIES.items():
    assert bq.desc and used_params(bq.sql) == {"since", "limit"}, name
    assert ";" not in bq.sql, name

# 4. Значения ячеек
assert cell_value(Decimal("1.5")) == 1.5
assert cell_value({"a": "б"}) == '{"a": "б"}'
assert cell_value(["x", "y"]) == "x, y"
assert cell_value("a\x00b") == "ab"
assert cell_value("x" * 40000) == "x" * 32767
msk = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
assert cell_value(msk) == datetime(2026, 11, 2, 12, 0) and cell_value(msk).tzinfo is None
assert cell_value(None) is None and cell_value(True) is True

# 5. Имена листов
used: set[str] = set()
assert sheet_title("a/b[1]", used) == "a_b_1_"
assert sheet_title("A/B[1]", used) == "A_B_1__2"
assert len(sheet_title("x" * 50, used)) == 31

# 6. xlsx: шапка, строки, текст «=...» не становится формулой, пустой результат
with tempfile.TemporaryDirectory() as d:
    path = Path(d) / "sub" / "t.xlsx"
    rows = [("@u1", 5, "=1+1", msk), ("@u2", None, "+7 700", None)]
    warns = write_workbook(path, [("one", ["user", "n", "text", "at"], rows), ("none", ["c"], [])])
    assert warns == []
    wb = load_workbook(path)
    assert wb.sheetnames == ["one", "none"]
    ws = wb["one"]
    assert [c.value for c in ws[1]] == ["user", "n", "text", "at"]
    assert ws["C2"].value == "=1+1" and ws["C2"].data_type == "s", ws["C2"].data_type
    assert ws["B3"].value is None and ws.freeze_panes == "A2"
    assert [c.value for c in wb["none"][1]] == ["c"] and wb["none"].max_row == 1

print("export_check: OK")
