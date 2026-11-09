"""Выполнение листов отчёта: подстановка параметров с типами из SQL и чтение из БД."""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import asyncpg

from core.timeutil import parse_since
from reporter.spec import PLACEHOLDER, STRING, Block, check_params, placeholders

_TRUE = {"1", "true", "yes", "y", "on", "да"}
_FALSE = {"0", "false", "no", "n", "off", "нет"}
_INTS = {"int2", "int4", "int8", "oid"}
_FLOATS = {"float4", "float8"}


def coerce(type_name: str, raw: str | None, label: str = "параметр"):
    """Текст из `-- param:` / `--set` -> значение для типа Postgres. Пусто = NULL.

    Даты и моменты понимают и сокращения --since: 30d, 4w, 1m, 1y."""
    if raw is None or raw.strip() == "":
        return None
    v = raw.strip()
    try:
        if type_name in _INTS:
            return int(v)
        if type_name in _FLOATS:
            return float(v.replace(",", "."))
        if type_name == "numeric":
            return Decimal(v.replace(",", "."))
        if type_name == "bool":
            if v.lower() in _TRUE:
                return True
            if v.lower() in _FALSE:
                return False
            raise ValueError(v)
        if type_name == "date":
            dt = parse_since(v)
            return dt.date() if isinstance(dt, datetime) else date.fromisoformat(v)
        if type_name in ("timestamptz", "timestamp"):
            dt = parse_since(v)
            if dt is None:
                return None
            return (
                dt
                if type_name == "timestamptz"
                else dt.astimezone(timezone.utc).replace(tzinfo=None)
            )
    except (ValueError, InvalidOperation, SystemExit) as e:
        raise SystemExit(f"{label}: «{raw}» не подходит к типу {type_name}") from e
    return v  # text, varchar и всё остальное — строкой


def bind(sql: str) -> tuple[str, list[str]]:
    """:имя -> $1, $2 (в порядке появления); возвращает (sql, имена параметров по номерам)."""
    names = placeholders(sql)
    parts: list[str] = []
    pos = 0
    # строки в кавычках не трогаем: подменяем только за их пределами
    for lit in STRING.finditer(sql):
        parts.append(_subst(sql[pos : lit.start()], names))
        parts.append(lit.group(0))
        pos = lit.end()
    parts.append(_subst(sql[pos:], names))
    return "".join(parts), names


def _subst(chunk: str, names: list[str]) -> str:
    return PLACEHOLDER.sub(lambda m: f"${names.index(m.group(1)) + 1}", chunk)


def resolve_values(b: Block, overrides: dict[str, str]) -> dict[str, str]:
    """Значения параметров листа: умолчания из файла, поверх них --set (общие и `лист.имя`)."""
    values = dict(b.params)
    for key, val in overrides.items():
        scope, dot, name = key.partition(".")
        if dot and scope == b.name:
            values[name] = val
        elif not dot and key in values:
            values[key] = val
    return values


async def run_block(
    conn: asyncpg.Connection, b: Block, overrides: dict[str, str]
) -> tuple[list[str], list[tuple], dict[str, str]]:
    """(колонки, строки, применённые параметры). Только чтение: read-only транзакция."""
    check_params(b)
    values = resolve_values(b, overrides)
    sql, names = bind(b.sql)
    try:
        async with conn.transaction(readonly=True):
            stmt = await conn.prepare(sql)
            cols = [a.name for a in stmt.get_attributes()]
            if not cols:
                raise SystemExit(f"[{b.name}] запрос не возвращает строк (нужен SELECT)")
            types = [t.name for t in stmt.get_parameters()]
            args = [
                coerce(t, values[n], f"[{b.name}] :{n}") for n, t in zip(names, types, strict=True)
            ]
            rows = [tuple(r.values()) for r in await stmt.fetch(*args)]
    except asyncpg.PostgresError as e:
        raise SystemExit(f"[{b.name}] ошибка запроса: {type(e).__name__}: {e}") from e
    return cols, rows, {n: values[n] for n in names}


def parse_sets(items: list[str]) -> dict[str, str]:
    """`--set имя=значение` или `--set блок.имя=значение`."""
    out: dict[str, str] = {}
    for item in items:
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            raise SystemExit(f"--set ожидает имя=значение, получено «{item}»")
        out[name.strip()] = value.strip()
    return out


def check_sets(blocks: list[Block], overrides: dict[str, str]) -> None:
    """--set с неизвестным именем почти всегда опечатка: лучше остановиться."""
    known = {b.name for b in blocks}
    for b in blocks:
        known.update(f"{b.name}.{p}" for p in b.params)
        known.update(b.params)
    for key in overrides:
        if key not in known:
            raise SystemExit(f"--set {key}: такого параметра нет ни в одном листе (см. --list)")
