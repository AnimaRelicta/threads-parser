"""Константы пакета ig: схема БД, каталог миграций, адреса Instagram."""

from pathlib import Path

from config import IG_APP_ID

IG_SCHEMA = "instagram"
IG_MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"
IG_LOCK_ID = 7_410_002  # свой advisory-lock: миграции threads и instagram не мешают друг другу

# Эндпоинты веб-клиента. USER_INFO_URL есть в insta-parser; PROFILE_INFO_URL (профиль по
# нику) там не использовался: ПРОВЕРИТЬ НА ЖИВОЙ СЕССИИ (формат ответа, коды при лимите).
PROFILE_INFO_URL = "/api/v1/users/web_profile_info/?username={username}"
USER_INFO_URL = "/api/v1/users/{uid}/info/"

HEADER_WAIT_MS = 10_000  # сколько ждать отрисовку шапки профиля (header section)
PROFILE_SETTLE_MS = 1500  # пауза после открытия профиля перед чтением данных
BLOCKED_RESOURCES = frozenset({"image", "media", "font"})  # для вкладок, которым картинки не нужны

# fetch из контекста страницы: сессия аккаунта, заголовки веб-клиента Instagram
JS_FETCH = (
    """
async (url) => {
  const csrf = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
  const r = await fetch(url, {
    credentials: 'include',
    headers: {
      'X-IG-App-ID': '__APP_ID__',
      'X-CSRFToken': csrf,
      'X-Requested-With': 'XMLHttpRequest'
    }
  });
  if (!r.ok) return {error: r.status};
  try { return await r.json(); } catch (e) { return {error: 'bad_json'}; }
}
"""
).replace("__APP_ID__", IG_APP_ID)

# Лента профиля (веб-клиент): новые посты первыми, постранично по next_max_id.
# ПРОВЕРИТЬ НА ЖИВОЙ СЕССИИ: имена полей ответа (см. ig/feed.py) и допустимый count.
FEED_URL = "/api/v1/feed/user/{username}/username/?count=12"

# Пост, комментарии, ответы. ПРОВЕРИТЬ НА ЖИВОЙ СЕССИИ (ТЗ п. 8.3: эндпоинт ответов подтвердить).
MEDIA_INFO_URL = "/api/v1/media/{media_id}/info/"
COMMENTS_URL = "/api/v1/media/{media_id}/comments/?can_support_threading=true&permalink_enabled=false"
CHILD_COMMENTS_URL = "/api/v1/media/{media_id}/comments/{comment_id}/child_comments/?"
