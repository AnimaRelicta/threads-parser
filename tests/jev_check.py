"""Проверка классификации через Jev без БД и внешней сети (classify/jev*.py).

Запуск: uv run python tests/jev_check.py
Клиент проверяется на локальном фиктивном сервере: повторы 429, ошибка 401, версия модели.
"""

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from classify.jev import (  # noqa: E402
    Gate,
    build_questions,
    build_state,
    decide,
    pick_bio,
    topics_list,
)
from classify.jev_client import JevClient, JevError, JevFatalError, version_ok  # noqa: E402

# --- версии: 1.14 и новее ---
assert (
    version_ok("typesafe/jev-1.14", "1.14")
    and version_ok("jev-1.14.0", "1.14")
    and version_ok("jev-1.14.1", "1.14")
)
assert version_ok("jev-1.15.0", "1.14") and version_ok("jev-2.0.0", "1.14")
assert not version_ok("typesafe/jev-1.13", "1.14") and not version_ok("jev-latest", "1.14")
assert not version_ok("", "1.14")

# --- контекст профиля ---
OG = "353 подписчика • 169 веток • Магазин ковров. Посмотреть последние ветки"
assert pick_bio("  Ковры\nопт ", OG) == ("Ковры опт", "bio")
assert pick_bio(None, OG) == ("Магазин ковров", "og_description")
assert pick_bio("", "") == (None, None)
assert topics_list([{"text": "Ковры"}, {"text": "Ковры"}, {"text": "Дом"}]) == ["Ковры", "Дом"]
assert topics_list(None) == [] and topics_list('[{"text": "Авто"}]') == ["Авто"]

row = {"about_name": "Ковры KZ", "bio": None, "og_description": OG, "topics": [{"text": "Дом"}]}
state, info = build_state(row)
assert state == ("Имя: Ковры KZ\nОписание: Магазин ковров\nТемы: Дом\nМестоположение: Казахстан"), (
    state
)
assert info == {"name": True, "bio_src": "og_description", "topics": 1}
empty = {
    "about_name": None,
    "bio": "",
    "og_description": "5 подписчиков • 1 веток • ",
    "topics": [],
}
assert build_state(empty)[0] is None  # одна страна не данные

# --- вопросы ---
CATS = [("vitrina", "Витрина", "магазин"), ("news", "Новости", None), ("other", "Прочее", "всё")]
q = build_questions(CATS, True)
assert q["category"]["type"] == "choice" and set(q["category"]["criteria"]) == {
    "vitrina",
    "news",
    "other",
}
assert q["fits_specific"]["type"] == "noul" and "fits_specific" not in build_questions(CATS, False)

# --- защита: класс с наибольшей вероятностью, иначе «Прочее» ---
CODES = {"vitrina", "news", "other"}
G = Gate(0.6, 0.5, True, 0.5)


def ans(probs, conf=0.9, noul=0.9):
    return {
        "category": {
            "choice": max(probs, key=probs.get),
            "probabilities": probs,
            "confidence": conf,
        },
        "fits_specific": {"noul": noul},
    }


d = decide(ans({"vitrina": 0.8, "news": 0.1, "other": 0.1}), CODES, G)
assert (d.category, d.reason) == ("vitrina", None)
d = decide(ans({"vitrina": 0.2, "news": 0.1, "other": 0.7}), CODES, G)
assert (d.category, d.reason) == ("other", "jev_other")
d = decide(ans({"vitrina": 0.5, "news": 0.3, "other": 0.2}), CODES, G)
assert (d.category, d.reason, d.top) == ("other", "low_prob", "vitrina")
d = decide(ans({"vitrina": 0.8, "news": 0.1, "other": 0.1}, conf=0.3), CODES, G)
assert (d.category, d.reason) == ("other", "low_confidence")
d = decide(ans({"vitrina": 0.8, "news": 0.1, "other": 0.1}, noul=0.2), CODES, G)
assert (d.category, d.reason) == ("other", "noul")
d = decide(ans({"vitrina": 0.8, "news": 0.1, "other": 0.1}, noul=0.2), CODES, Gate(noul_gate=False))
assert (d.category, d.reason) == ("vitrina", None)
assert decide({}, CODES, G).category == "other"  # пустой ответ
d = decide(ans({"zzz": 0.9, "vitrina": 0.7, "other": 0.3}), CODES, G)  # лишний ключ игнорируется
assert d.category == "vitrina"

# --- клиент на фиктивном сервере ---
CALLS: list[int] = []
MODE = {"v": "retry"}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert body["state"] and "questions" in body and body["model"]
        CALLS.append(1)
        if MODE["v"] == "auth":
            self.send_response(401)
        elif MODE["v"] == "retry" and len(CALLS) == 1:
            self.send_response(429)
            self.send_header("Retry-After", "0")
        else:
            model = "typesafe/jev-1.13" if MODE["v"] == "old" else "typesafe/jev-1.14"
            out = {"model": model, "answers": {"category": {}}, "usage": {"input_tokens": 7}}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(out).encode())
            return
        self.end_headers()
        self.wfile.write(b"{}")


srv = HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_port}/v1/systemone"


def client():
    return JevClient(url, "k", "typesafe/jev-1.14", "1.14", timeout=5, max_retries=2)


r = asyncio.run(client().ask("текст", q))
assert r.model == "typesafe/jev-1.14" and r.requests == 2 and r.input_tokens == 7  # 429 -> повтор
for mode, exc in (("auth", JevFatalError), ("old", JevFatalError)):
    MODE["v"] = mode
    try:
        asyncio.run(client().ask("текст", q))
    except exc:
        pass
    else:
        raise AssertionError(mode)
srv.shutdown()
try:
    asyncio.run(client().ask("текст", q))  # сервер закрыт: сеть -> JevError после повторов
except JevError as e:
    assert not isinstance(e, JevFatalError)
else:
    raise AssertionError("ожидалась ошибка сети")

# --- посты: SQL-условия и вопросы (без БД) ---
try:
    import asyncpg  # noqa: F401
except ImportError:  # проверка не требует БД: подставляем заглушку, если драйвера нет
    from unittest.mock import MagicMock

    sys.modules["asyncpg"] = MagicMock()
from classify.jev_posts_runner import FROM_WHERE, INSTRUCTIONS, post_state  # noqa: E402

assert "lower(pr.username) = lower(po.author_username)" in FROM_WHERE
assert "'казахстан'" in FROM_WHERE 
assert len(post_state("я" * 5000)) == 3000
assert build_questions(CATS, True, INSTRUCTIONS)["category"]["instructions"] == INSTRUCTIONS

print("jev_check: OK")
