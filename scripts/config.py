"""Конфигурация: константы Threads, чтение scripts/.env, объект Settings."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(__file__).resolve().parent / ".env"

TH = "https://www.threads.com"
TH_URLS = ["https://www.threads.com", "https://www.threads.net"]
COOKIE_DOMAIN = ".threads.com"


DEFAULT_LIMIT = 10  # веток на профиль в базовом прогоне, если LIMIT и --limit не заданы


@dataclass
class Settings:
    accounts: list[str] | None = None  # пусто = все активные аккаунты из реестра
    workers: int = 1  # воркеров (пар вкладок в одном браузере) на аккаунт: m
    headless: bool = False
    login_timeout: int = 300
    # Значения по умолчанию для run.py (флаги командной строки их переопределяют)
    limit: int | None = None  # None = не задан: DEFAULT_LIMIT для парсинга, 0 (все) для --fill
    since: str | None = None
    comments: bool = False
    depth: int = 2
    max_comments: int = 0
    recheck_days: int = 7
    with_videos: bool = False
    delay: float = 4.0
    profile_delay: float = 20.0
    stop_after_known: int = 5
    debug: bool = False
    profiles_file: Path = ROOT / "data" / "threads_profiles.txt"
    cookies_dir: Path = ROOT / "data" / "cookies"
    # Только для разового dbadmin.py import-accounts (старый реестр); сам парсер
    # аккаунты хранит в Postgres и этот файл не читает.
    accounts_file: Path = ROOT / "data" / "accounts.json"
    export_dir: Path = ROOT / "data" / "export"
    export_queries_file: Path = ROOT / "data" / "export_queries.sql"  # EXPORT_QUERIES_FILE
    output_dir: Path = ROOT / "data" / "output"
    # --- scripts/search.py: поиск по выдаче Threads --------------------------
    search_queries_file: Path = ROOT / "data" / "threads_queries.txt"
    search_output_dir: Path = ROOT / "output" / "search"
    search_workers: int = 3
    search_window_days: int = 0

    # --- LLM-классификация профилей (scripts/classify/) ----------------------
    llm_proxy_url: str | None = None  # LLM_PROXY_URL
    llm_proxy_api_key: str | None = None  # LLM_PROXY_API_KEY (только в scripts/.env)
    llm_model: str | None = None  # LLM_MODEL
    llm_timeout_sec: int = 60
    llm_max_retries: int = 3
    llm_workers: int = 2
    llm_sample_threads: int = 10  # сколько последних веток профиля идёт в контекст

    # --- спам-детектор (core/spam.py) ----------------------------------------
    spam_min_followers: int = 1000  # SPAM_MIN_FOLLOWERS: столько подписчиков и больше = не спам
    # классы 1/2/3 (core/spamclass.py)
    spam_class_sample: int = 10  # SPAM_CLASS_SAMPLE: сколько первых веток идёт в детекцию
    spam_class_low_followers: int = 100  # SPAM_CLASS_LOW_FOLLOWERS: меньше = класс 3
    spam_class_gap_hours: float = 24.0  # SPAM_CLASS_GAP_HOURS: пауза меньше = «часто»
    spam_class_many_replies: int = 20  # SPAM_CLASS_MANY_REPLIES: медиана ответов >= «много»
    spam_class_few_replies: int = 5  # SPAM_CLASS_FEW_REPLIES: медиана ответов <= «мало»

    # Postgres и Proxy для поиска
    database_url: str | None = None  # DATABASE_URL (пароль только в scripts/.env, не в коде)
    proxy: str | None = None  # PROXY, например socks5://127.0.0.1:12334

    def cookies_path(self, account: str) -> Path:
        return self.cookies_dir / f"{account}.json"


def parse_env_file(path: Path) -> dict[str, str]:
    """Минимальный разбор .env: KEY=VALUE, комментарии с #, кавычки по краям."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        else:
            val = val.split(" #")[0].strip()
        values[key.strip()] = val
    return values


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _int(v: str | None, default: int) -> int:
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


def _float(v: str | None, default: float) -> float:
    try:
        return float(v.replace(",", ".")) if v not in (None, "") else default
    except ValueError:
        return default


