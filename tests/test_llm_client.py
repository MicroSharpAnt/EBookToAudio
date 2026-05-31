import httpx
import pytest

from ebook_to_audio.config import ProviderConfig
from ebook_to_audio.llm_client import LLMClient


@pytest.mark.asyncio
async def test_llm_client_reads_openai_compatible_content():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "译文"}}]},
        )

    client = LLMClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await client.translate(
        ProviderConfig("https://api.deepseek.com", "sk", "deepseek-chat"),
        "system",
        "user",
        5,
        1,
    )

    assert result == "译文"
    await client.aclose()


@pytest.mark.asyncio
async def test_llm_client_retries_after_initial_rate_limit():
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                json={"error": {"message": "rate limited"}},
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "第二次译文"}}]},
        )

    client = LLMClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    result = await client.translate(
        ProviderConfig("https://api.deepseek.com", "sk", "deepseek-chat"),
        "system",
        "user",
        5,
        1,
    )

    assert result == "第二次译文"
    assert calls == 2
    await client.aclose()
