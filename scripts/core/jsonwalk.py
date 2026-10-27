"""Обход вложенных JSON-структур Threads."""


def walk(obj):
    """Обходит вложенные dict/list, отдаёт все словари."""
    stack = [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            yield x
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)


def walk_ordered(obj):
    """Как walk, но в порядке документа (сверху вниз, слева направо): по нему
    видно, в каком порядке сервер отдал ответы."""
    stack = [obj]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            yield x
            stack.extend(reversed(list(x.values())))
        elif isinstance(x, list):
            stack.extend(reversed(x))
