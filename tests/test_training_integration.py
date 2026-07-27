"""Integration tests for full training data logging flow.

Tests the complete lifecycle:
  BeforeInvocation → BeforeToolCall → AfterToolCall → AfterInvocation
  → interactions.jsonl record with correct fields.

Also tests nested invocation (supervisor → specialist) producing two separate records.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

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
async def test_full_message_flow(tmp_path):
    """Complete BeforeInvocation → tool call → AfterInvocation → interactions.jsonl record."""
    writer = TrainingDataWriter(str(tmp_path))

    # Set up context vars as bootstrapper would
    set_request_id("int-test-001")
    set_user("@person1:example.test")
    set_agent_name("comfort")
    set_model_id("claude-specialists-4-6")

    provider = TrainingDataHookProvider(writer)

    # --- BeforeInvocation ---
    before_inv = MagicMock()
    provider._on_before_invocation(before_inv)

    # --- BeforeToolCall ---
    before_tool = MagicMock()
    before_tool.tool_use = {
        "toolUseId": "tc-001",
        "name": "lights_control",
        "input": {"room": "Wohnzimmer", "action": "on"},
    }
    provider._on_before_tool_call(before_tool)

    # --- AfterToolCall with real ToolResult dict ---
    after_tool = MagicMock()
    after_tool.tool_use = {"toolUseId": "tc-001", "name": "lights_control", "input": {}}
    after_tool.result = {
        "content": [{"text": "Licht im Wohnzimmer eingeschaltet."}],
        "status": "success",
        "toolUseId": "tc-001",
    }
    provider._on_after_tool_call(after_tool)

    # --- AfterInvocation with mock AgentResult ---
    after_inv = MagicMock()
    after_inv.result = MagicMock()
    after_inv.result.__str__ = lambda self: "Ich habe das Licht im Wohnzimmer eingeschaltet."
    provider._on_after_invocation(after_inv)

    # Wait for async fire-and-forget write to complete
    await asyncio.sleep(0.05)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # Assert interactions.jsonl exists and has one record
    interactions_file = tmp_path / "interactions.jsonl"
    assert interactions_file.exists(), "interactions.jsonl not created"

    lines = interactions_file.read_text().strip().split("\n")
    assert len(lines) == 1, f"Expected 1 record, got {len(lines)}"

    record = json.loads(lines[0])

    # Verify all required fields
    assert record["request_id"] == "int-test-001"
    assert record["user"] == "@person1:example.test"
    assert record["agent"] == "comfort"
    assert record["model"] == "claude-specialists-4-6"
    assert len(record["tool_calls"]) == 1

    tool_call = record["tool_calls"][0]
    assert tool_call["tool_name"] == "lights_control"
    assert tool_call["input"] == {"room": "Wohnzimmer", "action": "on"}
    assert tool_call["output_status"] == "success"
    assert tool_call["output_content"] == "Licht im Wohnzimmer eingeschaltet."
    assert isinstance(tool_call["duration_ms"], int)

    assert record["final_response"] == "Ich habe das Licht im Wohnzimmer eingeschaltet."
    assert "timestamp" in record


@pytest.mark.asyncio
async def test_nested_invocation_two_separate_records(tmp_path):
    """Supervisor → specialist nested invocation produces two separate records.

    Outer: supervisor (no tools in this test beyond the nested call)
    Inner: comfort agent with lights_control tool
    """
    writer = TrainingDataWriter(str(tmp_path))

    set_request_id("int-nested-001")
    set_user("@person1:example.test")
    set_agent_name("supervisor")
    set_model_id("claude-specialists-4-6")

    provider = TrainingDataHookProvider(writer)

    # === OUTER BeforeInvocation ===
    outer_before = MagicMock()
    provider._on_before_invocation(outer_before)

    # === INNER BeforeInvocation (nested comfort agent) ===
    inner_before = MagicMock()
    provider._on_before_invocation(inner_before)

    # === INNER tool call ===
    inner_before_tool = MagicMock()
    inner_before_tool.tool_use = {
        "toolUseId": "inner-tc-001",
        "name": "lights_control",
        "input": {"room": "Küche", "action": "off"},
    }
    provider._on_before_tool_call(inner_before_tool)

    inner_after_tool = MagicMock()
    inner_after_tool.tool_use = {"toolUseId": "inner-tc-001", "name": "lights_control", "input": {}}
    inner_after_tool.result = {
        "content": [{"text": "Licht aus."}],
        "status": "success",
        "toolUseId": "inner-tc-001",
    }
    provider._on_after_tool_call(inner_after_tool)

    # === INNER AfterInvocation (flushes inner record) ===
    inner_after = MagicMock()
    inner_after.result = MagicMock()
    inner_after.result.__str__ = lambda self: "Licht in der Küche ausgeschaltet."
    provider._on_after_invocation(inner_after)

    # Flush inner write
    await asyncio.sleep(0.02)

    # === OUTER AfterInvocation (no tool calls at outer level) ===
    outer_after = MagicMock()
    outer_after.result = MagicMock()
    outer_after.result.__str__ = lambda self: "Fertig."
    provider._on_after_invocation(outer_after)

    # Flush all pending writes
    await asyncio.sleep(0.05)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    # Assert two records
    interactions_file = tmp_path / "interactions.jsonl"
    assert interactions_file.exists()
    lines = interactions_file.read_text().strip().split("\n")
    assert len(lines) == 2, (
        f"Expected 2 records, got {len(lines)}: {interactions_file.read_text()!r}"
    )

    inner_record = json.loads(lines[0])
    outer_record = json.loads(lines[1])

    # Inner record has lights_control tool call
    inner_tool_names = [tc["tool_name"] for tc in inner_record["tool_calls"]]
    assert "lights_control" in inner_tool_names
    assert inner_record["final_response"] == "Licht in der Küche ausgeschaltet."

    # Outer record has no tool calls (none were recorded at outer level)
    assert outer_record["final_response"] == "Fertig."


@pytest.mark.asyncio
async def test_multiple_tool_calls_in_sequence(tmp_path):
    """Multiple tool calls in a single invocation are all captured."""
    writer = TrainingDataWriter(str(tmp_path))

    set_request_id("int-multi-001")
    set_user("@person1:example.test")
    set_agent_name("comfort")
    set_model_id("claude-sonnet-4-6")

    provider = TrainingDataHookProvider(writer)

    before_inv = MagicMock()
    provider._on_before_invocation(before_inv)

    # Tool call 1
    for i, (tool_id, tool_name, room) in enumerate(
        [
            ("tc-1", "lights_control", "Wohnzimmer"),
            ("tc-2", "blinds_control", "Schlafzimmer"),
            ("tc-3", "heating_control", "Bad"),
        ]
    ):
        before_tool = MagicMock()
        before_tool.tool_use = {"toolUseId": tool_id, "name": tool_name, "input": {"room": room}}
        provider._on_before_tool_call(before_tool)

        after_tool = MagicMock()
        after_tool.tool_use = {"toolUseId": tool_id, "name": tool_name, "input": {}}
        after_tool.result = {
            "content": [{"text": f"{tool_name} in {room} OK"}],
            "status": "success",
            "toolUseId": tool_id,
        }
        provider._on_after_tool_call(after_tool)

    after_inv = MagicMock()
    after_inv.result = MagicMock()
    after_inv.result.__str__ = lambda self: "Alle Aktionen ausgeführt."
    provider._on_after_invocation(after_inv)

    await asyncio.sleep(0.05)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    interactions_file = tmp_path / "interactions.jsonl"
    record = json.loads(interactions_file.read_text().strip())
    assert len(record["tool_calls"]) == 3

    tool_names = [tc["tool_name"] for tc in record["tool_calls"]]
    assert "lights_control" in tool_names
    assert "blinds_control" in tool_names
    assert "heating_control" in tool_names
