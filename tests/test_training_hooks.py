"""Tests for TrainingDataHookProvider and _flatten_tool_result helper."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from src.training.context import (
    get_pending_calls,
    get_tool_calls,
    set_agent_name,
    set_model_id,
    set_request_id,
    set_user,
)
from src.training.hooks import TrainingDataHookProvider, _flatten_tool_result
from src.training.writer import TrainingDataWriter


# ── _flatten_tool_result tests ────────────────────────────────────────────────


def test_flatten_tool_result_single_text():
    """Single text content returns the text string."""
    result = {"content": [{"text": "hello"}], "status": "success", "toolUseId": "x"}
    assert _flatten_tool_result(result) == "hello"


def test_flatten_tool_result_json_content():
    """JSON content is serialized to a JSON string."""
    result = {"content": [{"json": {"key": "val"}}], "status": "success", "toolUseId": "x"}
    assert _flatten_tool_result(result) == '{"key": "val"}'


def test_flatten_tool_result_mixed_text_and_json():
    """Mixed text and JSON content are joined with newline."""
    result = {
        "content": [{"text": "first"}, {"json": {"b": 2}}],
        "status": "success",
        "toolUseId": "x",
    }
    out = _flatten_tool_result(result)
    assert out == 'first\n{"b": 2}'


def test_flatten_tool_result_empty_content():
    """Empty content list returns empty string."""
    result = {"content": [], "status": "success", "toolUseId": "x"}
    assert _flatten_tool_result(result) == ""


def test_flatten_tool_result_truncation():
    """Text longer than 2000 chars is truncated to 2000."""
    long_text = "x" * 3000
    result = {"content": [{"text": long_text}], "status": "success", "toolUseId": "x"}
    out = _flatten_tool_result(result)
    assert len(out) == 2000
    assert out == "x" * 2000


def test_flatten_tool_result_missing_content_key():
    """Missing 'content' key returns empty string without raising."""
    result = {"status": "error", "toolUseId": "x"}
    assert _flatten_tool_result(result) == ""


# ── Hook lifecycle simulation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hook_provider_full_lifecycle(tmp_path):
    """Simulate BeforeInvocation → BeforeToolCall → AfterToolCall → AfterInvocation.

    Verifies that log_interaction is called with correct data.
    """
    writer = TrainingDataWriter(str(tmp_path))

    # Set context vars
    set_request_id("req-abc123")
    set_user("@person1:example.test")
    set_agent_name("comfort")
    set_model_id("claude-specialists-4-6")

    provider = TrainingDataHookProvider(writer)

    # Simulate BeforeInvocationEvent
    before_inv_event = MagicMock()
    provider._on_before_invocation(before_inv_event)

    # Simulate BeforeToolCallEvent
    before_tool_event = MagicMock()
    before_tool_event.tool_use = {
        "toolUseId": "tool-001",
        "name": "lights_control",
        "input": {"room": "Wohnzimmer", "action": "on"},
    }
    provider._on_before_tool_call(before_tool_event)

    # Verify pending call was recorded
    pending = get_pending_calls()
    assert "tool-001" in pending
    assert pending["tool-001"]["tool_name"] == "lights_control"

    # Simulate AfterToolCallEvent
    after_tool_event = MagicMock()
    after_tool_event.tool_use = {"toolUseId": "tool-001", "name": "lights_control", "input": {}}
    after_tool_event.result = {
        "content": [{"text": "Licht eingeschaltet."}],
        "status": "success",
        "toolUseId": "tool-001",
    }
    provider._on_after_tool_call(after_tool_event)

    # Verify tool was moved from pending to completed
    assert "tool-001" not in get_pending_calls()
    tool_calls = get_tool_calls()
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool_name"] == "lights_control"
    assert tool_calls[0]["output_status"] == "success"
    assert tool_calls[0]["output_content"] == "Licht eingeschaltet."
    assert "duration_ms" in tool_calls[0]

    # Simulate AfterInvocationEvent
    after_inv_event = MagicMock()
    after_inv_event.result = MagicMock()
    after_inv_event.result.__str__ = lambda self: "Ich habe das Licht eingeschaltet."

    provider._on_after_invocation(after_inv_event)

    # Flush pending tasks
    await asyncio.sleep(0)
    await asyncio.gather(*[t for t in asyncio.all_tasks() if t is not asyncio.current_task()])

    # Verify interactions.jsonl was written
    interactions_file = tmp_path / "interactions.jsonl"
    assert interactions_file.exists()
    record = json.loads(interactions_file.read_text().strip())

    assert record["request_id"] == "req-abc123"
    assert record["user"] == "@person1:example.test"
    assert record["agent"] == "comfort"
    assert record["model"] == "claude-specialists-4-6"
    assert len(record["tool_calls"]) == 1
    assert record["tool_calls"][0]["output_status"] == "success"
    assert record["tool_calls"][0]["output_content"] == "Licht eingeschaltet."
    assert record["final_response"] == "Ich habe das Licht eingeschaltet."


@pytest.mark.asyncio
async def test_hook_provider_nested_invocation(tmp_path):
    """Simulate nested invocation (supervisor → specialist).

    Verifies that inner and outer invocations produce separate records
    with correct tool_calls lists (AD-6: ContextVar isolation).
    """
    writer = TrainingDataWriter(str(tmp_path))

    set_request_id("req-nested")
    set_user("@person1:example.test")
    set_agent_name("supervisor")
    set_model_id("claude-specialists-4-6")

    provider = TrainingDataHookProvider(writer)

    # --- Outer BeforeInvocation ---
    outer_before = MagicMock()
    provider._on_before_invocation(outer_before)
    outer_tool_calls = get_tool_calls()  # Reference to outer list

    # --- Outer tool call starts ---
    outer_tool_start = MagicMock()
    outer_tool_start.tool_use = {"toolUseId": "outer-001", "name": "comfort_agent", "input": {}}
    provider._on_before_tool_call(outer_tool_start)

    # --- Inner BeforeInvocation (nested agent) — creates fresh accumulator ---
    inner_before = MagicMock()
    provider._on_before_invocation(inner_before)
    # After inner init, get_tool_calls() returns the NEW inner list
    inner_tool_calls = get_tool_calls()
    assert inner_tool_calls is not outer_tool_calls  # Different list objects

    # --- Inner tool call ---
    inner_tool_start = MagicMock()
    inner_tool_start.tool_use = {"toolUseId": "inner-001", "name": "lights_control", "input": {}}
    provider._on_before_tool_call(inner_tool_start)

    inner_tool_end = MagicMock()
    inner_tool_end.tool_use = {"toolUseId": "inner-001", "name": "lights_control", "input": {}}
    inner_tool_end.result = {
        "content": [{"text": "Licht an"}],
        "status": "success",
        "toolUseId": "inner-001",
    }
    provider._on_after_tool_call(inner_tool_end)

    # --- Inner AfterInvocation ---
    inner_after = MagicMock()
    inner_after.result = MagicMock()
    inner_after.result.__str__ = lambda self: "Licht eingeschaltet"
    provider._on_after_invocation(inner_after)

    # Flush inner task
    await asyncio.sleep(0)

    # After inner flush, the contextvar now holds the inner (now empty) list
    # The outer AfterToolCall must still work using outer pending_calls

    # --- Outer AfterToolCall ---
    outer_tool_end = MagicMock()
    outer_tool_end.tool_use = {"toolUseId": "outer-001", "name": "comfort_agent", "input": {}}
    outer_tool_end.result = {
        "content": [{"text": "Fertig"}],
        "status": "success",
        "toolUseId": "outer-001",
    }
    # Note: after inner BeforeInvocation, pending_calls contextvar was replaced.
    # The outer pending record is LOST in this scenario. This is the known limitation
    # described in AD-12 — nested calls are attributed to supervisor.
    # We verify the outer AfterInvocation still works without crashing.
    provider._on_after_tool_call(outer_tool_end)

    # --- Outer AfterInvocation ---
    outer_after = MagicMock()
    outer_after.result = MagicMock()
    outer_after.result.__str__ = lambda self: "Supervisor fertig"
    provider._on_after_invocation(outer_after)

    # Flush all tasks
    await asyncio.sleep(0.05)
    pending_tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending_tasks:
        await asyncio.gather(*pending_tasks, return_exceptions=True)

    # Verify two records in interactions.jsonl
    interactions_file = tmp_path / "interactions.jsonl"
    assert interactions_file.exists()
    lines = interactions_file.read_text().strip().split("\n")
    assert len(lines) == 2, f"Expected 2 records, got {len(lines)}"

    inner_record = json.loads(lines[0])
    outer_record = json.loads(lines[1])

    # Inner record has the lights_control tool call
    assert any(tc["tool_name"] == "lights_control" for tc in inner_record["tool_calls"])
    assert inner_record["final_response"] == "Licht eingeschaltet"

    # Outer record final response
    assert outer_record["final_response"] == "Supervisor fertig"


@pytest.mark.asyncio
async def test_hook_provider_no_tool_calls(tmp_path):
    """AfterInvocation with no tool calls produces an empty tool_calls list."""
    writer = TrainingDataWriter(str(tmp_path))
    set_request_id("req-notool")
    set_user("@person1:example.test")
    set_agent_name("comfort")
    set_model_id("router")

    provider = TrainingDataHookProvider(writer)

    before_inv = MagicMock()
    provider._on_before_invocation(before_inv)

    after_inv = MagicMock()
    after_inv.result = MagicMock()
    after_inv.result.__str__ = lambda self: "Keine Aktion."
    provider._on_after_invocation(after_inv)

    await asyncio.sleep(0)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

    interactions_file = tmp_path / "interactions.jsonl"
    assert interactions_file.exists()
    record = json.loads(interactions_file.read_text().strip())
    assert record["tool_calls"] == []
    assert record["final_response"] == "Keine Aktion."
