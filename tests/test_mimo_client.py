from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from ebook_to_audio.mimo_client import MimoTTSClient, MimoTTSResponseError


class FakeCompletions:
    def __init__(self, audio_data: str):
        self.audio_data = audio_data
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(audio={"data": self.audio_data})
                )
            ]
        )


class FakeOpenAIClient:
    def __init__(self, audio_data: str):
        self.chat = SimpleNamespace(
            completions=FakeCompletions(audio_data)
        )


def test_mimo_client_synthesize_writes_wav_bytes(tmp_path: Path):
    wav = b"RIFFxxxxWAVEfake"
    client = FakeOpenAIClient(base64.b64encode(wav).decode("ascii"))
    tts = MimoTTSClient(
        api_key="sk-test",
        base_url="https://example.invalid",
        model="mimo",
        openai_client=client,
    )

    output = tts.synthesize("正文", "Cherry", "温柔旁白", tmp_path / "out.wav")

    assert output.read_bytes().startswith(b"RIFF")
    request = client.chat.completions.requests[0]
    assert request["audio"] == {"format": "wav", "voice": "Cherry"}
    assert request["messages"] == [
        {"role": "user", "content": "温柔旁白"},
        {"role": "assistant", "content": "正文"},
    ]


def test_mimo_client_rejects_non_wav_audio(tmp_path: Path):
    client = FakeOpenAIClient(base64.b64encode(b"not wav").decode("ascii"))
    tts = MimoTTSClient(
        api_key="sk-test",
        base_url="https://example.invalid",
        model="mimo",
        openai_client=client,
    )

    with pytest.raises(MimoTTSResponseError, match="not WAV"):
        tts.synthesize("正文", "Cherry", "", tmp_path / "out.wav")
