"""Проверка отчёта без БД: data/build_report.sql целиком, выбор листов флагами, имя файла.

Запуск: uv run python tests/report_cli_check.py
"""

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from reporter.runner import bind, check_sets, parse_sets  # noqa: E402
from reporter.selection import out_path, select_blocks  # noqa: E402
from reporter.spec import check_params, load_report  # noqa: E402

blocks = load_report(ROOT / "data" / "build_report.sql")
names = [b.name for b in blocks]
assert len(names) == len(set(names)) >= 10, names
assert not any(n in names for n in ("kz-profiles", "period-posts", "kz-posts")), (
    "фрагмент стал листом"
)

# каждый лист рабочего файла: параметры объявлены, фрагменты раскрыты, $-номера без пропусков
for b in blocks:
    check_params(b)
    assert "{{" not in b.sql, b.name
    sql, pnames = bind(b.sql)
    assert all(f"${i + 1}" in sql for i in range(len(pnames))), b.name
    assert len(b.sheet) <= 31, b.sheet
check_sets(blocks, parse_sets(["home_location=UZ", "top-authors.limit=10", "since=1m"]))

by = {b.name: b for b in blocks}
assert by["runs"].enabled is False and by["summary"].enabled is True

# выбор листов: файл < --only; --on добавляет; --off сильнее всего; порядок как в файле
base = [b.name for b in blocks if b.enabled]
assert [b.name for b in select_blocks(blocks, [], [], [])] == base
assert [b.name for b in select_blocks(blocks, [], ["runs"], [])][-1] == "runs"
assert [b.name for b in select_blocks(blocks, ["profiles", "Сводка"], [], [])] == [
    "summary",
    "profiles",
]
assert "comments" not in [b.name for b in select_blocks(blocks, [], [], ["comments"])]
assert [b.name for b in select_blocks(blocks, ["summary", "profiles"], [], ["profiles"])] == [
    "summary"
]
for bad in (([], ["нет"], []), ([], [], ["нет"]), (["нет"], [], []), (["runs"], [], ["runs"])):
    try:
        select_blocks(blocks, *bad)
    except SystemExit:
        continue
    raise AssertionError(bad)

# имя файла: одна книга с датой
d = date(2026, 11, 9)
rd = Path("r")
assert out_path(None, rd, d) == rd / "threads_report_2026-11-09.xlsx"
assert out_path("x/", rd, d) == Path("x") / "threads_report_2026-11-09.xlsx"
assert out_path("my", rd, d) == Path("my.xlsx") and out_path("a.XLSX", rd, d) == Path("a.XLSX")

print("report_cli_check: ok")
