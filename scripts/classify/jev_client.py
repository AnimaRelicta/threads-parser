"""HTTP-клиент Jev (TypeSafe System One): POST /v1/systemone, только стандартная библиотека.

Идёт через OpenRouter (https://openrouter.ai/api/v1/systemone, ключ OpenRouter, модель
«typesafe/jev-1.14»); формат тот же, что у родного API TypeSafe. Запрос: {"state": текст, "model": ..., "questions": {id: {type, instructions, criteria}}}.
Ответ: {"model": "jev-1.14.0", "answers": {id: {choice, probabilities, confidence | noul}},
"usage": {...}}. Повторы с нарастающей паузой при 429, 529, 5xx, таймауте и сетевых ошибках
(так советует документация TypeSafe). 401/403 и версия модели ниже JEV_MIN_VERSION дают
JevFatalError: запуск нужно остановить целиком. Остальные ошибки запроса дают JevError.
"""

import asyncio
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
MAX_PAUSE_SEC = 30.0
_MODEL_RE = re.compile(r"jev-(\d+)\.(\d+)(?:\.(\d+))?", re.I)


class JevError(Exception):
    """Запрос не удался (после повторов): профиль получает review_failed, цикл идёт дальше."""

    def __init__(self, message: str, requests: int = 0):
        super().__init__(message)
        self.requests = requests  # сколько HTTP-попыток потрачено


class JevFatalError(JevError):
    """401/403 или слишком старая модель: продолжать бессмысленно."""


@dataclass
class JevResponse:
    model: str  # версия, которая реально ответила: «jev-1.14.0»
    answers: dict
    requests: int
    input_tokens: int
    output_tokens: int
    latency_ms: int


def parse_version(model: str) -> tuple[int, ...] | None:
    """«jev-1.14.0» -> (1, 14, 0); «jev-1.14» -> (1, 14); не версия (jev-latest) -> None."""
    m = _MODEL_RE.search(model or "")
    return tuple(int(x) for x in m.groups() if x is not None) if m else None


def version_ok(model: str, min_version: str) -> bool:
    """Версия модели не ниже минимальной («1.14»). Нераспознанная версия = не годится."""
    have = parse_version(f"jev-{min_version.removeprefix('jev-')}")
    got = parse_version(model)
    return bool(have and got and got >= have)


class JevClient:
    def __init__(
        self,
        url: str,
        api_key: str,
        model: str,
        min_version: str,
        timeout: int,
        max_retries: int,
        proxy: str | None = None,
    ):
        self.url, self.api_key, self.model = url, api_key, model
        self.min_version, self.timeout, self.max_retries = min_version, timeout, max_retries
        self.proxy = proxy  # socks5://127.0.0.1:12334; urllib SOCKS не умеет, идём через httpx

    def _post(self, payload: dict) -> tuple[int, dict | None, str, float | None]:
        """Один HTTP-запрос: (статус, json, текст ошибки, Retry-After). Статус 0 = сеть/таймаут."""
        return self._post_httpx(payload) if self.proxy else self._post_urllib(payload)

    def _post_httpx(self, payload: dict) -> tuple[int, dict | None, str, float | None]:
        try:
            import httpx
        except ImportError as e:
            raise JevFatalError(
                "для JEV_PROXY нужен пакет httpx[socks]: uv add 'httpx[socks]'"
            ) from e
        try:
            with httpx.Client(proxy=self.proxy, timeout=self.timeout) as c:
                resp = c.post(
                    self.url,
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except (httpx.HTTPError, OSError, ImportError) as e:
            return 0, None, f"{type(e).__name__}: {e}", None
        if resp.status_code == 200:
            try:
                return 200, resp.json(), "", None
            except ValueError as e:
                return 200, None, f"ответ не JSON: {e}", None
        try:
            retry_after = float(resp.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = None
        return resp.status_code, None, resp.text[:300], retry_after

    def _post_urllib(self, payload: dict) -> tuple[int, dict | None, str, float | None]:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8")), "", None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            try:
                retry_after = float(e.headers.get("Retry-After", ""))
            except ValueError:
                retry_after = None
            return e.code, None, body, retry_after
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return 0, None, f"{type(e).__name__}: {e}", None
        except json.JSONDecodeError as e:
            return 200, None, f"ответ не JSON: {e}", None

    async def ask(self, state: str, questions: dict) -> JevResponse:
        payload = {"state": state, "model": self.model, "questions": questions}
        started = time.monotonic()
        attempts = 0
        last_error = ""
        while attempts <= self.max_retries:
            attempts += 1
            status, data, error, retry_after = await asyncio.to_thread(self._post, payload)
            if status == 200 and data is not None:
                return self._parse(data, attempts, started)
            last_error = f"HTTP {status}: {error}" if status else error
            if status in (401, 403):
                raise JevFatalError(last_error, attempts)
            if status and status not in RETRY_STATUSES:
                raise JevError(last_error, attempts)
            if attempts <= self.max_retries:
                pause = retry_after if retry_after is not None else 2.0 * attempts
                await asyncio.sleep(min(pause, MAX_PAUSE_SEC))
        raise JevError(f"после {attempts} попыток: {last_error}", attempts)

    def _parse(self, data: dict, attempts: int, started: float) -> JevResponse:
        answers = data.get("answers")
        if answers is None and isinstance(data.get("category"), dict):
            answers = data  # Decisions API OpenRouter: ответы лежат на верхнем уровне
        if not isinstance(answers, dict):
            raise JevError(f"неожиданный формат ответа: {str(data)[:200]}", attempts)
        model = str(data.get("model") or "")
        if not version_ok(model, self.min_version):
            raise JevFatalError(
                f"ответила модель «{model or '?'}», нужна не ниже jev-{self.min_version}: "
                "поправьте JEV_MODEL / JEV_MIN_VERSION",
                attempts,
            )
        usage = data.get("usage") or {}
        return JevResponse(
            model=model,
            answers=answers,
            requests=attempts,
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
