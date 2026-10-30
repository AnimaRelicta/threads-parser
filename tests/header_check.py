"""Проверка шапки профиля без сети: разбор + чтение DOM макета (по записи кликов с Threads).

Запуск: uv run python tests/header_check.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from playwright.async_api import async_playwright  # noqa: E402

from core.header import classify_link, clean_url, parse_views  # noqa: E402
from scrape.header import read_header  # noqa: E402

# 1. Чистый разбор
assert parse_views("38 621 недавних просмотров") == 38621
assert parse_views("38 621 недавних просмотров") == 38621
assert parse_views("1,2 тыс. недавних просмотров") == 1200
assert parse_views("2 млн недавних просмотров") == 2_000_000
assert parse_views("12K recent views") == 12000
assert parse_views("97 подписчиков") is None
TG = "https://t.me/by_kvartiraminsk?utm_source=threads&utm_medium=social&fbclid=Zg&x=1"
assert clean_url(TG) == "https://t.me/by_kvartiraminsk?x=1", clean_url(TG)
assert clean_url("https://l.threads.com/?u=https%3A%2F%2Fwa.me%2F7708%3Futm_source%3Dt") == (
    "https://wa.me/7708"
)
assert classify_link("https://wa.me/77085826725") == {"kind": "whatsapp", "handle": "77085826725"}
assert classify_link("https://t.me/@abc")["handle"] == "abc"
assert classify_link("https://www.instagram.com/by_kvartiraminsk/") == {
    "kind": "instagram",
    "handle": "by_kvartiraminsk",
}

# 2. Макет страницы: шапка (как в записи) + пост с собственной темой и ссылкой ниже
HTML = """<html><body style="margin:0">
<div style="height:260px"></div>
<span><div><a role="link" target="_blank" href="http://wa.me/77085826725?utm_source=threads&amp;utm_content=link_in_bio&amp;fbclid=Zg"><span><span>wa.me/77085826725</span></span></a></div></span><br>
<span><div><a role="link" target="_blank" href="https://t.me/by_kvartiraminsk?utm_source=threads&amp;fbclid=Zg"><span><span>t.me/by_kvartiraminsk</span></span></a></div></span><br>
<a role="link" href="/search?q=%D0%BC%D0%B8%D0%BD%D1%81%D0%BA&amp;serp_type=tags&amp;tag_id=111"><div><span><span>Минск</span></span></div></a>
<a role="link" href="/search?q=%D0%BA%D0%B2&amp;serp_type=tags&amp;tag_id=222"><div><span><span>квартира в минске</span></span></div></a>
<a role="link" target="_blank" href="https://www.instagram.com/by_kvartiraminsk/"><div><svg viewBox="0 0 24 24"><title>Instagram</title><path d="M1 1"/></svg></div></a>
<div>97 подписчиков <div role="button"><span>38&nbsp;621 недавних просмотров</span></div></div>
<a href="/@by_kvartiraminsk">профиль</a>
<div style="height:400px"></div>
<div id="post"><a href="/@u/post/ABC123">12 ч</a>
<a role="link" href="/search?q=x&amp;serp_type=tags&amp;tag_id=999"><span>Тема поста</span></a>
<a role="link" target="_blank" href="https://example.com/post-link"><span>ссылка в посте</span></a></div>
<a role="link" href="https://www.facebook.com/privacy">подвал</a>
</body></html>"""


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(HTML)
        h = await read_header(page)
        print(h)
        assert h.recent_views == 38621, h.recent_views
        assert [t["text"] for t in h.topics] == ["Минск", "квартира в минске"], h.topics
        assert h.topics[0]["tag_id"] == "111" and h.topics[0]["query"] == "минск"
        urls = {x["url"]: x for x in h.links}
        assert set(urls) == {
            "http://wa.me/77085826725",
            "https://t.me/by_kvartiraminsk",
            "https://www.instagram.com/by_kvartiraminsk/",
        }, urls
        assert urls["http://wa.me/77085826725"]["handle"] == "77085826725"
        assert h.instagram["handle"] == "by_kvartiraminsk"
        # профиль без шапки -> None
        await page.set_content("<html><body><p>пусто</p></body></html>")
        assert await read_header(page) is None
        await browser.close()
    print("OK")


asyncio.run(main())
