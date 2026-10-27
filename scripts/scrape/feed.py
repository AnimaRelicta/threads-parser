"""Лента профиля: ссылки на посты с заглядыванием вперёд."""

import random
from collections import deque

from playwright.async_api import Error as PlaywrightError

from core.consts import POST_RE


class Lookahead:
    """Асинхронный итератор с подглядыванием вперёд (без потери элементов)."""

    def __init__(self, agen):
        self._it = agen.__aiter__()
        self._buf: deque = deque()

    async def peek(self, n: int) -> list:
        while len(self._buf) < n:
            try:
                self._buf.append(await self._it.__anext__())
            except StopAsyncIteration:
                break
        return list(self._buf)[:n]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._buf:
            return self._buf.popleft()
        return await self._it.__anext__()


async def iter_post_links(page, with_videos: bool, username: str, max_idle: int = 4):
    """Генератор (shortcode, kind) собственных постов в порядке ленты.

    Лента подгружается прокруткой по мере надобности. Ссылки на посты других
    авторов (родительские посты, рекомендации) отбрасываются.
    """
    seen: set[str] = set()
    idle = 0
    while True:
        try:
            await page.bring_to_front()  # фоновая вкладка может не подгружать ленту
        except PlaywrightError:
            pass

        hrefs = await page.eval_on_selector_all(
            'a[href*="/post/"]', "els => els.map(e => e.getAttribute('href'))"
        )
        before = len(seen)
        fresh = []
        for h in hrefs:
            m = POST_RE.search(h or "")
            if not m:
                continue
            owner, code = m.groups()
            if owner.lower() != username.lower() or code in seen:
                continue
            seen.add(code)
            fresh.append((code, "post"))
        for item in fresh:
            yield item

        idle = idle + 1 if len(seen) == before else 0
        if idle >= max_idle:
            return

        await page.evaluate("window.scrollBy(0, document.documentElement.clientHeight)")
        await page.wait_for_timeout(random.randint(1500, 3000))
