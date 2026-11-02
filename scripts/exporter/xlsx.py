"""Запись результатов запросов в xlsx: по листу на запрос, шапка, фильтр, ширина колонок."""

import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Font, PatternFill

MAX_ROWS = 1_048_575  # лимит Excel минус шапка
MAX_CELL = 32_767  # длина текста в ячейке
MAX_WIDTH = 60
_BAD_SHEET = re.compile(r"[\[\]:*?/\\]")


def cell_value(v):
    """Значение Postgres -> то, что умеет хранить Excel. Строки никогда не становятся формулами."""
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):  # Excel не знает часовых поясов: пишем UTC без tzinfo
        return v.astimezone(timezone.utc).replace(tzinfo=None) if v.tzinfo else v
    if isinstance(v, date):
        return v
    if isinstance(v, (dict, tuple)) or (isinstance(v, list) and any(not _plain(x) for x in v)):
        return _clip(json.dumps(v, ensure_ascii=False, default=str))
    if isinstance(v, list):
        return _clip(", ".join(str(x) for x in v))
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return _clip(str(v))


def _plain(x) -> bool:
    return x is None or isinstance(x, (str, int, float, bool, Decimal))


def _clip(s: str) -> str:
    return ILLEGAL_CHARACTERS_RE.sub("", s)[:MAX_CELL]


def sheet_title(name: str, used: set[str]) -> str:
    """Имя листа: до 31 символа, без []:*?/\\, уникальное в книге."""
    base = (_BAD_SHEET.sub("_", name).strip("'") or "query")[:31]
    title, n = base, 2
    while title.lower() in used:
        suffix = f"_{n}"
        title, n = base[: 31 - len(suffix)] + suffix, n + 1
    used.add(title.lower())
    return title


def write_workbook(path: Path, sheets: list[tuple[str, list[str], list[tuple]]]) -> list[str]:
    """sheets: [(имя, колонки, строки)]. Возвращает предупреждения (например, обрезку строк)."""
    wb = Workbook()
    wb.remove(wb.active)
    used: set[str] = set()
    warnings: list[str] = []
    head_font = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="DDE6F0")
    for name, cols, rows in sheets:
        ws = wb.create_sheet(sheet_title(name, used))
        if len(rows) > MAX_ROWS:
            warnings.append(
                f"{name}: строк {len(rows)}, в лист влезает {MAX_ROWS}, остальные обрезаны"
            )
            rows = rows[:MAX_ROWS]
        ws.append(cols)
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
        widths = [len(str(c)) for c in cols]
        for i, row in enumerate(rows):
            vals = [cell_value(v) for v in row]
            ws.append(vals)
            for j, v in enumerate(vals):
                if isinstance(v, str):
                    ws.cell(row=i + 2, column=j + 1).data_type = "s"  # «=1+1» остаётся текстом
                if i < 200 and v is not None:
                    widths[j] = max(widths[j], len(str(v)))
        for j, w in enumerate(widths):
            ws.column_dimensions[ws.cell(row=1, column=j + 1).column_letter].width = min(
                MAX_WIDTH, w + 2
            )
        ws.freeze_panes = "A2"
        if cols:
            ws.auto_filter.ref = ws.dimensions
    if not wb.sheetnames:
        wb.create_sheet("empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return warnings
