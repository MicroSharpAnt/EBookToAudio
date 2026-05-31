from pathlib import Path

import pytest

from ebook_to_audio.config import ConfigError, load_config


def test_load_config_reads_translation_and_tts_sections(tmp_path: Path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
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
        encoding="utf-8",
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
    config_path.write_text(
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
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="source_text"):
        load_config(config_path)