def _path(v: str | None, default: Path) -> Path:
    if not v:
        return default
    p = Path(v)
    return p if p.is_absolute() else ROOT / p


def load_settings() -> Settings:
    """Читает scripts/.env; переменные окружения имеют приоритет над файлом."""
    env = parse_env_file(ENV_FILE)
    env.update({k: v for k, v in os.environ.items() if k in env})
    get = env.get
    return Settings(
        accounts=[a.strip() for a in (get("ACCOUNTS") or "").split(",") if a.strip()],
        workers=max(1, _int(get("WORKERS_PER_ACCOUNT"), 1)),
        headless=_bool(get("HEADLESS"), False),
        login_timeout=_int(get("LOGIN_TIMEOUT_SEC"), 300),
        limit=max(0, _int(get("LIMIT"), 0)) if (get("LIMIT") or "").strip() else None,
        since=(get("SINCE") or "").strip() or None,
        comments=_bool(get("COMMENTS"), False),
        depth=_int(get("DEPTH"), 2),
        max_comments=max(0, _int(get("MAX_COMMENTS"), 0)),
        recheck_days=max(0, _int(get("RECHECK_DAYS"), 7)),
        with_videos=_bool(get("WITH_VIDEOS"), False),
        delay=_float(get("DELAY"), 4.0),
        profile_delay=_float(get("PROFILE_DELAY"), 20.0),
        stop_after_known=max(1, _int(get("STOP_AFTER_KNOWN"), 5)),
        debug=_bool(get("DEBUG"), False),
        profiles_file=_path(get("PROFILES_FILE"), ROOT / "data" / "threads_profiles.txt"),
        cookies_dir=_path(get("COOKIES_DIR"), ROOT / "data" / "cookies"),
        accounts_file=_path(get("ACCOUNTS_FILE"), ROOT / "data" / "accounts.json"),
        export_dir=_path(get("EXPORT_DIR"), ROOT / "data" / "export"),
        export_queries_file=_path(get("EXPORT_QUERIES_FILE"), ROOT / "data" / "export_queries.sql"),
        output_dir=_path(get("OUTPUT_DIR"), ROOT / "data" / "output"),
        search_queries_file=_path(
            get("SEARCH_QUERIES_FILE"), ROOT / "data" / "threads_queries.txt"
        ),
        search_output_dir=_path(get("SEARCH_OUTPUT_DIR"), ROOT / "output" / "search"),
        search_workers=max(1, _int(get("SEARCH_WORKERS"), 3)),
        search_window_days=max(0, _int(get("SEARCH_WINDOW_DAYS"), 0)),
        llm_proxy_url=(get("LLM_PROXY_URL") or "").strip() or None,
        llm_proxy_api_key=(get("LLM_PROXY_API_KEY") or "").strip() or None,
        llm_model=(get("LLM_MODEL") or "").strip() or None,
        llm_timeout_sec=max(1, _int(get("LLM_TIMEOUT_SEC"), 60)),
        llm_max_retries=max(0, _int(get("LLM_MAX_RETRIES"), 3)),
        llm_workers=max(1, _int(get("LLM_WORKERS"), 2)),
        llm_sample_threads=max(1, _int(get("LLM_SAMPLE_THREADS"), 10)),
        spam_min_followers=max(0, _int(get("SPAM_MIN_FOLLOWERS"), 1000)),
        spam_class_sample=max(1, _int(get("SPAM_CLASS_SAMPLE"), 10)),
        spam_class_low_followers=max(0, _int(get("SPAM_CLASS_LOW_FOLLOWERS"), 100)),
        spam_class_gap_hours=max(0.0, _float(get("SPAM_CLASS_GAP_HOURS"), 24.0)),
        spam_class_many_replies=max(0, _int(get("SPAM_CLASS_MANY_REPLIES"), 20)),
        spam_class_few_replies=max(0, _int(get("SPAM_CLASS_FEW_REPLIES"), 5)),
        database_url=(get("DATABASE_URL") or "").strip() or None,
        proxy=(get("PROXY") or "").strip() or None,
    )
