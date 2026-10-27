"""Задачи поиска: режимы, URL выдачи, окна дат, список запросов."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from config import TH

MODES = ("top", "recent")


DATE_FMT = "%Y-%m-%d"


def parse_date(s: str | None) -> date | None:
    """'2026-10-20' -> date. Пустая строка/None -> None."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), DATE_FMT).date()
    except ValueError:
        raise SystemExit(f"Неверная дата «{s}», нужен формат YYYY-MM-DD")


def build_search_url(
    query: str, mode: str, after: date | None = None, before: date | None = None
) -> str:
    """URL поиска Threads для одного режима.

    top:      .../search?q={q}&serp_type=tags&hl=ru
    recent:   то же + &filter=recent
    """
    if mode not in MODES:
        raise ValueError(f"Неизвестный режим: {mode}")
    url = f"{TH}/search?q={quote(query)}&serp_type=tags&hl=ru"
    if mode == "recent":
        url += "&filter=recent"
    if after is not None:
        url += f"&after_date={after:{DATE_FMT}}"
    if before is not None:
        url += f"&before_date={before:{DATE_FMT}}"
    return url


@dataclass(frozen=True)
class SearchTask:
    """Одна задача поиска: запрос, режим, окно дат (любая граница может быть пустой)."""

    query: str
    mode: str
    after: date | None = None
    before: date | None = None

    def url(self) -> str:
        return build_search_url(self.query, self.mode, self.after, self.before)

    def label(self) -> str:
        window = ""
        if self.after or self.before:
            window = f" [{self.after or '..'}..{self.before or '..'}]"
        return f"{self.query!r}/{self.mode}{window}"


def split_windows(
    after: date | None, before: date | None, window_days: int
) -> list[tuple[date | None, date | None]]:
    """Режет период [after, before] на непересекающиеся окна по window_days дней.

    window_days <= 0 или одна из границ не задана -> одно окно на весь период
    (нарезка возможна только когда обе даты известны).
    Окна не пересекаются: конец окна = начало следующего минус 1 день (т. е.
    [after, after+window_days-1], следующее начинается с after+window_days).
    Это предположение, что before_date включительна (как и after_date);
    требует подтверждения на живой выдаче (--recon) — см. README/шаг 2.
    Если выяснится, что before_date исключительна, здесь достаточно убрать
    "- timedelta(days=1)" ниже.
    """
    if window_days <= 0 or after is None or before is None:
        return [(after, before)]
    if after > before:
        raise SystemExit(f"--after ({after}) позже --before ({before})")
    windows: list[tuple[date, date]] = []
    start = after
    step = timedelta(days=window_days)
    one_day = timedelta(days=1)
    while start <= before:
        end = min(start + step - one_day, before)
        windows.append((start, end))
        start = end + one_day
    return windows


def build_tasks(
    queries: list[str], modes: list[str], after: date | None, before: date | None, window_days: int
) -> list[SearchTask]:
    """Все задачи: запрос x режим x окно."""
    tasks: list[SearchTask] = []
    for q in queries:
        for mode in modes:
            for a, b in split_windows(after, before, window_days):
                tasks.append(SearchTask(q, mode, a, b))
    return tasks


def load_queries(path: Path) -> list[str]:
    """Запросы из файла, по одному на строку. # и пустые строки игнорируются."""
    if not path.exists():
        raise SystemExit(f"Файл не найден: {path}")
    out: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower() not in seen:
            seen.add(line.lower())
            out.append(line)
    return out
