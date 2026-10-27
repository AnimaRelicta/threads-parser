"""Сетевое состояние одного воркера."""


class Net:
    """Сетевое состояние одного воркера (раньше это были глобали модуля).

    captured / grid_captured: JSON-ответы сервера для вкладки постов и ленты;
    calls: (метод API, статус, разобран ли JSON); replies_pages: по каждому
    запросу подгрузки ответов, есть ли следующая страница.
    """

    def __init__(self, debug: bool = False):
        self.captured: list = []
        self.grid_captured: list = []
        self.calls: list = []
        self.replies_pages: list = []
        self.json_fail = 0
        self.debug = debug  # подробный лог дозагрузки (включает --debug)
