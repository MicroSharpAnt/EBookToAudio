from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when application configuration is invalid."""


PRESET_TTS_VOICES = ("冰糖", "茉莉", "苏打", "白桦", "Mia", "Chloe", "Milo", "Dean")
DEFAULT_TTS_VOICE = PRESET_TTS_VOICES[0]
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    api_key: str
    model: str


@dataclass(frozen=True)
class PromptConfig:
    system: str = "You are a helpful translation assistant."
    user_template: str = "Translate: {source_text}"


@dataclass(frozen=True)
class TranslationConfig:
    segment_limit: int
    request_timeout_seconds: int
    max_retries: int
    prompt: PromptConfig
    providers: dict[str, ProviderConfig]
    active_provider_name: str

    @property
    def active(self) -> ProviderConfig:
        return self.providers[self.active_provider_name]


@dataclass(frozen=True)
class TTSConfig:
    base_url: str
    api_key: str
    model: str
    default_voice: str
    max_request_chars: int
    default_parallel_segments: int


@dataclass(frozen=True)
class LimitsConfig:
    max_upload_bytes: int
    max_parallel_translation_segments: int
    max_parallel_tts_segments: int


@dataclass(frozen=True)
class PublishingConfig:
    ximalaya_album_id: str = ""
    default_tags: tuple[str, ...] = ()
    description_footer: str = ""


@dataclass(frozen=True)
class AppConfig:
    active_translation_provider: str
    data_dir: Path
    translation: TranslationConfig
    tts: TTSConfig
    limits: LimitsConfig
    publishing: PublishingConfig

    def safe_metadata(self) -> dict[str, Any]:
        return {
            "active_translation_provider": self.active_translation_provider,
            "data_dir": str(self.data_dir),
            "translation": {
                "segment_limit": self.translation.segment_limit,
                "request_timeout_seconds": self.translation.request_timeout_seconds,
                "max_retries": self.translation.max_retries,
                "prompt": {
                    "system": self.translation.prompt.system,
                    "user_template": self.translation.prompt.user_template,
                },
                "providers": {
                    name: {
                        "base_url": provider.base_url,
                        "model": provider.model,
                        "has_api_key": bool(provider.api_key),
                    }
                    for name, provider in self.translation.providers.items()
                },
            },
            "tts": {
                "base_url": self.tts.base_url,
                "model": self.tts.model,
                "default_voice": supported_tts_voice_or_default(self.tts.default_voice),
                "voices": list(PRESET_TTS_VOICES),
                "max_request_chars": self.tts.max_request_chars,
                "default_parallel_segments": self.tts.default_parallel_segments,
                "has_api_key": bool(self.tts.api_key),
            },
            "limits": {
                "max_upload_bytes": self.limits.max_upload_bytes,
                "max_parallel_translation_segments": self.limits.max_parallel_translation_segments,
                "max_parallel_tts_segments": self.limits.max_parallel_tts_segments,
            },
            "publishing": {
                "ximalaya": {
                    "has_album_id": bool(self.publishing.ximalaya_album_id),
                    "default_tags": list(self.publishing.default_tags),
                    "has_description_footer": bool(self.publishing.description_footer),
                },
            },
        }


def load_config(path: Path) -> AppConfig:
    raw = _load_yaml(path)

    active_translation_provider = _get_text(
        raw, "active_translation_provider", "active_translation_provider"
    )
    data_dir = Path(_get_text(raw, "data_dir", "data_dir", required=False) or "data")
    if not data_dir.is_absolute():
        data_dir = path.parent / data_dir

    translation_raw = _get_mapping(raw, "translation", "translation")
    prompt = _build_prompt(
        _get_mapping(translation_raw, "prompt", "translation.prompt", required=False)
    )
    providers = _build_providers(
        _get_mapping(translation_raw, "providers", "translation.providers")
    )
    if active_translation_provider not in providers:
        raise ConfigError(
            f"active_translation_provider must reference an existing provider: "
            f"{active_translation_provider}"
        )

    translation = TranslationConfig(
        segment_limit=_get_positive_int(
            translation_raw, "segment_limit", "translation.segment_limit", default=1200
        ),
        request_timeout_seconds=_get_positive_int(
            translation_raw,
            "request_timeout_seconds",
            "translation.request_timeout_seconds",
            default=45,
        ),
        max_retries=_get_positive_int(
            translation_raw, "max_retries", "translation.max_retries", default=2
        ),
        prompt=prompt,
        providers=providers,
        active_provider_name=active_translation_provider,
    )

    tts_raw = _get_mapping(raw, "tts", "tts")
    tts = TTSConfig(
        base_url=_get_text(tts_raw, "base_url", "tts.base_url"),
        api_key=_get_text(tts_raw, "api_key", "tts.api_key"),
        model=_get_text(tts_raw, "model", "tts.model"),
        default_voice=_get_text(tts_raw, "default_voice", "tts.default_voice"),
        max_request_chars=_get_positive_int(
            tts_raw, "max_request_chars", "tts.max_request_chars", default=900
        ),
        default_parallel_segments=_get_positive_int(
            tts_raw,
            "default_parallel_segments",
            "tts.default_parallel_segments",
            default=2,
        ),
    )

    limits_raw = _get_mapping(raw, "limits", "limits", required=False)
    limits = LimitsConfig(
        max_upload_bytes=_get_positive_int(
            limits_raw,
            "max_upload_bytes",
            "limits.max_upload_bytes",
            default=DEFAULT_MAX_UPLOAD_BYTES,
        ),
        max_parallel_translation_segments=_get_positive_int(
            limits_raw,
            "max_parallel_translation_segments",
            "limits.max_parallel_translation_segments",
            default=3,
        ),
        max_parallel_tts_segments=_get_positive_int(
            limits_raw,
            "max_parallel_tts_segments",
            "limits.max_parallel_tts_segments",
            default=4,
        ),
    )
    publishing = _build_publishing(
        _get_mapping(raw, "publishing", "publishing", required=False)
    )

    return AppConfig(
        active_translation_provider=active_translation_provider,
        data_dir=data_dir,
        translation=translation,
        tts=tts,
        limits=limits,
        publishing=publishing,
    )


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"Could not read config file: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse config file: {path}") from exc

    if not isinstance(loaded, dict):
        raise ConfigError("config must be a mapping")
    return loaded


def supported_tts_voice_or_default(voice: str | None) -> str:
    if voice and voice.strip() in PRESET_TTS_VOICES:
        return voice.strip()
    return DEFAULT_TTS_VOICE


def _build_prompt(raw: dict[str, Any]) -> PromptConfig:
    prompt = PromptConfig(
        system=_get_text(raw, "system", "translation.prompt.system", required=False)
        or PromptConfig.system,
        user_template=_get_text(
            raw, "user_template", "translation.prompt.user_template", required=False
        )
        or PromptConfig.user_template,
    )
    if "{source_text}" not in prompt.user_template:
        raise ConfigError("translation.prompt.user_template must include {source_text}")
    return prompt


def _build_providers(raw: dict[str, Any]) -> dict[str, ProviderConfig]:
    if not raw:
        raise ConfigError("translation.providers must include at least one provider")

    providers: dict[str, ProviderConfig] = {}
    for name, provider_raw in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("translation.providers names must be non-empty text")
        provider_path = f"translation.providers.{name}"
        provider_data = _ensure_mapping(provider_raw, provider_path)
        providers[name] = ProviderConfig(
            base_url=_get_text(provider_data, "base_url", f"{provider_path}.base_url"),
            api_key=_get_text(provider_data, "api_key", f"{provider_path}.api_key"),
            model=_get_text(provider_data, "model", f"{provider_path}.model"),
        )
    return providers


def _build_publishing(raw: dict[str, Any]) -> PublishingConfig:
    ximalaya_raw = _get_mapping(raw, "ximalaya", "publishing.ximalaya", required=False)
    return PublishingConfig(
        ximalaya_album_id=_get_text(
            ximalaya_raw,
            "album_id",
            "publishing.ximalaya.album_id",
            required=False,
            allow_empty=True,
        ),
        default_tags=tuple(
            _get_text_list(
                ximalaya_raw,
                "default_tags",
                "publishing.ximalaya.default_tags",
            )
        ),
        description_footer=_get_text(
            ximalaya_raw,
            "description_footer",
            "publishing.ximalaya.description_footer",
            required=False,
            allow_empty=True,
        ),
    )


def _get_mapping(
    data: dict[str, Any], key: str, path: str, *, required: bool = True
) -> dict[str, Any]:
    value = data.get(key)
    if value is None:
        if required:
            raise ConfigError(f"{path} is required")
        return {}
    return _ensure_mapping(value, path)


def _ensure_mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must be a mapping")
    return value


def _get_text(
    data: dict[str, Any],
    key: str,
    path: str,
    *,
    required: bool = True,
    allow_empty: bool = False,
) -> str:
    value = data.get(key)
    if value is None:
        if required:
            raise ConfigError(f"{path} is required")
        return ""
    if not isinstance(value, str):
        raise ConfigError(f"{path} must be non-empty text")
    if allow_empty and not value:
        return ""
    if not value.strip():
        raise ConfigError(f"{path} must be non-empty text")
    return value


def _get_text_list(data: dict[str, Any], key: str, path: str) -> list[str]:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{path} must be a list of text values")
    values: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(f"{path}[{index}] must be text")
        cleaned = item.strip()
        if cleaned:
            values.append(cleaned)
    return values


def _get_positive_int(
    data: dict[str, Any], key: str, path: str, *, default: int
) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{path} must be a positive integer")
    return value
