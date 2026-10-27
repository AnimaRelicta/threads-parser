"""Исключения парсера."""


class RateLimited(Exception):
    pass


class AuthProblem(Exception):
    pass


class SkipPost(Exception):
    """Пост не подходит (видео при режиме «только фото»; в args[0] дата)."""


class OldPost(Exception):
    """Пост старше границы --since (в args[0] дата поста)."""


class ThreadAborted(Exception):
    """Обход комментариев прерван (429/авторизация), но частичный результат
    есть: post сохраняется, cause определяет дальнейшее поведение."""

    def __init__(self, post: dict, cause: Exception):
        super().__init__(str(cause))
        self.post = post
        self.cause = cause


class HttpStatusError(Exception):
    pass
