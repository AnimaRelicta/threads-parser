"""Хранилище результатов поиска: posts.jsonl и jobs.json."""

import json
import os
from pathlib import Path

from core.fsio import read_json, read_jsonl, write_jsonl_atomic
from registry.store import now_iso
from serp.tasks import SearchTask


class PostStore:
    """Посты по code (дедуп из ТЗ: «посты по code»). Загружается один раз при
    старте — повторный запуск не плодит дубли, при обрыве продолжает с того,
    что уже сохранено."""

    def __init__(self, path: Path):
        self.path = path
        self.by_code: dict[str, dict] = {r["code"]: r for r in read_jsonl(path) if r.get("code")}
        self.new = 0
        self.dupes = 0

    def add(self, post: dict) -> bool:
        """True, если пост новый и добавлен."""
        if post["code"] in self.by_code:
            self.dupes += 1
            return False
        self.by_code[post["code"]] = post
        self.new += 1
        return True

    def save(self) -> None:
        write_jsonl_atomic(self.path, list(self.by_code.values()))


def job_key(task: SearchTask) -> str:
    """Ключ задачи для jobs.json: запрос+режим+окно (без учёта регистра
    запроса, чтобы «Олх» и «олх» в файле запросов не плодили две записи)."""
    return "|".join(
        (
            task.query.lower(),
            task.mode,
            task.after.isoformat() if task.after else "",
            task.before.isoformat() if task.before else "",
        )
    )


def load_jobs(path: Path) -> dict:
    data = read_json(path, {})
    return data if isinstance(data, dict) else {}


def save_jobs(path: Path, jobs: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def is_job_done(jobs: dict, task: SearchTask, force: bool) -> bool:
    """Готовое окно не перезапускается, если не указан --force (как --force
    для anchor.json в run.py)."""
    if force:
        return False
    rec = jobs.get(job_key(task))
    return bool(rec and rec.get("status") == "done")


def mark_job_done(jobs: dict, task: SearchTask, **extra) -> None:
    jobs[job_key(task)] = {
        "status": "done",
        "finished_at": now_iso(),
        "query": task.query,
        "mode": task.mode,
        **extra,
    }


def mark_job_failed(jobs: dict, task: SearchTask, error: str) -> None:
    """Попытки исчерпаны (429 на всех аккаунтах): задача не «done», но и не в очереди
    этого запуска. Следующий запуск (без --force) снова возьмёт её в работу, т. к.
    is_job_done проверяет только status == "done"."""
    jobs[job_key(task)] = {
        "status": "failed",
        "finished_at": now_iso(),
        "query": task.query,
        "mode": task.mode,
        "last_error": error,
    }
