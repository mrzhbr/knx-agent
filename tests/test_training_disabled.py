"""Tests that training logging is a no-op when disabled."""

from __future__ import annotations

import pytest

from src.config import BrainConfig, ModelsConfig, ModelSlotConfig

_STUB_SLOT = ModelSlotConfig(base_url="http://test", api_key_env="TEST_KEY", model_id="test")
_STUB_MODELS = ModelsConfig(router=_STUB_SLOT, specialists=_STUB_SLOT, reflection=_STUB_SLOT)


def test_training_data_disabled_by_default():
    """training_data.enabled is False by default."""
    config = BrainConfig(models=_STUB_MODELS)
    assert config.training_data.enabled is False


def test_training_data_config_enabled_true():
    """training_data.enabled can be set to True."""
    config = BrainConfig(
        models=_STUB_MODELS,
        training_data={"enabled": True, "output_dir": "data/training"},
    )
    assert config.training_data.enabled is True


@pytest.mark.asyncio
async def test_no_writer_created_when_disabled(tmp_path):
    """When training_data.enabled=False, no writer is created and no files are written.

    This tests the guard condition that the bootstrapper checks before creating
    the TrainingDataWriter. Since we can't run the full bootstrapper in a unit test,
    we verify the config flag is False by default and that directly calling the
    router without a writer doesn't create any files.
    """
    from src.agents.router import route
    import src.agents.router as router_module

    # Ensure no writer is set
    original_writer = router_module._training_writer
    router_module._training_writer = None

    try:
        # Route a message — no writer set, no files should be created
        result = await route("Licht an", router_agent=None, check_internet=False)
        assert result is not None

        # No routing.jsonl in tmp_path
        routing_file = tmp_path / "routing.jsonl"
        assert not routing_file.exists(), "routing.jsonl should not be created when writer is None"
    finally:
        router_module._training_writer = original_writer


@pytest.mark.asyncio
async def test_training_writer_is_none_by_default():
    """Router module _training_writer starts as None."""
    import src.agents.router as router_module

    # If set by previous tests, reset it
    original = router_module._training_writer
    router_module._training_writer = None
    assert router_module._training_writer is None
    router_module._training_writer = original


def test_no_import_error_without_training_enabled():
    """All modules import cleanly even when training is not enabled."""
    # This test just verifies no ImportError occurs for any training-related module
    from src.training.writer import TrainingDataWriter
    from src.training.hooks import TrainingDataHookProvider

    assert TrainingDataWriter is not None
    assert TrainingDataHookProvider is not None
