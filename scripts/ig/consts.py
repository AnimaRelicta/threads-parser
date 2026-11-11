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
