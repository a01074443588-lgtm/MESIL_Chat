from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from app.care_briefing_provider import (
    NvidiaCareBriefingProvider,
    OllamaCareBriefingProvider,
)
from scripts.run_care_briefing_suite import (
    DEFAULT_NVIDIA_BASE_URL,
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OLLAMA_MODEL,
    _build_provider,
    _read_env_value,
    _select_cases,
)


def _args(tmp_path: Path, **overrides) -> Namespace:
    values = {
        "provider": "ollama",
        "base_url": None,
        "model": None,
        "timeout": 37,
        "env_file": tmp_path / ".env.care-briefing.local",
        "api_key_env": "NVIDIA_API_KEY",
    }
    values.update(overrides)
    return Namespace(**values)


def test_ollama_runner_uses_safe_local_defaults(tmp_path):
    provider = _build_provider(_args(tmp_path), None)

    assert isinstance(provider, OllamaCareBriefingProvider)
    assert provider.base_url == DEFAULT_OLLAMA_BASE_URL
    assert provider.model == DEFAULT_OLLAMA_MODEL
    assert provider.timeout_seconds == 37


def test_nvidia_runner_requires_explicit_model_before_reading_key(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    with pytest.raises(ValueError, match="--model"):
        _build_provider(_args(tmp_path, provider="nvidia"), None)


def test_nvidia_runner_stops_cleanly_when_key_is_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    env_file = tmp_path / ".env.care-briefing.local"
    env_file.write_text("NVIDIA_API_KEY=\n", encoding="utf-8")

    with pytest.raises(ValueError, match="NVIDIA_API_KEY") as error:
        _build_provider(
            _args(
                tmp_path,
                provider="nvidia",
                model="nvidia/nemotron-test",
                env_file=env_file,
            ),
            None,
        )

    assert "Bearer" not in str(error.value)


def test_nvidia_runner_reads_only_named_key_from_ignored_file(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("UNRELATED_SECRET", raising=False)
    env_file = tmp_path / ".env.care-briefing.local"
    env_file.write_text(
        "UNRELATED_SECRET=must-not-be-loaded\n"
        "NVIDIA_API_KEY=test-local-key\n",
        encoding="utf-8",
    )

    provider = _build_provider(
        _args(
            tmp_path,
            provider="nvidia",
            model="nvidia/nemotron-test",
            env_file=env_file,
        ),
        None,
    )

    assert isinstance(provider, NvidiaCareBriefingProvider)
    assert provider.base_url == DEFAULT_NVIDIA_BASE_URL
    assert provider.model == "nvidia/nemotron-test"
    assert "UNRELATED_SECRET" not in __import__("os").environ


def test_env_reader_supports_comments_export_and_quoted_values(tmp_path):
    env_file = tmp_path / "comparison.env"
    env_file.write_text(
        "# local only\nexport NVIDIA_API_KEY='quoted-key'\n",
        encoding="utf-8",
    )

    assert _read_env_value(env_file, "NVIDIA_API_KEY") == "quoted-key"


def test_case_selector_returns_all_cases_when_no_case_is_requested():
    cases = [{"case_id": "CASE-A"}, {"case_id": "CASE-B"}]

    assert _select_cases(cases, None) is cases


def test_case_selector_returns_one_exact_case():
    cases = [{"case_id": "CASE-A"}, {"case_id": "CASE-B"}]

    assert _select_cases(cases, "CASE-B") == [{"case_id": "CASE-B"}]


def test_case_selector_rejects_unknown_case():
    with pytest.raises(ValueError, match="UNKNOWN"):
        _select_cases([{"case_id": "CASE-A"}], "UNKNOWN")
