from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace
import wave

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


class FakeDictCompletions:
    def __init__(self, audio_data: str):
        self.audio_data = audio_data

    def create(self, **kwargs):
        return {"choices": [{"message": {"audio": {"data": self.audio_data}}}]}


class FakeDictOpenAIClient:
    def __init__(self, audio_data: str):
        self.chat = SimpleNamespace(completions=FakeDictCompletions(audio_data))


def _minimal_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00")
    return buffer.getvalue()


def test_mimo_client_synthesize_writes_wav_bytes(tmp_path: Path):
    wav = _minimal_wav()
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


def test_mimo_client_accepts_dict_shaped_completion(tmp_path: Path):
    wav = _minimal_wav()
    client = FakeDictOpenAIClient(base64.b64encode(wav).decode("ascii"))
    tts = MimoTTSClient(
        api_key="sk-test",
        base_url="https://example.invalid",
        model="mimo",
        openai_client=client,
    )

    output = tts.synthesize("正文", "Cherry", "", tmp_path / "out.wav")

    assert output.read_bytes() == wav


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


def test_mimo_client_rejects_truncated_riff_wave_audio(tmp_path: Path):
    client = FakeOpenAIClient(base64.b64encode(b"RIFFxxxxWAVE").decode("ascii"))
    tts = MimoTTSClient(
        api_key="sk-test",
        base_url="https://example.invalid",
        model="mimo",
        openai_client=client,
    )

    with pytest.raises(MimoTTSResponseError, match="not valid WAV"):
        tts.synthesize("正文", "Cherry", "", tmp_path / "out.wav")


def test_mimo_client_rejects_invalid_base64(tmp_path: Path):
    client = FakeOpenAIClient("not-base64")
    tts = MimoTTSClient(
        api_key="sk-test",
        base_url="https://example.invalid",
        model="mimo",
        openai_client=client,
    )

    with pytest.raises(MimoTTSResponseError, match="not valid base64"):
        tts.synthesize("正文", "Cherry", "", tmp_path / "out.wav")


def test_mimo_client_reports_missing_audio_for_dict_completion(tmp_path: Path):
    class MissingAudioCompletions:
        def create(self, **kwargs):
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "text only"},
                    }
                ]
            }

    client = SimpleNamespace(chat=SimpleNamespace(completions=MissingAudioCompletions()))
    tts = MimoTTSClient(
        api_key="sk-test",
        base_url="https://example.invalid",
        model="mimo",
        openai_client=client,
        retries=1,
    )

    with pytest.raises(MimoTTSResponseError, match="content=text only"):
        tts.synthesize("正文", "Cherry", "", tmp_path / "out.wav")


def test_mimo_client_reports_last_api_error_after_retries(tmp_path: Path):
    class FailingCompletions:
        def create(self, **kwargs):
            raise RuntimeError("unsupported voice: Cherry")

    client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))
    tts = MimoTTSClient(
        api_key="sk-test",
        base_url="https://example.invalid",
        model="mimo",
        openai_client=client,
        retries=2,
        sleeper=lambda _: None,
    )

    with pytest.raises(RuntimeError, match="unsupported voice: Cherry"):
        tts.synthesize("正文", "Cherry", "", tmp_path / "out.wav")
