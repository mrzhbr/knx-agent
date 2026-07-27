"""Tests that JSONL output format is fine-tuning compatible.

Validates that all three JSONL files have correct required fields
and that each line is valid JSON.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.training.context import (
    set_agent_name,
    set_model_id,
    set_request_id,
    set_user,
)
from src.training.hooks import TrainingDataHookProvider
from src.training.writer import TrainingDataWriter


@pytest.mark.asyncio
async def test_routing_jsonl_format(tmp_path):
    """Each line in routing.jsonl is valid JSON with required fields."""
    writer = TrainingDataWriter(str(tmp_path))

    # Write two routing records
    await writer.log_routing(
        {
            "request_id": "req-001",
            "source": "agent",
            "input_text": "Licht an",
            "raw_response": '{"intent": "comfort"}',
            "parsed": {
                "intent": "comfort",
                "complexity": 2,
                "agents": ["comfort"],
                "model": "router",
                "safety_critical": False,
            },
            "router_model": "accounts/fireworks/models/deepseek-v3p2",
        }
    )
    await writer.log_routing(
        {
            "request_id": "req-002",
            "source": "offline",
            "input_text": "Rolladen hoch",
            "raw_response": "",
            "parsed": {
                "intent": "comfort",
                "complexity": 1,
                "agents": ["comfort"],
                "model": "offline",
                "safety_critical": False,
            },
            "router_model": "",
            "offline_action": "blinds_up",
            "offline_params": {"room": "Schlafzimmer"},
        }
    )

    routing_file = tmp_path / "routing.jsonl"
    assert routing_file.exists()

    lines = routing_file.read_text().strip().split("\n")
    assert len(lines) == 2

    required_fields = {"timestamp", "request_id", "source", "input_text", "parsed", "router_model"}
    for i, line in enumerate(lines):
        record = json.loads(line)  # Must be valid JSON
        missing = required_fields - set(record.keys())
        assert not missing, f"Line {i}: missing fields {missing}"

        # Validate parsed subfields
        parsed = record["parsed"]
        assert "intent" in parsed, f"Line {i}: parsed.intent missing"
        assert "agents" in parsed, f"Line {i}: parsed.agents missing"
        assert isinstance(parsed["agents"], list), f"Line {i}: parsed.agents not a list"


@pytest.mark.asyncio
async def test_interactions_jsonl_format(tmp_path):
    """Each line in interactions.jsonl is valid JSON with required fields including output_status."""
    writer = TrainingDataWriter(str(tmp_path))

    set_request_id("req-int-001")
    set_user("@person1:example.test")
    set_agent_name("comfort")
    set_model_id("accounts/fireworks/models/deepseek-v3p2")

    provider = TrainingDataHookProvider(writer)

    # Simulate a full lifecycle
    before_inv = object.__new__(type("E", (), {"__init__": lambda s: None}))
    provider._on_before_invocation(before_inv)

    from unittest.mock import MagicMock

    before_tool = MagicMock()
    before_tool.tool_use = {
        "toolUseId": "t-001",
        "name": "lights_control",
        "input": {"room": "Bad", "action": "on"},
    }
    provider._on_before_tool_call(before_tool)

    after_tool = MagicMock()
    after_tool.tool_use = {"toolUseId": "t-001", "name": "lights_control", "input": {}}
    after_tool.result = {
        "content": [{"text": "Licht eingeschaltet."}],
        "status": "success",
        "toolUseId": "t-001",
    }
    provider._on_after_tool_call(after_tool)

    after_inv = MagicMock()
    after_inv.result = MagicMock()
    after_inv.result.__str__ = lambda self: "Licht ist an."
    provider._on_after_invocation(after_inv)

    await asyncio.sleep(0.05)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    interactions_file = tmp_path / "interactions.jsonl"
    assert interactions_file.exists()
    record = json.loads(interactions_file.read_text().strip())

    required_top = {
        "timestamp",
        "request_id",
        "user",
        "agent",
        "model",
        "tool_calls",
        "final_response",
    }
    missing_top = required_top - set(record.keys())
    assert not missing_top, f"Missing top-level fields: {missing_top}"

    assert isinstance(record["tool_calls"], list)
    assert len(record["tool_calls"]) >= 1

    tool_call = record["tool_calls"][0]
    required_tc = {"tool_name", "input", "output_status", "output_content", "duration_ms"}
    missing_tc = required_tc - set(tool_call.keys())
    assert not missing_tc, f"Missing tool_call fields: {missing_tc}"

    assert tool_call["output_status"] == "success"
    assert isinstance(tool_call["duration_ms"], int)


@pytest.mark.asyncio
async def test_background_jsonl_format(tmp_path):
    """Each line in background.jsonl is valid JSON with required fields."""
    writer = TrainingDataWriter(str(tmp_path))

    await writer.log_background(
        {
            "task": "pattern_detector",
            "model": "claude-router-4-5-20251001",
            "prompt_length": 1234,
            "prompt_preview": "Analysiere die folgenden Ereignisse...",
            "response": '[{"pattern": "Licht an um 07:00"}]',
            "patterns_found": 1,
        }
    )

    await writer.log_background(
        {
            "task": "reflection",
            "model": "claude-reflection-4-6",
            "prompt_length": 4523,
            "prompt_preview": "Analysiere die Wochendaten...",
            "response": '{"routine_updates": [], "new_suggestions": []}',
            "analysis_keys_found": ["routine_updates", "new_suggestions"],
        }
    )

    bg_file = tmp_path / "background.jsonl"
    assert bg_file.exists()

    lines = bg_file.read_text().strip().split("\n")
    assert len(lines) == 2

    required_fields = {"timestamp", "task", "model", "prompt_preview", "response"}
    for i, line in enumerate(lines):
        record = json.loads(line)
        missing = required_fields - set(record.keys())
        assert not missing, f"Line {i}: missing fields {missing}"
        assert record["task"] in ("pattern_detector", "reflection"), (
            f"Line {i}: unexpected task {record['task']!r}"
        )


@pytest.mark.asyncio
async def test_all_lines_are_valid_json(tmp_path):
    """All records across all three files are valid JSON (no interleaving, no corruption)."""
    writer = TrainingDataWriter(str(tmp_path))

    # Write many concurrent records
    import asyncio

    tasks = []
    for i in range(10):
        tasks.append(asyncio.create_task(writer.log_routing({"seq": i, "source": "test"})))
        tasks.append(asyncio.create_task(writer.log_interaction({"seq": i, "tool_calls": []})))
        tasks.append(asyncio.create_task(writer.log_background({"seq": i, "task": "test"})))

    await asyncio.gather(*tasks)

    for filename in ("routing.jsonl", "interactions.jsonl", "background.jsonl"):
        filepath = tmp_path / filename
        assert filepath.exists(), f"{filename} not created"
        lines = filepath.read_text().strip().split("\n")
        assert len(lines) == 10, f"{filename}: expected 10 lines, got {len(lines)}"
        for j, line in enumerate(lines):
            try:
                json.loads(line)  # Must not raise
            except json.JSONDecodeError as e:
                pytest.fail(f"{filename} line {j} is not valid JSON: {e}\nLine: {line!r}")
