"""Запросы для выгрузки: разбор файла с именованными запросами и подстановка параметров.

Формат файла (`data/export_queries.sql`, путь задаёт EXPORT_QUERIES_FILE):

    -- name: my-query
    -- desc: что выгружает (видно в `export.py --list`)
    SELECT username, followers_count FROM profiles WHERE followers_count < :max_followers;

Запрос заканчивается `;` или следующим `-- name:`. В тексте можно использовать :since
(timestamptz или NULL) и :limit (число или NULL), их задают флаги --since и --limit.
"""

import re
from dataclasses import dataclass
from pathlib import Path

PARAMS = ("since", "limit")
_PARAM_RE = re.compile(r"(?<!:):(" + "|".join(PARAMS) + r")\b")  # `::int` не затрагивается
_NAME_RE = re.compile(r"^--\s*name:\s*(\S+)\s*$", re.I)
_DESC_RE = re.compile(r"^--\s*desc:\s*(.*)$", re.I)
_PARAM_LINE = re.compile(r"^--[ \t]*param:[ \t]*(\w+)[ \t]*=[ \t]*(.*?)[ \t]*$", re.I | re.M)


@dataclass(frozen=True)
class Query:
    name: str
    desc: str
    sql: str
    source: str = "base"  # base | file | cli


def parse_queries(text: str, source: str = "file") -> dict[str, Query]:
    """Именованные запросы из текста файла; блоки без SQL пропускаются."""
    found: dict[str, Query] = {}
    name: str | None = None
    desc = ""
    body: list[str] = []

    def flush() -> None:
        sql = "\n".join(body).strip().rstrip(";").strip()
        if name and sql:
            found[name] = Query(name, desc, sql, source)

    for line in text.splitlines():
        m = _NAME_RE.match(line.strip())
        if m:
            flush()
            name, desc, body = m.group(1), "", []
            continue
        d = _DESC_RE.match(line.strip())
        if d and name and not body:
            desc = d.group(1).strip()
            continue
        if name is not None:
            body.append(line)
    flush()
    return found


def load_queries_file(path: Path) -> dict[str, Query]:
    if not path.exists():
        return {}
    return parse_queries(path.read_text(encoding="utf-8-sig"), "file")


def bind(sql: str, values: dict) -> tuple[str, list]:
    """Заменяет :since и :limit на $1, $2 (в порядке появления); возвращает (sql, аргументы)."""
    order: list[str] = []

    def repl(m: re.Match) -> str:
        n = m.group(1)
        if n not in order:
            order.append(n)
        return f"${order.index(n) + 1}"

    return _PARAM_RE.sub(repl, sql), [values.get(n) for n in order]


def used_params(sql: str) -> set[str]:
    return set(_PARAM_RE.findall(sql))


def declared_params(sql: str) -> dict[str, str]:
    """Параметры по умолчанию из строк `-- param: имя=значение` (общий формат sqlrun и отчёта)."""
    return {m.group(1): m.group(2) for m in _PARAM_LINE.finditer(sql)}


def strip_comments(sql: str) -> str:
    """Текст запроса без `-- комментариев` (параметры в комментариях не считаются)."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())
