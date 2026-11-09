"""Описание отчёта: разбор `data/build_report.sql` на листы и общие фрагменты SQL.

Формат файла: блоки в формате `exporter/sqlfile.py` (`-- name:`, `-- desc:`, запрос), плюс
строки-директивы сразу под `-- desc:`:

    -- name: posts-kz
    -- desc: что на листе
    -- sheet: Посты KZ              имя листа (по умолчанию = name; до 31 символа, без : [ ] * ? / \\)
    -- enabled: on                  on | off: войдёт ли лист в отчёт (флаги --on/--off/--only сильнее)
    -- param: home_country=KZ       параметр запроса: в тексте :home_country::text, значение по умолчанию
    SELECT ... WHERE author_country = :home_country::text AND ...;

Общая логика не копируется между листами, а выносится во фрагмент: блок с `-- kind: fragment`
содержит подзапрос в скобках `(SELECT ...)`, в листах он подставляется как `{{имя}}`. Фрагмент
может ссылаться на другие фрагменты и объявлять свои `-- param:`: лист получает их вместе с ним.
Тип параметра задаёт приведение в SQL (`:since::timestamptz`), пустое значение = NULL.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from exporter.sqlfile import Query, declared_params, parse_queries, strip_comments

_DIRECTIVE = re.compile(
    r"^[ \t]*--[ \t]*(sheet|kind|enabled)[ \t]*:[ \t]*(.*?)[ \t]*$", re.I | re.M
)
_FRAGMENT_REF = re.compile(r"\{\{\s*([\w-]+)\s*\}\}")
PLACEHOLDER = re.compile(r"(?<!:):([a-z_]\w*)\b", re.I)  # `::int` не затрагивается
STRING = re.compile(r"'(?:[^']|'')*'")
_ON = {"on", "true", "yes", "y", "1", "да", "вкл"}
_OFF = {"off", "false", "no", "n", "0", "нет", "выкл"}


@dataclass(frozen=True)
class Block:
    name: str
    sheet: str
    desc: str
    enabled: bool
    sql: str  # фрагменты раскрыты, комментарии убраны, параметры как :имя
    params: dict[str, str] = field(default_factory=dict)  # имя -> значение по умолчанию


def _flag(name: str, value: str) -> bool:
    v = value.strip().lower()
    if v in _ON:
        return True
    if v in _OFF:
        return False
    raise SystemExit(f"[{name}] enabled: ждёт on или off, получено «{value}»")


def _directives(q: Query) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2) for m in _DIRECTIVE.finditer(q.sql)}


def _expand(
    name: str,
    sql: str,
    frags: dict[str, Query],
    params: dict[str, str],
    chain: tuple[str, ...] = (),
) -> str:
    """Подставляет {{фрагмент}} (рекурсивно) и собирает `-- param:` фрагментов в params."""

    def repl(m: re.Match) -> str:
        ref = m.group(1)
        if ref not in frags:
            raise SystemExit(f"[{name}] нет фрагмента «{ref}» (kind: fragment)")
        if ref in chain:
            raise SystemExit(f"[{name}] фрагменты ссылаются друг на друга по кругу: {ref}")
        for k, v in declared_params(frags[ref].sql).items():
            params.setdefault(k, v)
        return _expand(name, strip_comments(frags[ref].sql), frags, params, (*chain, ref))

    return _FRAGMENT_REF.sub(repl, sql)


def parse_report(text: str) -> list[Block]:
    """Листы отчёта в порядке файла. Фрагменты в результат не попадают."""
    parsed = parse_queries(text, "file")
    frags = {
        n: q for n, q in parsed.items() if _directives(q).get("kind", "").lower() == "fragment"
    }
    blocks: list[Block] = []
    sheets: dict[str, str] = {}
    for name, q in parsed.items():
        if name in frags:
            continue
        d = _directives(q)
        if d.get("kind", "sheet").lower() != "sheet":
            raise SystemExit(f"[{name}] kind: ждёт sheet или fragment, получено «{d['kind']}»")
        sheet = d.get("sheet") or name
        if sheet.lower() in sheets:
            raise SystemExit(f"Лист «{sheet}» занят блоком «{sheets[sheet.lower()]}» и «{name}»")
        sheets[sheet.lower()] = name
        params = dict(declared_params(q.sql))  # свои параметры главнее параметров фрагментов
        sql = _expand(name, strip_comments(q.sql), frags, params).strip()
        if not sql:
            raise SystemExit(f"[{name}] пустой запрос")
        blocks.append(Block(name, sheet, q.desc, _flag(name, d.get("enabled", "on")), sql, params))
    return blocks


def load_report(path: Path) -> list[Block]:
    if not path.exists():
        raise SystemExit(f"Нет файла отчёта: {path} (REPORT_FILE или --file)")
    return parse_report(path.read_text(encoding="utf-8-sig"))


def placeholders(sql: str) -> list[str]:
    """Имена :параметров в порядке появления (строки в кавычках не смотрим)."""
    return list(dict.fromkeys(PLACEHOLDER.findall(STRING.sub("''", sql))))


def check_params(b: Block) -> None:
    """Каждый :параметр в запросе объявлен `-- param:`; лишние объявления — тоже ошибка."""
    used = set(placeholders(b.sql))
    missing = sorted(used - set(b.params))
    if missing:
        raise SystemExit(
            f"[{b.name}] параметр :{missing[0]} не объявлен: добавьте строку "
            f"`-- param: {missing[0]}=значение` (пусто = NULL)"
        )
    unused = sorted(set(b.params) - used)
    if unused:
        raise SystemExit(f"[{b.name}] объявлен, но не используется: {', '.join(unused)}")
