"""Подкоманда `cli.py ig sql`: свои запросы на изменение схемы instagram.

    uv run scripts/cli.py ig sql --list
    uv run scripts/cli.py ig sql targets-stats
    uv run scripts/cli.py ig sql set-target-by-followers --set min_followers=500          # пробный прогон
    uv run scripts/cli.py ig sql set-target-by-followers --set min_followers=500 --apply  # записать
    uv run scripts/cli.py ig sql drop-profile --set username=nick --apply

Файл запросов: data/ig_db_tasks.sql (IG_DB_TASKS_FILE). Без --apply запрос откатывается.
Движок общий с sqlrun.py (exporter/tasks.py).
"""

from config import load_ig_settings
from exporter.tasks import TaskSpec, task_main
from ig.consts import IG_SCHEMA


def main() -> None:
    st = load_ig_settings()
    task_main(TaskSpec("cli.py ig sql", IG_SCHEMA, st.db_tasks_file), st)


if __name__ == "__main__":
    main()
