"""Tests for per-user profile resolution and guardrail context wiring.

Verifies that:
1. resolve_user maps Matrix IDs to FamilyProfile
2. set_request_user_profile sets the contextvar
3. GuardrailProvider reads the profile from contextvars per tool call
4. Concurrent asyncio tasks get isolated profile values
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from strands.hooks import BeforeToolCallEvent  # type: ignore[import-not-found]

from src.core.guardrails import (
    GuardrailContext,
    GuardrailProvider,
    _current_user_profile,
    set_request_user_profile,
)
from src.core.profiles import (
    FamilyProfile,
    FAMILY_PROFILES,
    load_profiles_from_config,
    resolve_user,
)


def _make_tool_event(tool_name: str, tool_input: dict | None = None) -> BeforeToolCallEvent:
    return BeforeToolCallEvent(
        agent=MagicMock(),
        selected_tool=MagicMock(),
        tool_use={"name": tool_name, "input": tool_input or {}, "toolUseId": "test-id"},
        invocation_state={},
    )


# ── resolve_user ────────────────────────────────────────────────────────────


def test_resolve_user_matches_matrix_id():
    """resolve_user returns the correct FamilyProfile for a known Matrix ID."""
    load_profiles_from_config(
        [
            {
                "key": "person1",
                "name": "Person 1",
                "trust_level": "admin",
                "matrix_ids": ["@person1:home"],
            },
            {"key": "kid", "name": "Kind", "trust_level": "limited", "matrix_ids": ["@kind:home"]},
        ]
    )
    profile = resolve_user("@person1:home")
    assert profile is not None
    assert profile.name == "Person 1"
    assert profile.trust_level == "admin"

    kid_profile = resolve_user("@kind:home")
    assert kid_profile is not None
    assert kid_profile.trust_level == "limited"
    FAMILY_PROFILES.clear()


def test_resolve_user_returns_none_for_unknown():
    """resolve_user returns None for unregistered Matrix IDs."""
    load_profiles_from_config(
        [
            {
                "key": "person1",
                "name": "Person 1",
                "trust_level": "admin",
                "matrix_ids": ["@person1:home"],
            },
        ]
    )
    assert resolve_user("@stranger:evil") is None
    FAMILY_PROFILES.clear()


def test_resolve_user_case_insensitive():
    """resolve_user matches Matrix IDs case-insensitively."""
    load_profiles_from_config(
        [
            {
                "key": "person1",
                "name": "Person 1",
                "trust_level": "admin",
                "matrix_ids": ["@Person1:Home"],
            },
        ]
    )
    assert resolve_user("@person1:home") is not None
    FAMILY_PROFILES.clear()


# ── contextvar wiring ───────────────────────────────────────────────────────


def test_set_request_user_profile_sets_contextvar():
    """set_request_user_profile stores the profile in the contextvar."""
    profile = FamilyProfile(name="Test", trust_level="admin")
    set_request_user_profile(profile)
    assert _current_user_profile.get() is profile
    # Clean up
    _current_user_profile.set(None)


def test_guardrail_reads_profile_from_contextvar():
    """GuardrailProvider._on_before_tool_call reads user_profile from contextvar."""
    kid_profile = FamilyProfile(name="Kind", trust_level="limited")
    set_request_user_profile(kid_profile)

    ctx = GuardrailContext()
    provider = GuardrailProvider(context=ctx)

    event = _make_tool_event("alarm_control", {"action": "status"})
    provider._on_before_tool_call(event)

    # kids_limited_tools should have blocked this
    assert event.cancel_tool
    assert "Berechtigung" in str(event.cancel_tool)
    _current_user_profile.set(None)


def test_guardrail_allows_admin_from_contextvar():
    """Admin profile from contextvar does not trigger kids_limited_tools."""
    admin_profile = FamilyProfile(name="Admin", trust_level="admin")
    set_request_user_profile(admin_profile)

    ctx = GuardrailContext()
    provider = GuardrailProvider(context=ctx)

    event = _make_tool_event("alarm_control", {"action": "status"})
    provider._on_before_tool_call(event)

    assert not event.cancel_tool
    _current_user_profile.set(None)


def test_guardrail_allows_none_profile():
    """None profile (unknown user) does not trigger kids_limited_tools."""
    set_request_user_profile(None)

    ctx = GuardrailContext()
    provider = GuardrailProvider(context=ctx)

    event = _make_tool_event("alarm_control", {"action": "status"})
    provider._on_before_tool_call(event)

    assert not event.cancel_tool


# ── concurrent isolation ────────────────────────────────────────────────────


async def test_contextvar_isolated_between_tasks():
    """Two concurrent asyncio tasks get independent user profile values."""
    results: dict[str, str | None] = {}

    async def simulate_request(sender_name: str, profile: FamilyProfile | None):
        set_request_user_profile(profile)
        # Yield control to let the other task set its own profile
        await asyncio.sleep(0.01)
        # Read back — should still be this task's profile
        current = _current_user_profile.get(None)
        results[sender_name] = current.name if current else None

    admin = FamilyProfile(name="Admin", trust_level="admin")
    kid = FamilyProfile(name="Kind", trust_level="limited")

    await asyncio.gather(
        simulate_request("admin_task", admin),
        simulate_request("kid_task", kid),
    )

    assert results["admin_task"] == "Admin"
    assert results["kid_task"] == "Kind"
