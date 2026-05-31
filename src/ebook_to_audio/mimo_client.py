from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from pathlib import Path
import time
from typing import Any

from openai import OpenAI


class MissingMimoApiKey(RuntimeError):
    pass


class MimoTTSResponseError(RuntimeError):
    pass


class MissingMimoAudioData(MimoTTSResponseError):
    def __init__(self, response_summary: str):
        self.response_summary = response_summary
        super().__init__(f"MiMo response did not include audio data; {response_summary}")


class MimoTTSClient:
    def __init__(
        self,
        api_key: str | None,
        base_url: str,
        model: str,
        openai_client: Any | None = None,
        retries: int = 3,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise MissingMimoApiKey("MIMO_API_KEY is not set")
        self.model = model
        self.retries = retries
        self.sleeper = sleeper
        self.client = openai_client or OpenAI(api_key=api_key, base_url=base_url)

    def synthesize(self, text: str, voice: str, context: str, output_path: Path) -> Path:
        messages = []
        if context.strip():
            messages.append({"role": "user", "content": context.strip()})
        messages.append({"role": "assistant", "content": text})

        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                completion = self._create_completion(messages, voice)
                audio = _audio_bytes(completion)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(audio)
                return output_path
            except MissingMimoAudioData as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    self.sleeper(0.1 * (attempt + 1))
                    continue
            except MimoTTSResponseError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < self.retries - 1:
                    self.sleeper(0.1 * (attempt + 1))
                    continue
            break

        if isinstance(last_error, MissingMimoAudioData):
            raise MimoTTSResponseError(
                f"MiMo response did not include audio data after {self.retries} attempts; "
                f"last response: {last_error.response_summary}"
            ) from last_error
        raise RuntimeError(f"MiMo synthesis failed after {self.retries} attempts") from last_error

    def _create_completion(self, messages: list[dict[str, str]], voice: str) -> Any:
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            audio={"format": "wav", "voice": voice},
        )


def _audio_bytes(completion: Any) -> bytes:
    try:
        audio_payload = completion.choices[0].message.audio
        data = audio_payload.get("data") if isinstance(audio_payload, dict) else audio_payload.data
    except (AttributeError, IndexError, TypeError) as exc:
        raise MissingMimoAudioData(_response_summary(completion)) from exc
    if not data:
        raise MissingMimoAudioData(_response_summary(completion))
    try:
        audio = base64.b64decode(data, validate=True)
    except (binascii.Error, TypeError) as exc:
        raise MimoTTSResponseError("MiMo response audio data was not valid base64") from exc
    if not audio:
        raise MimoTTSResponseError("MiMo response audio data was empty")
    if not _is_wav_audio(audio):
        raise MimoTTSResponseError("MiMo response audio data was not WAV")
    return audio


def _is_wav_audio(audio: bytes) -> bool:
    return len(audio) >= 12 and audio[:4] == b"RIFF" and audio[8:12] == b"WAVE"


def _response_summary(completion: Any) -> str:
    choice = _first_choice(completion)
    if choice is None:
        return f"type={type(completion).__name__}; choices=missing"

    parts = []
    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason:
        parts.append(f"finish_reason={_short_text(finish_reason)}")

    message = getattr(choice, "message", None)
    if message is None:
        parts.append("message=missing")
        return "; ".join(parts)

    content = getattr(message, "content", None)
    if content:
        parts.append(f"content={_short_text(content)}")

    refusal = getattr(message, "refusal", None)
    if refusal:
        parts.append(f"refusal={_short_text(refusal)}")

    audio = getattr(message, "audio", None)
    if audio is None:
        parts.append("audio=None")
    elif isinstance(audio, dict):
        keys = ",".join(sorted(str(key) for key in audio.keys())) or "empty"
        parts.append(f"audio_keys={keys}")
    else:
        data = getattr(audio, "data", None)
        parts.append(f"audio_data={'present' if data else 'missing'}")

    message_keys = _object_keys(message)
    if message_keys:
        parts.append(f"message_keys={','.join(message_keys)}")

    return "; ".join(parts) or f"type={type(completion).__name__}"


def _first_choice(completion: Any) -> Any | None:
    try:
        return completion.choices[0]
    except (AttributeError, IndexError, TypeError):
        return None


def _object_keys(value: Any) -> list[str]:
    if hasattr(value, "model_dump"):
        try:
            data = value.model_dump(exclude_none=True)
            return sorted(str(key) for key in data.keys())
        except Exception:
            return []
    if hasattr(value, "__dict__"):
        return sorted(str(key) for key in value.__dict__.keys() if not key.startswith("_"))
    return []


def _short_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."
