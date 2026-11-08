"""Точка входа: классификация профилей через LLM (категория профиля).

Работает через БД и proxy API модели, без браузера. Запускается подкомандой cli.py
(файл не называется classify.py, чтобы не совпасть с именем пакета):

    uv run scripts/cli.py classify --limit 50
    uv run scripts/cli.py classify --limit 200 --workers 4
    uv run scripts/cli.py classify --limit 50 --source failed          # повтор неудачных
    uv run scripts/cli.py classify --limit 50 --recheck                # переклассифицировать
"""

import argparse
import asyncio

from classify.runner import run_classify
from config import ENV_FILE, load_settings
from db.stats import print_reports

SOURCES = ("new", "failed")
STATS_REPORTS = ["profiles_by_category", "classify_latency", "classify_usage"]


def build_parser(st) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cli.py classify",
        description=f"Классификация профилей LLM. Настройки: {ENV_FILE}",
    )
    ap.add_argument(
        "--source",
        choices=SOURCES,
        help="new: без категории и без ошибки (по умолчанию); failed: с ошибкой классификации",
    )
    ap.add_argument(
        "--limit", type=int, help="сколько профилей обработать за запуск (обязателен без --stats)"
    )
    ap.add_argument(
        "--workers", type=int, default=st.llm_workers, help="профилей одновременно (LLM_WORKERS)"
    )
    ap.add_argument(
        "--recheck",
        action="store_true",
        help="переклассифицировать профили, у которых категория уже есть; "
        "с --source не сочетается",
    )
    ap.add_argument(
        "--stats",
        action="store_true",
        help="отдельный режим: категории профилей, время классификации, расход запросов к LLM",
    )
    return ap


def main() -> None:
    st = load_settings()
    args = build_parser(st).parse_args()
    if args.stats:
        print_reports(st, STATS_REPORTS)
        return
    if args.limit is None or args.limit < 1:
        raise SystemExit("--limit обязателен и должен быть не меньше 1")
    if args.workers < 1:
        raise SystemExit("--workers должен быть не меньше 1")
    if args.recheck and args.source:
        raise SystemExit("--recheck и --source несовместимы")
    missing = [
        name
        for name, val in (
            ("LLM_PROXY_URL", st.llm_proxy_url),
            ("LLM_PROXY_API_KEY", st.llm_proxy_api_key),
            ("LLM_MODEL", st.llm_model),
        )
        if not val
    ]
    if missing:
        raise SystemExit(f"Не заданы в scripts/.env: {', '.join(missing)}")
    source = "reviewed" if args.recheck else (args.source or "new")
    try:
        res = asyncio.run(run_classify(st, source, args.limit, args.workers, args.recheck))
    except KeyboardInterrupt:
        print("\n[!] Остановлено: записанные профили сохранены, остальные не тронуты.")
        return
    print(
        f"[+] Обработано профилей: {res['processed']} "
        f"(с категорией {res['reviewed']}, с ошибкой {res['failed']})"
    )
    print(
        f"[*] Запросов к модели: {res['requests']}, токенов: "
        f"{res['prompt_tokens']} + {res['completion_tokens']}, "
        f"среднее время профиля: {res['avg_latency_ms'] or '-'} мс"
    )
    if res["auth_error"]:
        raise SystemExit(f"[!] Запуск остановлен: доступ к модели отклонён ({res['error']})")
    if res["error"]:
        raise SystemExit(f"[!] Запуск прерван ошибкой: {res['error']}")


if __name__ == "__main__":
    main()
