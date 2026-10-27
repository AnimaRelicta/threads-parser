"""Чтение и запись файлов: JSON, JSONL, списки профилей."""

import json
import os
import re
from pathlib import Path

from core.consts import USERNAME_RE
from core.logs import log


def normalize_username(s: str) -> str:
    """Имя профиля из ссылки, @имени или просто имени. Пустая строка, если нет."""
    s = s.strip()
    if "threads." in s:
        m = re.search(r"threads\.(?:com|net)/@([^/?#\s]+)", s)
        return m.group(1) if m else ""
    name = re.split(r"[/?#\s]", s.lstrip("@"), maxsplit=1)[0]
    return name.lstrip("@")


def read_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return default


def read_usernames(path: Path) -> list[str]:
    """Читает файл со ссылками/именами профилей (по одной в строке)."""
    if not path.exists():
        raise SystemExit(f"Файл не найден: {path}")
    names: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = normalize_username(line)
        if not name:
            log(f"[*] В строке нет имени профиля, пропускаю: {line}")
            continue
        if not USERNAME_RE.match(name):
            log(f"[!] Не похоже на профиль, пропускаю: {line}")
            continue
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    return names


# ---------------------------------------------------------------------------
# Шаг 4: хранилище. posts.jsonl — дедуп + атомарная запись (тот
# же приём, что save_cookies/save_posts в functions.py: пишем во .tmp рядом,
# затем os.replace). jobs.json — чекпоинт задач поиска (по аналогии с
# anchor.json в run.py): готовое окно не перезапускается без --force.
# ---------------------------------------------------------------------------


def read_jsonl(path: Path) -> list[dict]:
    """Построчный JSON. Битые строки молча пропускаются (не роняют загрузку
    при обрыве записи посреди строки, например)."""
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def write_jsonl_atomic(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)  # атомарно, как save_cookies/save_posts в functions.py
