"""Реестр аккаунтов data/accounts.json и синхронизация файлов кук с ним."""

import json
from datetime import datetime, timezone

from config import Settings
from registry.cookies import cookie_jar


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_registry(st: Settings) -> dict:
    """Возвращает {имя: запись}. Битый или отсутствующий файл = пустой реестр."""
    if st.accounts_file.exists():
        try:
            data = json.loads(st.accounts_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def save_registry(st: Settings, reg: dict) -> None:
    st.accounts_file.parent.mkdir(parents=True, exist_ok=True)
    st.accounts_file.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")


def new_record(name: str, username: str | None, user_id: str | None = None) -> dict:
    return {
        "username": username,
        "user_id": user_id,
        "cookies_file": f"{name}.json",
        "status": "active",  # active | needs_login | disabled
        "created_at": now_iso(),
        "last_login_at": now_iso(),
        "last_run_at": None,
        "last_error": None,
        "runs_total": 0,  # число запусков
        "runs": [],  # подробная запись о каждом запуске (см. mark_account)
        **{k: 0 for k in STAT_KEYS},
    }


# Счётчики реестра (за всё время): запуски (runs_total; подробности в списке runs), обработанные профили, новые посты и комментарии,
# ответы 429 и проблемы авторизации. Ключ статистики из воркера -> поле реестра.
STAT_KEYS = ("profiles", "posts", "comments", "media_files", "errors_429", "errors_auth")


STAT_FROM_WORKER = {"e429": "errors_429", "eauth": "errors_auth"}


def apply_stats(rec: dict, stats: dict) -> None:
    """Прибавляет приращения к счётчикам записи (у старых записей полей нет: считаем с 0)."""
    for k in STAT_KEYS:
        rec[k] = int(rec.get(k) or 0)
    for k, v in stats.items():
        key = STAT_FROM_WORKER.get(k, k)
        if key in STAT_KEYS:
            rec[key] += int(v or 0)


def sync_cookie_files(st: Settings) -> list[str]:
    """Заводит в реестре файлы кук, которые положили в папку руками (без import)."""
    if not st.cookies_dir.exists():
        return []
    reg = load_registry(st)
    added = []
    for f in sorted(st.cookies_dir.glob("*.json")):
        if f.stem in reg:
            continue
        try:
            jar = cookie_jar(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        if jar.get("sessionid"):
            reg[f.stem] = new_record(f.stem, None, jar.get("ds_user_id"))
            added.append(f.stem)
    if added:
        save_registry(st, reg)
        print(f"[+] Найдены файлы кук вне реестра, добавлены: {', '.join(added)}")
    return added


def list_active(st: Settings) -> list[str]:
    """Имена аккаунтов, которыми можно парсить."""
    sync_cookie_files(st)
    return [
        n
        for n, r in load_registry(st).items()
        if r.get("status", "active") == "active" and st.cookies_path(n).exists()
    ]


def pick_accounts(st: Settings, requested: list[str] | None) -> list[str]:
    """Аккаунты для запуска: указанные или все активные."""
    active = list_active(st)
    if requested:
        reg = load_registry(st)
        bad = [n for n in requested if n not in reg or not st.cookies_path(n).exists()]
        if bad:
            raise SystemExit(
                f"Нет аккаунтов: {', '.join(bad)}. Есть: {', '.join(active) or 'пусто'} "
                "(uv run scripts/accounts.py list)"
            )
        return list(dict.fromkeys(requested))
    if not active:
        raise SystemExit(
            "Нет активных аккаунтов. Войдите: uv run scripts/accounts.py add 1 "
            "или перенесите куки: uv run scripts/accounts.py import <файл>"
        )
    return active


def next_name(st: Settings, reg: dict, prefix: str = "account") -> str:
    i = 1
    while True:
        name = f"{prefix}_{i:03d}"
        if name not in reg and not st.cookies_path(name).exists():
            return name
        i += 1


def find_duplicate(reg: dict, user_id: str | None) -> str | None:
    for n, r in reg.items():
        if user_id and r.get("user_id") == user_id:
            return n
    return None
