"""Tests for create_model_for_slot() factory."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from src.agents import create_model_for_slot
from src.config import BrainConfig, ModelSlot


def _build_config_with_slots() -> BrainConfig:
    """Create a BrainConfig with three fully populated model slots."""
    return BrainConfig.model_validate(
        {
            "models": {
                "router": {
                    "base_url": "https://api.fireworks.ai/inference/v1",
                    "api_key_env": "FIREWORKS_API_KEY",
                    "model_id": "accounts/fireworks/models/gpt-oss-120b",
                },
                "specialists": {
                    "base_url": "https://api.fireworks.ai/inference/v1",
                    "api_key_env": "FIREWORKS_API_KEY",
                    "model_id": "accounts/fireworks/models/minimax-m2p5",
                },
                "reflection": {
                    "base_url": "https://openrouter.ai/api/v1",
                    "api_key_env": "OPENROUTER_API_KEY",
                    "model_id": "anthropic/claude-opus-4.6",
                },
            }
        }
    )


@pytest.mark.parametrize(
    "slot,expected_model_id,expected_base_url,env_var",
    [
        (
            ModelSlot.ROUTER_TIER,
            "accounts/fireworks/models/gpt-oss-120b",
            "https://api.fireworks.ai/inference/v1",
            "FIREWORKS_API_KEY",
        ),
        (
            ModelSlot.SPECIALISTS_TIER,
            "accounts/fireworks/models/minimax-m2p5",
            "https://api.fireworks.ai/inference/v1",
            "FIREWORKS_API_KEY",
        ),
        (
            ModelSlot.REFLECTION_TIER,
            "anthropic/claude-opus-4.6",
            "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY",
        ),
    ],
)
def test_create_model_for_slot_constructs_openai_model(
    slot, expected_model_id, expected_base_url, env_var, monkeypatch
):
    """create_model_for_slot returns an OpenAIModel with correct config per slot."""
    monkeypatch.setenv("FIREWORKS_API_KEY", "fw-test")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test")

    config = _build_config_with_slots()

    MockOpenAIModel = MagicMock()
    mock_instance = MagicMock()
    mock_instance.model_id = expected_model_id
    MockOpenAIModel.return_value = mock_instance

    mock_openai_module = MagicMock()
    mock_openai_module.OpenAIModel = MockOpenAIModel

    with patch.dict(
        sys.modules, {"strands.models.openai": mock_openai_module, "openai": MagicMock()}
    ):
        # Re-import to pick up mocked module
        import src.agents as agents_mod

        original_model_class = agents_mod.OpenAIModel
        agents_mod.OpenAIModel = MockOpenAIModel
        try:
            create_model_for_slot(config, slot)
        finally:
            agents_mod.OpenAIModel = original_model_class

    MockOpenAIModel.assert_called_once()
    call_kwargs = (
        MockOpenAIModel.call_args.kwargs
        if hasattr(MockOpenAIModel.call_args, "kwargs")
        else MockOpenAIModel.call_args[1]
    )
    assert call_kwargs["model_id"] == expected_model_id
    assert call_kwargs["client_args"]["base_url"] == expected_base_url
    expected_api_key = "fw-test" if env_var == "FIREWORKS_API_KEY" else "or-test"
    assert call_kwargs["client_args"]["api_key"] == expected_api_key
    assert "max_tokens" in call_kwargs["params"]


def test_create_model_for_slot_raises_when_env_var_missing(monkeypatch):
    """create_model_for_slot raises RuntimeError when API key env var is not set."""
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    config = _build_config_with_slots()

    with pytest.raises(RuntimeError, match="Missing API key env var"):
        create_model_for_slot(config, ModelSlot.ROUTER_TIER)
