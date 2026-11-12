"""Хранилище медиа: пути, безопасные имена, атомарная запись, свободное место."""

import re
import shutil
from datetime import datetime
from pathlib import Path

USERNAME_RE = re.compile(r"^[a-z0-9._]{1,30}$")
_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "video/mp4": "mp4"}
DOWNLOAD_TIMEOUT_MS = 120_000


def safe_nick(nick: str) -> str:
    """Имя каталога профиля: ник в нижнем регистре; всё прочее (в т. ч. '..') отвергается."""
    n = (nick or "").lower()
    if not USERNAME_RE.match(n) or n.startswith(".") or ".." in n:
        raise ValueError(f"небезопасный ник для каталога: {nick!r}")
    return n


def ext_for(url: str, content_type: str | None, kind: str) -> str:
    """Расширение: по Content-Type, иначе по URL, иначе jpg/mp4 по виду."""
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _EXT:
        return _EXT[ct]
    m = re.search(r"\.(jpg|jpeg|png|webp|mp4)(?:\?|$)", url.lower())
    if m:
        return "jpg" if m.group(1) == "jpeg" else m.group(1)
    return "mp4" if kind == "video" else "jpg"


def rel_path(nick: str, taken_at: datetime | None, code: str, position: int, ext: str) -> str:
    day = taken_at.strftime("%Y%m%d") if taken_at else "00000000"
    return f"{safe_nick(nick)}/{day}_{code}_{position}.{ext}"


def avatar_rel(nick: str, ext: str = "jpg") -> str:
    return f"{safe_nick(nick)}/avatar.{ext}"


def free_gb(path: Path) -> float:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free / 1024**3


def ensure_space(path: Path, min_gb: float) -> None:
    """SystemExit, если в хранилище меньше min_gb свободного места."""
    if min_gb and free_gb(path) < min_gb:
        raise SystemExit(
            f"В хранилище {path} меньше {min_gb:g} ГБ свободного места (IG_MIN_FREE_GB)"
        )


async def download(page, url: str, dest: Path) -> tuple[int, bool]:
    """Скачивает url в dest -> (размер, скачано_сейчас). Запись *.part -> rename.

    Существующий непустой файл не качается заново. Ошибка загрузки — исключение."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest.stat().st_size, False
    resp = await page.context.request.get(url, timeout=DOWNLOAD_TIMEOUT_MS)
    if not resp.ok:
        raise OSError(f"HTTP {resp.status}")
    body = await resp.body()
    if not body:
        raise OSError("пустой ответ")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    part.write_bytes(body)
    part.replace(dest)
    return len(body), True
