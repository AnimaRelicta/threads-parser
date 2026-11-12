"""Подкоманда `cli.py ig export`: выгрузка схемы instagram в xlsx (по листу на запрос).

    uv run scripts/cli.py ig export --list
    uv run scripts/cli.py ig export ig-profiles ig-sources --out data/export/
    uv run scripts/cli.py ig export ig-posts --since 1m --limit 5000
    uv run scripts/cli.py ig export top-profiles          # свой запрос из data/ig_export_queries.sql

Движок общий с export.py (exporter/runner.py); запросы идут в read-only транзакции.
"""

from config import load_ig_settings
from exporter.runner import ExportSpec, export_main
from ig.consts import IG_SCHEMA
from ig.export_queries import BASE_QUERIES


def main() -> None:
    st = load_ig_settings()
    spec = ExportSpec(
        "cli.py ig export", IG_SCHEMA, BASE_QUERIES, st.export_queries_file, st.export_dir
    )
    export_main(spec, st)


if __name__ == "__main__":
    main()
