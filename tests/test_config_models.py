"""Tests for ModelSlotConfig, ModelsConfig, and resolve_model() in src/config.py."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config import (
    BrainConfig,
    KNXIPConfig,
    ModelSlot,
    ModelSlotConfig,
    ModelsConfig,
)


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
        BrainConfig.model_validate({})


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


# ── KNX/IP configuration ───────────────────────────────────────────────────


def test_knx_ip_defaults_are_safe_and_explicit():
    config = KNXIPConfig()

    assert config.connection_type == "automatic"
    assert config.gateway_port == 3671
    assert config.device_map == "knx_devices.yaml"
    assert not config.secure
    assert config.rate_limit == 20


def test_explicit_tunneling_requires_gateway_ip():
    with pytest.raises(ValidationError, match="gateway_ip"):
        KNXIPConfig(connection_type="tunneling")


def test_tunneling_accepts_explicit_gateway():
    config = KNXIPConfig(connection_type="tunneling", gateway_ip="192.0.2.10")

    assert config.gateway_ip == "192.0.2.10"


def test_secure_connection_requires_keyfile_path():
    with pytest.raises(ValidationError, match="secure_keyfile"):
        KNXIPConfig(secure=True)


def test_secure_knx_ip_requires_explicit_transport_mode():
    with pytest.raises(ValidationError, match="automatic"):
        KNXIPConfig(
            secure=True,
            secure_keyfile="secrets/installation.knxkeys",
        )


def test_knx_ip_rejects_invalid_connection_type():
    with pytest.raises(ValidationError):
        KNXIPConfig.model_validate({"connection_type": "usb"})


def test_knx_ip_rejects_invalid_numeric_limits():
    with pytest.raises(ValidationError):
        KNXIPConfig(gateway_port=0)
    with pytest.raises(ValidationError):
        KNXIPConfig(rate_limit=-1)
    with pytest.raises(ValidationError):
        KNXIPConfig(initial_read_timeout=0)


def test_brain_config_exposes_knx_ip_defaults():
    assert isinstance(_brain().knx_ip, KNXIPConfig)


def test_brain_config_rejects_two_primary_knx_control_platforms():
    with pytest.raises(ValidationError, match="gira_x1.*knx_ip"):
        _brain(platforms=["gira_x1", "knx_ip"])
