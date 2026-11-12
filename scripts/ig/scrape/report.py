"""Итоги запуска Instagram: результат профиля, запись для profile_runs, сводка."""

import json
from pathlib import Path

from core.logs import log

MODE_LABELS = {
    "profile": "только профиль",
    "posts": "посты без комментариев",
    "full": "полный проход",
}


def new_result(username: str, mode: str, status: str = "ok", note: str = "", **extra) -> dict:
    """Пустой итог профиля: ключи, которые читают print_summary и profile_run_entry."""
    return {
        "username": username,
        "status": status,  # ok | empty | private | not_found | abort | error | not_run
        "mode": mode,
        "note": note,
        "abort_kind": None,  # 429 | auth | None
        "stop_reason": None,
        "followers": None,
        **extra,
    }


def profile_run_entry(res: dict) -> dict:
    """Запись о запуске профиля для profile_runs (comments/media пока нулевые)."""
    return {
        "username": res.get("username"),
        "status": res.get("status"),
        "mode": res.get("mode"),
        "stop_reason": res.get("stop_reason"),
        "abort_kind": res.get("abort_kind"),
        "note": res.get("note") or "",
        "started_at": res.get("started_at"),
        "finished_at": res.get("finished_at"),
        "duration_sec": res.get("duration_sec"),
        "posts": res.get("posts") or {},
        "comments": {},
        "media": {},
        "pages_opened": res.get("pages_opened") or 0,
    }


def print_summary(results: list[dict]) -> None:
    log("\n" + "=" * 60)
    log("ИТОГИ")
    log("=" * 60)
    for r in results:
        followers = r.get("followers")
        line = (
            f"@{r['username']}: {r['status']} | "
            f"подписчиков: {followers if followers is not None else '—'} | "
            f"режим: {MODE_LABELS.get(r.get('mode'), r.get('mode'))}"
        )
        p = r.get("posts")
        if p:
            line += f" | постов: новых {p.get('new', 0)}, обновлено {p.get('refreshed', 0)}"
            line += f" | остановка: {r.get('stop_reason') or '—'}"
        if r["note"]:
            line += f" | {r['note']}"
        log(line)
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    log("Всего: " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())))


def write_summary(results: list[dict], out_dir: Path) -> Path:
    """summary.json в каталоге --output (служебное поле pstat не пишется)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "summary.json"
    data = [{k: v for k, v in r.items() if k != "pstat"} for r in results]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
