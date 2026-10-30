"""Проверка связи с Instagram без сети и БД. Запуск: uv run python tests/instagram_check.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from core.header import classify_link  # noqa: E402
from core.instagram import find_in_bio, find_in_json, find_instagram_link  # noqa: E402

# JSON: берём только узел самого профиля, рекомендованный чужой профиль игнорируем
DOC = {
    "a": [
        {"username": "other_user", "instagram_username": "other_ig"},
        {"username": "by_kvartiraminsk", "ig_username": "by_kvartiraminsk"},
    ]
}
ig = find_in_json(DOC, owner="by_kvartiraminsk")
assert ig and ig.username == "by_kvartiraminsk" and ig.source == "threads_json", ig
assert find_in_json({"username": "other", "ig_username": "x"}, owner="me") is None
assert find_in_json({"ig_username": "x"}, owner="me") is None  # без username рядом не доверяем

# Био: ссылка на профиль подходит, на пост/рилс нет
assert find_in_bio("мой инст instagram.com/abc.def").username == "abc.def"
assert find_in_bio("смотри instagram.com/p/Cxyz123/") is None
assert find_in_bio("https://www.instagram.com/reel/Abc/") is None
assert classify_link("https://www.instagram.com/p/Cxyz/")["handle"] is None

# Цепочка: JSON -> био -> догадка (помечена guessed)
g = find_instagram_link([], None, "some_user")
assert (g.username, g.source, g.confidence) == ("some_user", "username_guess", "guessed")
assert find_instagram_link([], "ig: instagram.com/real_one", "some_user").username == "real_one"
print("OK")
