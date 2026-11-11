"""Конфигурация: константы Threads, чтение scripts/.env, объект Settings."""

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path(__file__).resolve().parent / ".env"

TH = "https://www.threads.com"
TH_URLS = ["https://www.threads.com", "https://www.threads.net"]
COOKIE_DOMAIN = ".threads.com"

IG = "https://www.instagram.com"
IG_APP_ID = "936619743392459"
IG_COOKIE_DOMAIN = ".instagram.com"


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
    delay: float = 2.0
    profile_delay: float = 8.0
    stop_after_known: int = 5
    debug: bool = False
    profiles_file: Path = ROOT / "data" / "threads_profiles.txt"
    cookies_dir: Path = ROOT / "data" / "cookies"
    # Только для разового dbadmin.py import-accounts (старый реестр); сам парсер
    # аккаунты хранит в Postgres и этот файл не читает.
    accounts_file: Path = ROOT / "data" / "accounts.json"
    export_dir: Path = ROOT / "data" / "export"
    export_queries_file: Path = ROOT / "data" / "export_queries.sql"  # EXPORT_QUERIES_FILE
    db_tasks_file: Path = ROOT / "data" / "db_tasks.sql"  # DB_TASKS_FILE (sqlrun.py)
    report_file: Path = ROOT / "data" / "build_report.sql"  # REPORT_FILE (build_report.py)
    report_dir: Path = ROOT / "data" / "report"  # REPORT_DIR
    output_dir: Path = ROOT / "data" / "output"
    # --- scripts/search.py: поиск по выдаче Threads --------------------------
    search_queries_file: Path = ROOT / "data" / "threads_queries.txt"
    search_output_dir: Path = ROOT / "output" / "search"
    search_workers: int = 3
    search_window_days: int = 0
    search_start_delay: float = 1.5
    search_page_wait_ms: int = 2500
    search_scroll_min_ms: int = 500
    search_scroll_quiet_ms: int = 1200
    search_scroll_max_ms: int = 6000
    search_stale_scrolls: int = 2
    search_429_pause: int = 60
    # --- scripts/postlist.py: сбор веток по списку ссылок ----------------------
    posts_file: Path = ROOT / "data" / "threads_posts.txt"
    posts_workers: int = 3
    posts_delay: float = 0.3

    # --- LLM-классификация профилей (scripts/classify/) ----------------------
    llm_proxy_url: str | None = None  # LLM_PROXY_URL
    llm_proxy_api_key: str | None = None  # LLM_PROXY_API_KEY (только в scripts/.env)
    llm_model: str | None = None  # LLM_MODEL
    llm_timeout_sec: int = 60
    llm_max_retries: int = 3
    llm_workers: int = 2
    llm_sample_threads: int = 10  # сколько последних веток профиля идёт в контекст

    # --- классификация профилей через Jev (TypeSafe System One, scripts/classify/jev_*.py) ---
    jev_api_key: str | None = None  # OPENROUTER_API_KEY (или JEV_API_KEY), только в scripts/.env
    jev_url: str = "https://openrouter.ai/api/v1/systemone"  # JEV_URL (System One через OpenRouter)
    jev_model: str = "typesafe/jev-1.14"  # JEV_MODEL: версия, а не алиас ~typesafe/jev-latest
    jev_proxy: str | None = None  # JEV_PROXY, например socks5://127.0.0.1:12334 (Hiddify)
    jev_min_version: str = "1.14"  # JEV_MIN_VERSION: ответ модели старше этой версии = стоп
    jev_timeout_sec: int = 60
    jev_max_retries: int = 3
    jev_workers: int = 4
    jev_min_prob: float = 0.6  # JEV_MIN_PROB: вероятность лучшего класса ниже = «Прочее»
    jev_min_confidence: float = 0.5  # JEV_MIN_CONFIDENCE: confidence ответа ниже = «Прочее»
    jev_noul_gate: bool = True  # JEV_NOUL_GATE: вторая проверка «подходит ли хоть один класс 1-6»
    jev_noul_min: float = 0.5  # JEV_NOUL_MIN: Noul ниже = «Прочее»

    # Postgres и Proxy для поиска
    database_url: str | None = None  # DATABASE_URL (пароль только в scripts/.env, не в коде)
    proxy: str | None = None  # PROXY, например socks5://127.0.0.1:12334

    def cookies_path(self, account: str) -> Path:
        return self.cookies_dir / f"{account}.json"


