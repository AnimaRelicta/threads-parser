"""Перехват JSON-ответов Threads вкладкой."""

import json
import re

from browser.net import Net
from scrape.consts import REPLIES_API
from scrape.page import page_infos

# ---------------------------------------------------------------------------
# Сетка n x m: n аккаунтов, у каждого ОДИН браузер и m воркеров. Воркер это пара
# вкладок (лента профиля + вкладка постов) со своим профилем из общей очереди.
# Всё работает в одном процессе на asyncio: вкладки реально параллельны, а сетевое
# состояние (Net) у каждого воркера своё. Внутри профиля посты идут последовательно:
# от порядка зависят закреплённые, серия старых постов и дозагрузка комментариев.
# ---------------------------------------------------------------------------


def make_handler(target: list, net: Net):
    """Складывает JSON-ответы сервера вкладки в target (и ведёт сетевой журнал воркера)."""

    async def on_response(resp):
        try:
            if resp.request.resource_type not in ("xhr", "fetch"):
                return
            if not any(h in resp.url for h in ("threads.com", "threads.net")):
                return
            ct = resp.headers.get("content-type", "")
            if "json" not in ct and "javascript" not in ct:
                return
            name = "?"
            try:
                m = re.search(r"fb_api_req_friendly_name=([^&]+)", resp.request.post_data or "")
                if m:
                    name = m.group(1)[:40]
            except Exception:
                pass
            try:
                data = await resp.json()
            except Exception:
                txt = await resp.text()
                txt = re.sub(r"^for\s*\(;;\);", "", txt.lstrip())
                data = json.loads(txt.split("\n")[0])
            target.append(data)
            net.calls.append((name, resp.status, True))
            if REPLIES_API in name:
                pis = page_infos([data])
                net.replies_pages.append(any(hn for hn, _ in pis) if pis else None)
        except Exception:
            net.json_fail += 1
            net.calls.append(("?", getattr(resp, "status", 0), False))

    return on_response
