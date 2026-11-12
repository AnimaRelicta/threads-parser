#!/usr/bin/env python3
"""Точка входа: выгрузка данных из Postgres в xlsx (по листу на запрос).

Запросы: базовые (`--list`), свои из файла data/export_queries.sql (EXPORT_QUERIES_FILE),
разовые через --sql и --file. Все идут в read-only транзакции: записать или удалить
запросом нельзя. В тексте запроса доступны :since и :limit (флаги --since и --limit).

Примеры (из корня проекта):
    uv run scripts/export.py --list
    uv run scripts/export.py profiles                               # data/export/..._время.xlsx
    uv run scripts/export.py profiles threads --since 7d            # два листа в одном файле
    uv run scripts/export.py top-authors --since 1m --limit 500 --out data/export/top.xlsx
    uv run scripts/export.py my-query                               # из data/export_queries.sql
    uv run scripts/export.py --sql "SELECT username FROM profiles WHERE is_target" --sheet targets
    uv run scripts/export.py --file queries/one.sql --out report.xlsx
"""

from config import load_settings
from exporter.queries import BASE_QUERIES
from exporter.runner import ExportSpec, export_main


def main() -> None:
    st = load_settings()
    spec = ExportSpec("export.py", "threads", BASE_QUERIES, st.export_queries_file, st.export_dir)
    export_main(spec, st)


if __name__ == "__main__":
    main()