@dataclass
class IgSettings:
    """Настройки Instagram-пакета `ig` (переменные IG_* в scripts/.env)."""

    accounts: list[str] | None = None  # пусто = все активные IG-аккаунты
    tabs: int = 3  # вкладок на аккаунт, m
    headless: bool = False
    limit: int = 0  # постов на профиль, 0 = все
    since: str | None = None
    comments: bool = False
    depth: int = 2  # потолок в Instagram: 2 (комментарий и ответы)
    max_comments: int = 0
    images: bool = True
    videos: bool = False
    avatars: bool = False
    recheck_days: int = 7
    stop_after_known: int = 4
    delay: float = 4.0
    profile_delay: float = 8.0
    pause_429: int = 300  # сек, cooldown аккаунта после 429
    min_free_gb: float = 5.0  # минимум свободного места в хранилище
    login_timeout: int = 300
    profiles_file: Path = ROOT / "data" / "ig_profiles.txt"
    cookies_dir: Path = ROOT / "data" / "ig_cookies"
    storage_dir: Path = ROOT.parent / "storage" / "instagram-parcer"
    output_dir: Path = ROOT / "data" / "ig_output"
    export_dir: Path = ROOT / "data" / "export"
    export_queries_file: Path = ROOT / "data" / "ig_export_queries.sql"
    db_tasks_file: Path = ROOT / "data" / "ig_db_tasks.sql"
    # Только для разового `ig accounts import-legacy` (реестр insta-parser)
    accounts_file: Path = ROOT / "data" / "ig_accounts.json"
    # Общие с Threads
    database_url: str | None = None
    proxy: str | None = None

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
        delay=_float(get("DELAY"), 2.0),
        profile_delay=_float(get("PROFILE_DELAY"), 8.0),
        stop_after_known=max(1, _int(get("STOP_AFTER_KNOWN"), 5)),
        debug=_bool(get("DEBUG"), False),
        profiles_file=_path(get("PROFILES_FILE"), ROOT / "data" / "threads_profiles.txt"),
        cookies_dir=_path(get("COOKIES_DIR"), ROOT / "data" / "cookies"),
        accounts_file=_path(get("ACCOUNTS_FILE"), ROOT / "data" / "accounts.json"),
        export_dir=_path(get("EXPORT_DIR"), ROOT / "data" / "export"),
        export_queries_file=_path(get("EXPORT_QUERIES_FILE"), ROOT / "data" / "export_queries.sql"),
        db_tasks_file=_path(get("DB_TASKS_FILE"), ROOT / "data" / "db_tasks.sql"),
        report_file=_path(get("REPORT_FILE"), ROOT / "data" / "build_report.sql"),
        report_dir=_path(get("REPORT_DIR"), ROOT / "data" / "report"),
        output_dir=_path(get("OUTPUT_DIR"), ROOT / "data" / "output"),
        search_queries_file=_path(
            get("SEARCH_QUERIES_FILE"), ROOT / "data" / "threads_queries.txt"
        ),
        search_output_dir=_path(get("SEARCH_OUTPUT_DIR"), ROOT / "output" / "search"),
        search_workers=max(1, _int(get("SEARCH_WORKERS"), 3)),
        search_window_days=max(0, _int(get("SEARCH_WINDOW_DAYS"), 0)),
        search_start_delay=max(0.0, _float(get("SEARCH_START_DELAY"), 1.5)),
        search_page_wait_ms=max(0, _int(get("SEARCH_PAGE_WAIT_MS"), 2500)),
        search_scroll_min_ms=max(0, _int(get("SEARCH_SCROLL_MIN_MS"), 500)),
        search_scroll_quiet_ms=max(0, _int(get("SEARCH_SCROLL_QUIET_MS"), 1200)),
        search_scroll_max_ms=max(150, _int(get("SEARCH_SCROLL_MAX_MS"), 6000)),
        search_stale_scrolls=max(1, _int(get("SEARCH_STALE_SCROLLS"), 2)),
        search_429_pause=max(0, _int(get("SEARCH_429_PAUSE"), 60)),
        posts_file=_path(get("POSTS_FILE"), ROOT / "data" / "threads_posts.txt"),
        posts_workers=max(1, _int(get("POSTS_WORKERS"), 3)),
        posts_delay=max(0.0, _float(get("POSTS_DELAY"), 0.3)),
        llm_proxy_url=(get("LLM_PROXY_URL") or "").strip() or None,
        llm_proxy_api_key=(get("LLM_PROXY_API_KEY") or "").strip() or None,
        llm_model=(get("LLM_MODEL") or "").strip() or None,
        llm_timeout_sec=max(1, _int(get("LLM_TIMEOUT_SEC"), 60)),
        llm_max_retries=max(0, _int(get("LLM_MAX_RETRIES"), 3)),
        llm_workers=max(1, _int(get("LLM_WORKERS"), 2)),
        llm_sample_threads=max(1, _int(get("LLM_SAMPLE_THREADS"), 10)),
        jev_api_key=(get("OPENROUTER_API_KEY") or get("JEV_API_KEY") or "").strip() or None,
        jev_url=(get("JEV_URL") or "").strip() or "https://openrouter.ai/api/v1/systemone",
        jev_model=(get("JEV_MODEL") or "").strip() or "typesafe/jev-1.14",
        jev_proxy=(get("JEV_PROXY") or "").strip() or None,
        jev_min_version=(get("JEV_MIN_VERSION") or "").strip() or "1.14",
        jev_timeout_sec=max(1, _int(get("JEV_TIMEOUT_SEC"), 60)),
        jev_max_retries=max(0, _int(get("JEV_MAX_RETRIES"), 3)),
        jev_workers=max(1, _int(get("JEV_WORKERS"), 4)),
        jev_min_prob=min(1.0, max(0.0, _float(get("JEV_MIN_PROB"), 0.6))),
        jev_min_confidence=min(1.0, max(0.0, _float(get("JEV_MIN_CONFIDENCE"), 0.5))),
        jev_noul_gate=_bool(get("JEV_NOUL_GATE"), True),
        jev_noul_min=min(1.0, max(0.0, _float(get("JEV_NOUL_MIN"), 0.5))),
        database_url=(get("DATABASE_URL") or "").strip() or None,
        proxy=(get("PROXY") or "").strip() or None,
    )


