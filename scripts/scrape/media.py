"""Скачивание медиа через сессию аккаунта."""

import asyncio
import random
from pathlib import Path
from urllib.parse import urlparse

from browser.account import Account
from config import TH
from core.errors import HttpStatusError
from core.logs import log, pstat
from scrape.extract import media_urls


async def fetch_bytes(acc: Account, url: str) -> bytes:
    """Скачивает файл. Сначала быстрым API-запросом, при сбое соединения
    переключается на загрузку через вкладку настоящего браузера."""
    if acc.use_api:
        try:
            r = await acc.ctx.request.get(url, timeout=60000, headers={"Referer": TH + "/"})
            if not r.ok:
                raise HttpStatusError(f"HTTP {r.status}")
            return await r.body()
        except HttpStatusError:
            raise  # протухшая ссылка и т.п., режим загрузки не меняем
        except Exception as e:
            first_line = str(e).splitlines()[0][:120]
            log(f"    [!] Прямая загрузка не удалась ({first_line}), дальше качаю через браузер")
            acc.use_api = False

    last: Exception | None = None
    for attempt in range(3):
        pg = await acc.ctx.new_page()
        try:
            resp = await pg.goto(url, wait_until="load", timeout=60000)
            if resp is None:
                raise RuntimeError("нет ответа")
            if not resp.ok:
                raise HttpStatusError(f"HTTP {resp.status}")
            return await resp.body()
        except HttpStatusError:
            raise
        except Exception as e:
            last = e
            await asyncio.sleep(1.5 * (attempt + 1))
        finally:
            try:
                await pg.close()
            except Exception:
                pass
    raise RuntimeError(str(last).splitlines()[0][:150] if last else "ошибка")


async def download(acc: Account, url: str, base: Path, kind: str) -> str | None:
    try:
        body = await fetch_bytes(acc, url)
        ext = Path(urlparse(url).path).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".heic"}:
            ext = ".mp4" if kind == "video" else ".jpg"
        path = base.parent / (base.name + ext)
        path.write_bytes(body)
        pstat(files=1, bytes=len(body), **{"videos" if kind == "video" else "images": 1})
        return path.name
    except Exception as e:
        pstat(failed=1)
        log(f"    [!] Медиа не скачано: {str(e).splitlines()[0][:150]}")
        return None


async def save_comment_media(
    acc: Account, d: dict, pk: str, media_dir: Path, args
) -> tuple[list, int]:
    """Скачивает медиа комментария. Возвращает (файлы, число неудач)."""
    items = media_urls(d)
    if not args.with_videos:
        items = [(k, u) for k, u in items if k == "image"]
    if not items:
        return [], 0
    base = media_dir / "comments"
    base.mkdir(parents=True, exist_ok=True)
    files = []
    for i, (kind, url) in enumerate(items, start=1):
        name = await download(acc, url, base / f"{pk}_{i}", kind)
        if name:
            files.append({"type": kind, "file": f"comments/{name}"})
        await asyncio.sleep(random.uniform(0.2, 0.5))
    return files, len(items) - len(files)
