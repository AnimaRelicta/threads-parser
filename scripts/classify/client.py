"""HTTP-клиент к proxy API модели (формат OpenAI chat/completions), только стандартная библиотека.

Запрос идёт в потоке (asyncio.to_thread), чтобы не блокировать цикл. Повторы с нарастающей
паузой при 429, 5xx, таймауте и сетевых ошибках. 401/403 дают LLMAuthError: запуск нужно
остановить целиком. Остальные ошибки одного запроса, после повторов, дают LLMError.
"""

import asyncio
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_PAUSE_SEC = 30.0


class LLMError(Exception):
    """Запрос не удался (после повторов); профиль получает review_failed, цикл идёт дальше."""

    def __init__(self, message: str, requests: int = 0):
        super().__init__(message)
        self.requests = requests  # сколько HTTP-попыток потрачено


class LLMAuthError(LLMError):
    """401/403: ключ или доступ неверны, продолжать бессмысленно."""


@dataclass
class LLMResponse:
    text: str
    requests: int  # сколько HTTP-попыток понадобилось
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


def endpoint(url: str) -> str:
    """LLM_PROXY_URL может быть базой (.../v1) или полным адресом .../chat/completions."""
    url = url.rstrip("/")
    return url if url.endswith("/chat/completions") else url + "/chat/completions"


class LLMClient:
    def __init__(self, url: str, api_key: str, model: str, timeout: int, max_retries: int):
        self.url = endpoint(url)
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def _post(self, payload: dict) -> tuple[int, dict | None, str, float | None]:
        """Один HTTP-запрос: (статус, json, текст ошибки, Retry-After). Статус 0 = сеть/таймаут."""
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
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

    async def complete(self, system: str, user: str) -> LLMResponse:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
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
                raise LLMAuthError(last_error, attempts)
            if status and status not in RETRY_STATUSES:
                raise LLMError(last_error, attempts)
            if attempts <= self.max_retries:
                pause = retry_after if retry_after is not None else 2.0 * attempts
                await asyncio.sleep(min(pause, MAX_PAUSE_SEC))
        raise LLMError(f"после {attempts} попыток: {last_error}", attempts)

    @staticmethod
    def _parse(data: dict, attempts: int, started: float) -> LLMResponse:
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"неожиданный формат ответа: {str(data)[:200]}") from e
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            requests=attempts,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
