from pathlib import Path
from pprint import pformat

import pytest

from ebook_to_audio.config import ConfigError, DEFAULT_MAX_UPLOAD_BYTES, load_config


def write_config(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def minimal_config(**overrides: str) -> str:
    values = {
        "active_provider": "deepseek",
        "provider_block": """
    deepseek:
      base_url: "https://api.deepseek.com"
      api_key: "sk-test"
      model: "deepseek-chat"
""",
        "tts_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "tts_api_key": "mimo-key",
        "tts_model": "mimo-audio",
        "tts_voice": "Cherry",
        "extra_translation": "",
        "prompt_block": "",
        "limits_block": "",
        "data_dir": "",
    }
    values.update(overrides)
    return f"""
active_translation_provider: {values["active_provider"]}
{values["data_dir"]}translation:
{values["extra_translation"]}{values["prompt_block"]}  providers:
{values["provider_block"]}tts:
  base_url: "{values["tts_base_url"]}"
  api_key: "{values["tts_api_key"]}"
  model: "{values["tts_model"]}"
  default_voice: "{values["tts_voice"]}"
{values["limits_block"]}"""


def test_load_config_reads_translation_and_tts_sections(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    write_config(
        config_path,
        """
active_translation_provider: deepseek
data_dir: data
translation:
  segment_limit: 1200
  request_timeout_seconds: 45
  max_retries: 2
  prompt:
    system: "translator"
    user_template: "Translate: {source_text}"
  providers:
    deepseek:
      base_url: "https://api.deepseek.com"
      api_key: "sk-test"
      model: "deepseek-chat"
tts:
  base_url: "https://token-plan-cn.xiaomimimo.com/v1"
  api_key: "mimo-key"
  model: "mimo-audio"
  default_voice: "Cherry"
  max_request_chars: 900
  default_parallel_segments: 2
limits:
  max_upload_bytes: 1000000
  max_parallel_translation_segments: 3
  max_parallel_tts_segments: 4
""",
    )

    config = load_config(config_path)

    assert config.active_translation_provider == "deepseek"
    assert config.translation.active.model == "deepseek-chat"
    assert config.translation.prompt.user_template == "Translate: {source_text}"
    assert config.tts.default_voice == "Cherry"
    assert config.limits.max_parallel_tts_segments == 4
    assert config.safe_metadata()["translation"]["providers"]["deepseek"]["has_api_key"] is True


def test_load_config_requires_source_text_placeholder(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    write_config(
        config_path,
        """
active_translation_provider: deepseek
translation:
  prompt:
    user_template: "Translate this"
  providers:
    deepseek:
      base_url: "https://api.deepseek.com"
      api_key: "sk-test"
      model: "deepseek-chat"
tts:
  base_url: "https://token-plan-cn.xiaomimimo.com/v1"
  api_key: "mimo-key"
  model: "mimo-audio"
  default_voice: "Cherry"
""",
    )

    with pytest.raises(ConfigError, match="source_text"):
        load_config(config_path)


def test_load_config_uses_defaults_for_optional_prompt_numeric_and_limits(
    tmp_path: Path,
):
    config_path = tmp_path / "nested" / "config.yaml"
    config_path.parent.mkdir()
    write_config(config_path, minimal_config(data_dir='data_dir: "data"\n'))

    config = load_config(config_path)

    assert config.data_dir == config_path.parent / "data"
    assert config.translation.prompt.system == "You are a helpful translation assistant."
    assert config.translation.prompt.user_template == "Translate: {source_text}"
    assert config.translation.segment_limit == 1200
    assert config.translation.request_timeout_seconds == 45
    assert config.translation.max_retries == 2
    assert config.tts.max_request_chars == 900
    assert config.tts.default_parallel_segments == 2
    assert config.limits.max_upload_bytes == DEFAULT_MAX_UPLOAD_BYTES
    assert config.limits.max_parallel_translation_segments == 3
    assert config.limits.max_parallel_tts_segments == 4


def test_load_config_rejects_unknown_active_provider(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    write_config(config_path, minimal_config(active_provider="missing"))

    with pytest.raises(ConfigError, match="active_translation_provider"):
        load_config(config_path)


@pytest.mark.parametrize(
    "extra_translation,limits_block",
    [
        ("  segment_limit: 0\n", ""),
        ("  request_timeout_seconds: -1\n", ""),
        ("  max_retries: false\n", ""),
        ("", "limits:\n  max_parallel_tts_segments: 0\n"),
    ],
)
def test_load_config_rejects_non_positive_integer_values(
    tmp_path: Path,
    extra_translation: str,
    limits_block: str,
):
    config_path = tmp_path / "config.yaml"
    write_config(
        config_path,
        minimal_config(extra_translation=extra_translation, limits_block=limits_block),
    )

    with pytest.raises(ConfigError, match="positive integer"):
        load_config(config_path)


def test_load_config_rejects_blank_required_text(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    write_config(config_path, minimal_config(tts_api_key="   "))

    with pytest.raises(ConfigError, match="tts.api_key"):
        load_config(config_path)


@pytest.mark.parametrize(
    "provider_block,error_match",
    [
        ("    deepseek: nope\n", "translation.providers.deepseek"),
        ('    "":\n      base_url: "https://api.deepseek.com"\n      api_key: "sk-test"\n      model: "deepseek-chat"\n', "provider"),
    ],
)
def test_load_config_rejects_provider_mapping_shape_errors(
    tmp_path: Path,
    provider_block: str,
    error_match: str,
):
    config_path = tmp_path / "config.yaml"
    write_config(config_path, minimal_config(provider_block=provider_block))

    with pytest.raises(ConfigError, match=error_match):
        load_config(config_path)


def test_safe_metadata_excludes_literal_secret_values(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    write_config(config_path, minimal_config())

    metadata_text = pformat(load_config(config_path).safe_metadata())

    assert "sk-test" not in metadata_text
    assert "mimo-key" not in metadata_text


def test_config_example_loads():
    config = load_config(Path("config.example.yaml"))

    assert config.publishing.description_footer == ""


def test_load_config_reads_publishing_section(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    write_config(
        config_path,
        minimal_config(
            limits_block="""
publishing:
  ximalaya:
    album_id: "122326236"
    default_tags:
      - 有声书
      - 中文文学
    description_footer: "本音频由 EBookToAudio 辅助生成。"
""",
        ),
    )

    config = load_config(config_path)

    assert config.publishing.ximalaya_album_id == "122326236"
    assert config.publishing.default_tags == ("有声书", "中文文学")
    assert config.publishing.description_footer == "本音频由 EBookToAudio 辅助生成。"
    assert config.safe_metadata()["publishing"]["ximalaya"] == {
        "has_album_id": True,
        "default_tags": ["有声书", "中文文学"],
        "has_description_footer": True,
    }
