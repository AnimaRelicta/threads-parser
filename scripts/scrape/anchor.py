"""anchor.json: метка самого нового поста для режима «только новое»."""

import json
from datetime import datetime
from pathlib import Path

from core.fsio import read_json
from core.timeutil import parse_dt
from registry.store import now_iso

ANCHOR_NAME = "anchor.json"


DEFAULT_STOP_AFTER = 5


def read_anchor(path: Path) -> dict | None:
    a = read_json(path, None)
    return a if isinstance(a, dict) and a.get("newest_code") else None


def anchor_covers(anchor: dict, cutoff: datetime | None) -> bool:
    """Покрывает ли прошлый полный проход окно, которое просят сейчас.

    covered_from = null: пройден весь профиль; иначе пройдено до этой даты.
    """
    covered = parse_dt(anchor.get("covered_from"))
    if anchor.get("covered_from") is None:
        return True
    return cutoff is not None and covered is not None and covered <= cutoff


def write_anchor(
    path: Path, newest_code: str, newest_date: str | None, covered_from: str | None, total: int
) -> None:
    path.write_text(
        json.dumps(
            {
                "newest_code": newest_code,
                "newest_date": newest_date,
                "covered_from": covered_from,
                "known_posts": total,
                "updated_at": now_iso(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def save_anchor(
    path: Path,
    anchor: dict | None,
    stop_reason: str,
    cutoff: datetime | None,
    proc_dates: list[datetime],
    posts: list[dict],
    skipped: dict,
) -> None:
    """Пишет anchor.json после успешного прохода.

    Полный проход задаёт, какое окно покрыто (covered_from); проход по якорю окно не
    расширяет, а только двигает метку самого нового поста.
    """
    cands = [(parse_dt(p.get("date_utc")), p["code"]) for p in posts]
    cands += [(parse_dt(v.get("date")), c) for c, v in skipped.items()]
    cands = [(d, c) for d, c in cands if d is not None]
    if not cands:
        return
    newest_date, newest_code = max(cands)
    if anchor is not None:
        covered = anchor.get("covered_from")
    elif stop_reason == "old" and cutoff is not None:
        covered = cutoff.isoformat()
    elif stop_reason == "limit":
        if not proc_dates:
            return
        covered = min(proc_dates).isoformat()
    elif stop_reason == "end":
        covered = None  # лента закончилась: пройден весь профиль
    else:
        return
    write_anchor(path, newest_code, newest_date.isoformat(), covered, len(posts))