def load_ig_settings() -> IgSettings:
    """Читает IG_* из scripts/.env; переменные окружения имеют приоритет над файлом."""
    env = parse_env_file(ENV_FILE)
    env.update({k: v for k, v in os.environ.items() if k in env})
    get = env.get
    return IgSettings(
        accounts=[a.strip() for a in (get("IG_ACCOUNTS") or "").split(",") if a.strip()],
        tabs=max(1, _int(get("IG_TABS_PER_ACCOUNT"), 3)),
        headless=_bool(get("IG_HEADLESS"), False),
        limit=max(0, _int(get("IG_LIMIT"), 0)),
        since=(get("IG_SINCE") or "").strip() or None,
        comments=_bool(get("IG_COMMENTS"), False),
        depth=_int(get("IG_DEPTH"), 2),
        max_comments=max(0, _int(get("IG_MAX_COMMENTS"), 0)),
        images=_bool(get("IG_IMAGES"), True),
        videos=_bool(get("IG_VIDEOS"), False),
        avatars=_bool(get("IG_AVATARS"), False),
        recheck_days=max(0, _int(get("IG_RECHECK_DAYS"), 7)),
        stop_after_known=max(1, _int(get("IG_STOP_AFTER_KNOWN"), 4)),
        delay=_float(get("IG_DELAY"), 4.0),
        profile_delay=_float(get("IG_PROFILE_DELAY"), 8.0),
        pause_429=max(0, _int(get("IG_429_PAUSE"), 300)),
        min_free_gb=max(0.0, _float(get("IG_MIN_FREE_GB"), 5.0)),
        login_timeout=_int(get("LOGIN_TIMEOUT_SEC"), 300),
        profiles_file=_path(get("IG_PROFILES_FILE"), ROOT / "data" / "ig_profiles.txt"),
        cookies_dir=_path(get("IG_COOKIES_DIR"), ROOT / "data" / "ig_cookies"),
        storage_dir=_path(get("IG_STORAGE_DIR"), ROOT.parent / "storage" / "instagram-parcer"),
        output_dir=_path(get("IG_OUTPUT_DIR"), ROOT / "data" / "ig_output"),
        export_dir=_path(get("EXPORT_DIR"), ROOT / "data" / "export"),
        export_queries_file=_path(
            get("IG_EXPORT_QUERIES_FILE"), ROOT / "data" / "ig_export_queries.sql"
        ),
        db_tasks_file=_path(get("IG_DB_TASKS_FILE"), ROOT / "data" / "ig_db_tasks.sql"),
        accounts_file=_path(get("IG_ACCOUNTS_FILE"), ROOT / "data" / "ig_accounts.json"),
        database_url=(get("DATABASE_URL") or "").strip() or None,
        proxy=(get("PROXY") or "").strip() or None,
    )
