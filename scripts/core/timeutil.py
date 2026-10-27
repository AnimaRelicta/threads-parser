"""Время и даты: ISO-строки, разбор --since, сдвиг месяцев."""

import calendar
import re
from datetime import datetime, timedelta, timezone


def iso(ts) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def ts_from_iso(s: str | None) -> int | None:
    d = parse_dt(s)
    return int(d.timestamp()) if d else None


def parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def shift_months(dt: datetime, months: int) -> datetime:
    """dt минус календарные месяцы; день обрезается до длины месяца (31 мар - 1m = 28 фев)."""
    total = dt.year * 12 + (dt.month - 1) - months
    year, month0 = divmod(total, 12)
    if year < 1:
        raise ValueError("слишком большой период")
    month = month0 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1]))


def parse_since(value: str | None, now: datetime | None = None) -> datetime | None:
    """'30d', '4w', '1m' (календарный месяц), '1y' (календарный год) или дата '2026-09-01'."""
    if not value:
        return None
    v = value.strip().lower()
    now = now or datetime.now(timezone.utc)
    m = re.fullmatch(r"(\d+)\s*([dwmy])", v)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit in "dw":
            return now - timedelta(days=n * (7 if unit == "w" else 1))
        try:
            return shift_months(now, n * (12 if unit == "y" else 1))
        except (ValueError, OverflowError):
            raise SystemExit(f"--since: слишком большой период «{value}»")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise SystemExit("--since: используйте 30d, 4w, 1m, 1y или дату 2026-09-01")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
