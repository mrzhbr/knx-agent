"""Context variables for training data logging — per-request correlation.

Uses Python contextvars so each asyncio task (Matrix message handler) gets its own values.
Same pattern as _current_user_profile in src/core/guardrails.py.
"""

from __future__ import annotations

from contextvars import ContextVar

# Per-request context variables
_current_request_id: ContextVar[str] = ContextVar("training_request_id", default="")
_current_agent_name: ContextVar[str] = ContextVar("training_agent_name", default="")
_current_model_id: ContextVar[str] = ContextVar("training_model_id", default="")
_current_user: ContextVar[str] = ContextVar("training_user", default="")

# Per-invocation accumulators — NO default (LookupError if accessed before init)
_current_tool_calls: ContextVar[list[dict]] = ContextVar("training_tool_calls")
_current_pending_calls: ContextVar[dict[str, dict]] = ContextVar("training_pending_calls")


# ── Request ID ─────────────────────────────────────────────────────────────────


def set_request_id(value: str) -> None:
    """Set the request ID for the current async context."""
    _current_request_id.set(value)


def get_request_id() -> str:
    """Get the request ID for the current async context."""
    return _current_request_id.get()


# ── Agent Name ─────────────────────────────────────────────────────────────────


def set_agent_name(value: str) -> None:
    """Set the agent name for the current async context."""
    _current_agent_name.set(value)


def get_agent_name() -> str:
    """Get the agent name for the current async context."""
    return _current_agent_name.get()


# ── Model ID ───────────────────────────────────────────────────────────────────


def set_model_id(value: str) -> None:
    """Set the model ID for the current async context."""
    _current_model_id.set(value)


def get_model_id() -> str:
    """Get the model ID for the current async context."""
    return _current_model_id.get()


# ── User ───────────────────────────────────────────────────────────────────────


def set_user(value: str) -> None:
    """Set the user (Matrix ID) for the current async context."""
    _current_user.set(value)


def get_user() -> str:
    """Get the user (Matrix ID) for the current async context."""
    return _current_user.get()


# ── Tool Calls Accumulator ─────────────────────────────────────────────────────


def init_tool_calls() -> list[dict]:
    """Create a fresh tool calls list for this invocation context and set it in the contextvar.

    Must be called at BeforeInvocationEvent to reset state for nested invocations.
    Returns the new empty list.
    """
    lst: list[dict] = []
    _current_tool_calls.set(lst)
    return lst


def get_tool_calls() -> list[dict]:
    """Get the tool calls list for the current invocation context.

    Returns empty list if not initialized (e.g. router agent excluded from hooks).
    """
    try:
        return _current_tool_calls.get()
    except LookupError:
        return []


# ── Pending Calls Accumulator ──────────────────────────────────────────────────


def init_pending_calls() -> dict[str, dict]:
    """Create a fresh pending calls dict for this invocation context and set it in the contextvar.

    Keyed by toolUseId. Must be called at BeforeInvocationEvent.
    Returns the new empty dict.
    """
    d: dict[str, dict] = {}
    _current_pending_calls.set(d)
    return d


def get_pending_calls() -> dict[str, dict]:
    """Get the pending calls dict for the current invocation context.

    Returns empty dict if not initialized.
    """
    try:
        return _current_pending_calls.get()
    except LookupError:
        return {}
