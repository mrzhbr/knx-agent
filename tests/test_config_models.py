"""Tests for ModelSlotConfig, ModelsConfig, and resolve_model() in src/config.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import BrainConfig, ModelSlot, ModelSlotConfig, ModelsConfig


# ── Test helpers ────────────────────────────────────────────────────────────

_ROUTER = ModelSlotConfig(
    base_url="https://api.fireworks.ai/inference/v1",
    api_key_env="FIREWORKS_API_KEY",
    model_id="accounts/fireworks/models/gpt-oss-120b",
)
_SPECIALISTS = ModelSlotConfig(
    base_url="https://api.fireworks.ai/inference/v1",
    api_key_env="FIREWORKS_API_KEY",
    model_id="accounts/fireworks/models/minimax-m2p5",
)
_REFLECTION = ModelSlotConfig(
    base_url="https://openrouter.ai/api/v1",
    api_key_env="OPENROUTER_API_KEY",
    model_id="anthropic/claude-opus-4.6",
)
_MODELS = ModelsConfig(router=_ROUTER, specialists=_SPECIALISTS, reflection=_REFLECTION)


def _brain(**kwargs) -> BrainConfig:
    return BrainConfig(models=_MODELS, **kwargs)


# ── models: block is required ──────────────────────────────────────────────


def test_brain_config_requires_models():
    """BrainConfig() without models raises ValidationError."""
    with pytest.raises(ValidationError):
        BrainConfig()


def test_brain_config_with_models():
    """BrainConfig with explicit models block parses correctly."""
    brain = _brain()
    assert isinstance(brain.models, ModelsConfig)
    assert brain.models.router.model_id == "accounts/fireworks/models/gpt-oss-120b"
    assert brain.models.specialists.model_id == "accounts/fireworks/models/minimax-m2p5"
    assert brain.models.reflection.model_id == "anthropic/claude-opus-4.6"


def test_brain_config_models_from_yaml_dict():
    """BrainConfig.model_validate() with models YAML section parses correctly."""
    brain = BrainConfig.model_validate(
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
    assert brain.models.router.model_id == "accounts/fireworks/models/gpt-oss-120b"
    assert brain.models.specialists.model_id == "accounts/fireworks/models/minimax-m2p5"
    assert brain.models.reflection.model_id == "anthropic/claude-opus-4.6"


# ── resolve_model() for all slots ──────────────────────────────────────────


def test_resolve_model_router_tier():
    """resolve_model(ROUTER_TIER) returns the router ModelSlotConfig."""
    brain = _brain()
    slot_config = brain.resolve_model(ModelSlot.ROUTER_TIER)
    assert isinstance(slot_config, ModelSlotConfig)
    assert slot_config.model_id == "accounts/fireworks/models/gpt-oss-120b"
    assert slot_config.base_url == "https://api.fireworks.ai/inference/v1"


def test_resolve_model_specialists_tier():
    """resolve_model(SPECIALISTS_TIER) returns the specialists ModelSlotConfig."""
    brain = _brain()
    slot_config = brain.resolve_model(ModelSlot.SPECIALISTS_TIER)
    assert isinstance(slot_config, ModelSlotConfig)
    assert slot_config.model_id == "accounts/fireworks/models/minimax-m2p5"


def test_resolve_model_reflection_tier():
    """resolve_model(REFLECTION_TIER) returns the reflection ModelSlotConfig."""
    brain = _brain()
    slot_config = brain.resolve_model(ModelSlot.REFLECTION_TIER)
    assert isinstance(slot_config, ModelSlotConfig)
    assert slot_config.model_id == "anthropic/claude-opus-4.6"
    assert slot_config.base_url == "https://openrouter.ai/api/v1"


def test_model_slot_values():
    """ModelSlot enum values are correct."""
    assert ModelSlot.ROUTER_TIER == "router_tier"
    assert ModelSlot.SPECIALISTS_TIER == "specialists_tier"
    assert ModelSlot.REFLECTION_TIER == "reflection_tier"
