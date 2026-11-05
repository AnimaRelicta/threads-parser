"""Точка входа: классификация профилей Казахстана через Jev (TypeSafe System One).

    uv run scripts/cli.py jev --dry-run              # сколько профилей подходит, без запросов
    uv run scripts/cli.py jev --dry-run --show 5     # плюс 5 примеров контекста для модели
    uv run scripts/cli.py jev --limit 50             # пробный прогон на 50 профилях
    uv run scripts/cli.py jev --all                  # полный прогон
    uv run scripts/cli.py jev --all --recheck        # пересчитать и уже классифицированные

Берётся класс с наибольшей вероятностью, но слабый ответ (низкая вероятность, confidence или
Noul-проверка) превращается в «Прочее» (other), см. classify/jev.py и JEV_* в scripts/.env.
"""

import argparse
import asyncio

from classify.jev import Gate
from classify.jev_runner import run_jev
from config import ENV_FILE, load_settings


def build_parser(st) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="cli.py jev",
        description=f"Классификация профилей КЗ через Jev. Настройки: {ENV_FILE}",
    )
    ap.add_argument("--dry-run", action="store_true", help="только отбор и счётчики, без запросов")
    ap.add_argument("--show", type=int, default=3, help="примеров контекста в --dry-run (3)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--limit", type=int, help="сколько профилей обработать за запуск")
    g.add_argument("--all", action="store_true", help="обработать все подходящие профили")
    ap.add_argument("--workers", type=int, default=st.jev_workers, help="профилей одновременно")
    ap.add_argument(
        "--recheck", action="store_true", help="брать и профили, у которых категория уже есть"
    )
    ap.add_argument("--min-prob", type=float, default=st.jev_min_prob, help="порог вероятности")
    ap.add_argument(
        "--min-confidence", type=float, default=st.jev_min_confidence, help="порог confidence"
    )
    ap.add_argument("--noul-min", type=float, default=st.jev_noul_min, help="порог Noul-проверки")
    ap.add_argument(
        "--no-noul",
        action="store_true",
        default=not st.jev_noul_gate,
        help="не задавать Noul-проверку «подходит ли хоть один класс 1-6»",
    )
    return ap


def main() -> None:
    st = load_settings()
    args = build_parser(st).parse_args()
    if not args.dry_run:
        if not args.all and (args.limit is None or args.limit < 1):
            raise SystemExit("Нужен --limit N (N >= 1) или --all; для проверки есть --dry-run")
        if args.workers < 1:
            raise SystemExit("--workers должен быть не меньше 1")
        if not st.jev_api_key:
            raise SystemExit(f"Не задан OPENROUTER_API_KEY в {ENV_FILE}")
    gate = Gate(args.min_prob, args.min_confidence, not args.no_noul, args.noul_min)
    try:
        res = asyncio.run(
            run_jev(
                st,
                gate,
                None if args.all else args.limit,
                args.workers,
                args.recheck,
                args.dry_run,
                max(0, args.show),
            )
        )
    except KeyboardInterrupt:
        print("\n[!] Остановлено: записанные профили сохранены, остальные не тронуты.")
        return
    if res is None:  # dry-run
        return
    if not res.get("processed") and not res.get("failed"):
        print("[*] Обрабатывать нечего.")
        return
    cats = ", ".join(
        f"{k}: {v}" for k, v in sorted(res["by_category"].items(), key=lambda x: -x[1])
    )
    why = ", ".join(f"{k}: {v}" for k, v in sorted(res["by_reason"].items()))
    print(
        f"[+] Классифицировано: {res.get('processed', 0)}, ошибок (review_failed): "
        f"{res.get('failed', 0)}, осталось в очереди: {res['left']}"
    )
    print(f"[+] Итоговые категории: {cats or '-'}")
    print(f"[*] Ушло в «Прочее» по защите/ответу Jev: {why or '-'}")
    print(
        f"[*] Запросов: {res.get('requests', 0)}, токенов: {res.get('input_tokens', 0)} + "
        f"{res.get('output_tokens', 0)}, среднее время: {res['avg_latency_ms'] or '-'} мс"
    )
    if res["fatal"]:
        raise SystemExit(f"[!] Запуск остановлен: {res['error']}")
    if res["error"]:
        raise SystemExit(f"[!] Запуск прерван ошибкой: {res['error']}")


if __name__ == "__main__":
    main()
