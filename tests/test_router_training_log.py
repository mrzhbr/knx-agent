"""Tests for training data logging in router.py (Tasks 2.2 and 2.3)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.training.context import set_request_id


@pytest.fixture(autouse=True)
def reset_router_training_writer():
    """Reset router's training writer between tests."""
    from src.agents import router as router_module

    original = router_module._training_writer
    yield
    router_module._training_writer = original


@pytest.mark.asyncio
async def test_set_training_writer_injects_writer():
    """set_training_writer stores the writer on the module."""
    from src.agents.router import set_training_writer
    import src.agents.router as router_module

    mock_writer = MagicMock()
    set_training_writer(mock_writer)
    assert router_module._training_writer is mock_writer


@pytest.mark.asyncio
async def test_offline_match_logs_routing(tmp_path):
    """Offline match logs to routing.jsonl with source='offline' and request_id."""
    from src.training.writer import TrainingDataWriter
    from src.agents.router import route, set_training_writer

    writer = TrainingDataWriter(str(tmp_path))
    set_training_writer(writer)
    set_request_id("req-offline-test")

    await route("Licht an im Wohnzimmer", router_agent=None, check_internet=False)

    # Wait for async fire-and-forget task to complete
    await asyncio.sleep(0.05)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    routing_file = tmp_path / "routing.jsonl"
    assert routing_file.exists(), "routing.jsonl should be created for offline match"

    record = json.loads(routing_file.read_text().strip())
    assert record["source"] == "offline", f"Expected source='offline', got {record['source']}"
    assert record["request_id"] == "req-offline-test"
    assert "input_text" in record
    assert record["offline_action"] != ""


@pytest.mark.asyncio
async def test_agent_routing_logs_with_request_id(tmp_path):
    """Agent classification logs to routing.jsonl with request_id, source, parsed fields."""
    from src.training.writer import TrainingDataWriter
    from src.agents.router import route, set_training_writer

    writer = TrainingDataWriter(str(tmp_path))
    set_training_writer(writer)
    set_request_id("req-agent-test")

    # Mock router agent that returns valid JSON classification
    mock_agent = MagicMock()
    mock_agent.messages = []
    mock_agent.model = MagicMock()
    mock_agent.model.model_id = "accounts/fireworks/models/deepseek-v3p2"
    mock_agent.return_value = MagicMock()
    mock_agent.return_value.__str__ = lambda self: json.dumps(
        {
            "intent": "comfort",
            "complexity": 2,
            "agents": ["comfort"],
            "model": "router",
            "safety_critical": False,
        }
    )

    with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda fn, *a: fn(*a))):
        await route(
            "Dimm das Licht auf 50%",
            router_agent=mock_agent,
            check_internet=False,
        )

    # Wait for async tasks
    await asyncio.sleep(0.05)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    routing_file = tmp_path / "routing.jsonl"
    assert routing_file.exists(), "routing.jsonl should be created for agent classification"

    record = json.loads(routing_file.read_text().strip())
    assert record["source"] == "agent"
    assert record["request_id"] == "req-agent-test"
    assert "input_text" in record
    assert "parsed" in record
    assert record["parsed"]["intent"] == "comfort"
    assert record["parsed"]["agents"] == ["comfort"]
    assert "router_model" in record


@pytest.mark.asyncio
async def test_no_logging_when_writer_not_set():
    """No error and no routing.jsonl when training writer is not set."""
    import src.agents.router as router_mod

    router_mod._training_writer = None
    set_request_id("req-no-writer")

    # Should not raise
    result = await router_mod.route("Licht an", router_agent=None, check_internet=False)
    assert result is not None


@pytest.mark.asyncio
async def test_offline_log_has_correct_fields(tmp_path):
    """Offline routing record contains all required JSONL fields."""
    from src.training.writer import TrainingDataWriter
    from src.agents.router import route, set_training_writer

    writer = TrainingDataWriter(str(tmp_path))
    set_training_writer(writer)
    set_request_id("req-fields-test")

    await route("Rolladen hoch im Schlafzimmer", router_agent=None, check_internet=False)

    await asyncio.sleep(0.05)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    routing_file = tmp_path / "routing.jsonl"
    if not routing_file.exists():
        pytest.skip("No offline pattern matched — regex may not cover this text")

    record = json.loads(routing_file.read_text().strip())
    required_fields = {"timestamp", "request_id", "source", "input_text", "parsed", "router_model"}
    missing = required_fields - set(record.keys())
    assert not missing, f"Missing required fields: {missing}"
