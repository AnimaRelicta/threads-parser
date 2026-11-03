"""Запись данных профиля одним вызовом: страница, «Об этом профиле», шапка, Instagram, геосигналы.

Раньше каждый блок писался отдельной функцией и отдельным соединением (`save_meta`,
`save_about`, `save_header`, `save_instagram`, `save_geo`), а геосигналы заново читали
только что записанную строку. Теперь профиль обновляется одним UPDATE ... RETURNING, связь
с Instagram пишется в той же транзакции, а геосигналы считаются по строке из RETURNING.
"""

import asyncpg

from core.geo import DEFAULT_TEXT_SOURCES, Resolved
from core.instagram import DETECTOR_VERSION as IG_VERSION
from core.logs import log
from db.geo import PROFILE_COLS, save_geo

# Блоки about и header пишутся, только если переданы (флаги $9 и $14); иначе поля остаются.
PROFILE_SQL = f"""
UPDATE profiles SET
    url = coalesce($2, url), title = $3, og_title = $4, og_description = $5, og_image = $6,
    followers_count = coalesce($7, followers_count),
    bio = coalesce($8, bio),
    about_name = CASE WHEN $9 THEN $10 ELSE about_name END,
    about_joined = CASE WHEN $9 THEN $11 ELSE about_joined END,
    about_location = CASE WHEN $9 THEN $12 ELSE about_location END,
    about_raw = CASE WHEN $9 THEN $13::jsonb ELSE about_raw END,
    about_fetched_at = CASE WHEN $9 THEN now() ELSE about_fetched_at END,
    recent_views = CASE WHEN $14 THEN coalesce($15, recent_views) ELSE recent_views END,
    topics = CASE WHEN $14 THEN $16::jsonb ELSE topics END,
    links = CASE WHEN $14 THEN $17::jsonb ELSE links END,
    header_fetched_at = CASE WHEN $14 THEN now() ELSE header_fetched_at END,
    threads_count = coalesce($18, threads_count)
WHERE id = $1
RETURNING {PROFILE_COLS}
"""

# Приоритет источников: ссылка в шапке > JSON страницы > догадка по нику. Слабый источник
# не затирает более сильный (догадка не перезаписывает подтверждённую связь).
_IG_RANK = "CASE {} WHEN 'header_link' THEN 3 WHEN 'threads_json' THEN 2 ELSE 1 END"
INSTAGRAM_SQL = f"""
INSERT INTO instagram_links
    (profile_id, instagram_username, source, confidence, matched_raw, detector_version)
VALUES ($1, $2, $3, $4, $5, $6)
ON CONFLICT (profile_id) DO UPDATE SET
    instagram_username = EXCLUDED.instagram_username, source = EXCLUDED.source,
    confidence = EXCLUDED.confidence, matched_raw = EXCLUDED.matched_raw,
    detector_version = EXCLUDED.detector_version, updated_at = now()
WHERE {_IG_RANK.format("EXCLUDED.source")} >= {_IG_RANK.format("instagram_links.source")}
"""


async def save_profile_data(
    conn: asyncpg.Connection,
    profile_id: int,
    meta: dict,
    about=None,
    header=None,
    link=None,
    text_sources: tuple[str, ...] = DEFAULT_TEXT_SOURCES,
) -> Resolved:
    """Пишет всё, что собрано при заходе в профиль; None-блок оставляет прежние значения.

    meta: словарь страницы (url, title, og_*, followers, bio); about: core.about.About;
    header: core.header.Header; link: core.instagram.IgLink. Данные и связь с Instagram
    пишутся в одной транзакции. Геосигналы (location_mentions, about_country) — следом и
    отдельно: их сбой не откатывает данные профиля. Возвращает итог гео (пустой при сбое).
    """
    async with conn.transaction():
        row = await conn.fetchrow(
            PROFILE_SQL,
            profile_id,
            meta.get("url"),
            meta.get("title"),
            meta.get("og_title"),
            meta.get("og_description"),
            meta.get("og_image"),
            meta.get("followers"),
            meta.get("bio"),
            about is not None,
            about.name if about else None,
            about.joined if about else None,
            about.location if about else None,
            about.raw if about else None,
            header is not None,
            header.recent_views if header else None,
            header.topics if header else None,
            header.links if header else None,
            meta.get("threads"),
        )
        if row is None:
            return Resolved()
        if link is not None:
            await conn.execute(
                INSTAGRAM_SQL,
                profile_id,
                link.username,
                link.source,
                link.confidence,
                link.matched_raw,
                IG_VERSION,
            )
    try:
        return await save_geo(conn, row, text_sources)
    except Exception as e:  # гео не должно ронять обход профиля
        log(f"    [!] гео профиля не записано: {e!r}")
        return Resolved()
