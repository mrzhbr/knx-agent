"""TrainingDataHookProvider — Strands hook provider for LLM interaction logging.

Captures the full lifecycle of each agent invocation:
  BeforeInvocationEvent → init fresh accumulators (tool_calls, pending_calls) via contextvars
  BeforeToolCallEvent   → record tool name, input, start_time in pending_calls
  AfterToolCallEvent    → complete record with duration, status, flattened output
  AfterInvocationEvent  → flush completed records + final response to interactions.jsonl

Design decisions from plan:
  AD-6: ContextVar accumulators (not instance state) — safe for nested invocations
  AD-7: _flatten_tool_result() helper — handles ToolResult TypedDict
  AD-8: str(event.result).strip() — AgentResult is not plain text
  AD-14: asyncio.Lock in writer serializes concurrent file writes
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from strands.hooks import HookRegistry
from strands.hooks.events import (
    AfterInvocationEvent,
    AfterToolCallEvent,
    BeforeInvocationEvent,
    BeforeToolCallEvent,
)

from src.training.context import (
    get_agent_name,
    get_model_id,
    get_pending_calls,
    get_request_id,
    get_tool_calls,
    get_user,
    init_pending_calls,
    init_tool_calls,
)

if TYPE_CHECKING:
    from src.training.writer import TrainingDataWriter

logger = logging.getLogger(__name__)


def _flatten_tool_result(result: Any) -> str:
    """Extract text content from a ToolResult TypedDict and truncate to 2000 chars.

    ToolResult shape: {"content": list[ToolResultContent], "status": Literal, "toolUseId": str}
    ToolResultContent has optional keys: "text", "json", "document", "image"
    """
    parts: list[str] = []
    for item in result.get("content", []):
        if "text" in item:
            parts.append(item["text"])
        elif "json" in item:
            parts.append(json.dumps(item["json"], ensure_ascii=False))
    text = "\n".join(parts)
    return text[:2000]


class TrainingDataHookProvider:
    """Strands HookProvider that logs agent interactions to JSONL files.

    Implements the HookProvider protocol (same shape as GuardrailProvider).
    Register on Agent via hooks=[training_hook_provider].

    IMPORTANT: Do NOT register on the router agent — it runs via asyncio.to_thread()
    which uses a temporary event loop. asyncio.create_task() calls inside hooks would
    be silently dropped when that loop exits. Router logging is handled explicitly
    at the call site in router.py (AD-11).
    """

    def __init__(self, writer: "TrainingDataWriter") -> None:
        self._writer = writer

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        """Register hook callbacks with the Strands HookRegistry."""
        registry.add_callback(BeforeInvocationEvent, self._on_before_invocation)
        registry.add_callback(BeforeToolCallEvent, self._on_before_tool_call)
        registry.add_callback(AfterToolCallEvent, self._on_after_tool_call)
        registry.add_callback(AfterInvocationEvent, self._on_after_invocation)

    def _on_before_invocation(self, event: BeforeInvocationEvent) -> None:
        """Initialize fresh accumulators for this invocation context (AD-6).

        Each nested invocation (supervisor -> specialist) gets its OWN list/dict
        because contextvars are isolated per async context.
        """
        init_tool_calls()
        init_pending_calls()

    def _on_before_tool_call(self, event: BeforeToolCallEvent) -> None:
        """Record tool start time and input in pending_calls keyed by toolUseId."""
        tool_use_id = event.tool_use["toolUseId"]
        tool_name = event.tool_use["name"]
        tool_input = event.tool_use["input"]

        pending = get_pending_calls()
        pending[tool_use_id] = {
            "tool_name": tool_name,
            "input": tool_input,
            "start_time": time.time(),
        }

    def _on_after_tool_call(self, event: AfterToolCallEvent) -> None:
        """Complete the tool record with duration, status, and flattened output."""
        tool_use_id = event.tool_use["toolUseId"]
        pending = get_pending_calls()
        started = pending.pop(tool_use_id, None)

        if started is None:
            logger.debug("TrainingHook: no pending record for toolUseId=%s", tool_use_id)
            return

        duration_ms = int((time.time() - started["start_time"]) * 1000)
        output_status = event.result.get("status", "unknown") if event.result else "unknown"
        output_content = _flatten_tool_result(event.result) if event.result else ""

        record = {
            "tool_name": started["tool_name"],
            "input": started["input"],
            "output_status": output_status,
            "output_content": output_content,
            "duration_ms": duration_ms,
        }

        tool_calls = get_tool_calls()
        tool_calls.append(record)

    def _on_after_invocation(self, event: AfterInvocationEvent) -> None:
        """Flush accumulated tool calls + final response to interactions.jsonl."""
        tool_calls = list(get_tool_calls())  # Snapshot before any nested init wipes it

        # Extract final response text from AgentResult (AD-8)
        final_response = ""
        if event.result is not None:
            final_response = str(event.result).strip()[:5000]

        record = {
            "request_id": get_request_id(),
            "user": get_user(),
            "agent": get_agent_name(),
            "model": get_model_id(),
            "tool_calls": tool_calls,
            "final_response": final_response,
        }

        # Fire-and-forget: create task on the running event loop
        try:
            asyncio.create_task(self._writer.log_interaction(record))
        except RuntimeError:
            # No running event loop (should not happen in normal async path)
            logger.warning("TrainingHook: no running event loop for log_interaction")
