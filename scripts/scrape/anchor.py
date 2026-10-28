"""Якорь профиля: метка самого нового поста для режима «только новое».

Хранится в таблице profile_anchors (db/profiles.py); здесь только логика.
"""

from datetime import datetime

from core.timeutil import parse_dt

DEFAULT_STOP_AFTER = 5


def anchor_covers(anchor: dict, cutoff: datetime | None) -> bool:
    """Покрывает ли прошлый полный проход окно, которое просят сейчас.

    covered_from = null: пройден весь профиль; иначе пройдено до этой даты.
    """
    covered = parse_dt(anchor.get("covered_from"))
    if anchor.get("covered_from") is None:
        return True
    return cutoff is not None and covered is not None and covered <= cutoff


def compute_anchor(
    anchor: dict | None,
    stop_reason: str,
    cutoff: datetime | None,
    proc_dates: list[datetime],
    posts: list[dict],
    skipped: dict,
) -> dict | None:
    """Новый якорь после успешного прохода или None, если обновлять нечего.

    Полный проход задаёт, какое окно покрыто (covered_from); проход по якорю окно не
    расширяет, а только двигает метку самого нового поста.
    """
    cands = [(parse_dt(p.get("date_utc")), p["code"]) for p in posts]
    cands += [(parse_dt(v.get("date")), c) for c, v in skipped.items()]
    cands = [(d, c) for d, c in cands if d is not None]
    if not cands:
        return None
    newest_date, newest_code = max(cands)
    if anchor is not None:
        covered = anchor.get("covered_from")
    elif stop_reason == "old" and cutoff is not None:
        covered = cutoff.isoformat()
    elif stop_reason == "limit":
        if not proc_dates:
            return None
        covered = min(proc_dates).isoformat()
    elif stop_reason == "end":
        covered = None  # лента закончилась: пройден весь профиль
    else:
        return None
    return {
        "newest_code": newest_code,
        "newest_date": newest_date.isoformat(),
        "covered_from": covered,
        "known_posts": len(posts),
    }
