"""Журнал воркеров: метка в строках лога и счётчики текущего профиля."""

import builtins
import contextvars

# Метка воркера для строк лога: у каждой задачи asyncio своя (contextvars).
TAG: contextvars.ContextVar[str] = contextvars.ContextVar("tag", default="")


# Счётчики текущего профиля (у каждой задачи asyncio свои): скачанные файлы, байты,
# открытые страницы. Воркер кладёт сюда словарь перед run_profile и забирает после.
PSTAT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("pstat", default=None)


def pstat(**kw) -> None:
    """Прибавляет приращения к счётчикам текущего профиля (вне профиля ничего не делает)."""
    s = PSTAT.get()
    if s is not None:
        for k, v in kw.items():
            s[k] = s.get(k, 0) + v


def log(*a, **k) -> None:
    """print с меткой «[аккаунт/wN]» текущего воркера и немедленным сбросом буфера."""
    k.setdefault("flush", True)
    tag = TAG.get()
    if tag:
        builtins.print(f"[{tag}]", *a, **k)
    else:
        builtins.print(*a, **k)
