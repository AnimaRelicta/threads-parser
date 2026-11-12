#!/usr/bin/env python3
"""Точка входа: свои запросы на изменение БД из файла (аналог export.py, но с записью).

Запросы лежат в data/db_tasks.sql (путь задаёт DB_TASKS_FILE), формат как у export.py:

    -- name: select-targets
    -- desc: что делает (видно в `sqlrun.py --list`)
    -- param: min_followers=100        (значение по умолчанию, меняется через --set)
    UPDATE profiles SET is_target = (followers_count >= :min_followers::int) WHERE ...;

По умолчанию запрос выполняется в транзакции и откатывается (пробный прогон): видно, сколько
строк изменится. Записать в БД: `--apply`. UPDATE и DELETE без WHERE отклоняются.

Примеры (из корня проекта):
    uv run scripts/sqlrun.py --list
    uv run scripts/sqlrun.py select-targets                          # пробный прогон
    uv run scripts/sqlrun.py select-targets --set min_followers=200 --apply
    uv run scripts/sqlrun.py targets-stats                           # SELECT: покажет строки
"""

from config import load_settings
from exporter.tasks import TaskSpec, bind, check_safe, coerce, declared_params, parse_sets, task_main

# имена, которые проверяет tests/sqlrun_check.py
__all__ = ["bind", "check_safe", "coerce", "declared_params", "parse_sets"]


def main() -> None:
    st = load_settings()
    task_main(TaskSpec("sqlrun.py", "threads", st.db_tasks_file), st)


if __name__ == "__main__":
    main()
