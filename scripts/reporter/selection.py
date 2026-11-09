"""Какие листы войдут в отчёт: `-- enabled:` в файле, поверх него --only / --on / --off."""

from datetime import date
from pathlib import Path

from reporter.spec import Block


def find(blocks: list[Block], ref: str, flag: str) -> Block:
    """Лист по имени блока или по имени листа (без регистра)."""
    r = ref.strip().lower()
    for b in blocks:
        if r in (b.name.lower(), b.sheet.lower()):
            return b
    raise SystemExit(f"{flag} {ref}: такого листа нет (см. --list)")


def select_blocks(
    blocks: list[Block], only: list[str], on: list[str], off: list[str]
) -> list[Block]:
    """--only заменяет выбор из файла; --on добавляет; --off убирает (--off сильнее всех).
    Порядок листов всегда как в файле."""
    chosen = (
        {find(blocks, r, "--only").name for r in only}
        if only
        else {b.name for b in blocks if b.enabled}
    )
    chosen.update(find(blocks, r, "--on").name for r in on)
    chosen.difference_update(find(blocks, r, "--off").name for r in off)
    picked = [b for b in blocks if b.name in chosen]
    if not picked:
        raise SystemExit(
            "Не выбран ни один лист: проверьте enabled в файле и флаги --only/--on/--off"
        )
    return picked


def out_path(out: str | None, report_dir: Path, today: date | None = None) -> Path:
    """Один файл на все листы: threads_report_ГГГГ-ММ-ДД.xlsx. --out: файл или папка."""
    default = f"threads_report_{(today or date.today()).isoformat()}.xlsx"
    if not out:
        return report_dir / default
    p = Path(out)
    if p.is_dir() or out.endswith(("/", "\\")):  # Path съедает хвостовой слэш
        return p / default
    return p if p.suffix.lower() == ".xlsx" else p.with_suffix(".xlsx")
