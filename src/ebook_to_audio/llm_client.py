from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .config import ProviderConfig


class LLMError(RuntimeError):
    pass


MAX_RETRY_DELAY_SECONDS = 30.0


class LLMClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client or httpx.AsyncClient()

    async def translate(
        self,
        provider: ProviderConfig,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> str:
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {provider.api_key}"}
        attempts = 1 + max(0, max_retries)
        last_error = "LLM request failed"

        for attempt in range(attempts):
            response: httpx.Response | None = None
            try:
                response = await self._http_client.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=timeout_seconds,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc) or exc.__class__.__name__
            else:
                if response.is_error:
                    last_error = _error_message(response)
                else:
                    try:
                        content = response.json()["choices"][0]["message"]["content"]
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        last_error = f"Could not parse LLM response: {exc}"
                    else:
                        if isinstance(content, str):
                            return content.strip()
                        last_error = (
                            "Could not parse LLM response: "
                            "choices[0].message.content is not text"
                        )

            if attempt < attempts - 1:
                await asyncio.sleep(_retry_delay_seconds(attempt, response))

        raise LLMError(last_error)

    async def aclose(self) -> None:
        await self._http_client.aclose()


def _error_message(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text}"

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()

    return f"HTTP {response.status_code}: {response.reason_phrase}"


def _retry_delay_seconds(attempt: int, response: httpx.Response | None) -> float:
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after:
        parsed = _parse_retry_after(retry_after)
        if parsed is not None:
            return min(parsed, MAX_RETRY_DELAY_SECONDS)
    return min(float(2**attempt), MAX_RETRY_DELAY_SECONDS)


def _parse_retry_after(value: str) -> float | None:
    try:
        seconds = float(value)
    except ValueError:
        pass
    else:
        if seconds >= 0:
            return seconds
        return None

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)
