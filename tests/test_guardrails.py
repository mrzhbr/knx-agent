"""Tests for GuardrailProvider — HookProvider protocol compliance and rule evaluation."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from strands.hooks import (  # type: ignore[import-not-found]
    BeforeInvocationEvent,
    BeforeToolCallEvent,
)

from src.core.guardrails import (
    GuardrailContext,
    GuardrailProvider,
    RequestSource,
    request_security_context,
    set_request_user_profile,
)


def make_tool_event(tool_name: str, tool_input: dict | None = None) -> BeforeToolCallEvent:
    """Create a BeforeToolCallEvent with required fields."""
    return BeforeToolCallEvent(
        agent=MagicMock(),
        selected_tool=MagicMock(),
        tool_use={
            "name": tool_name,
            "input": tool_input or {},
            "toolUseId": "test-id-001",
        },
        invocation_state={},
    )


def make_invocation_event() -> BeforeInvocationEvent:
    """Create a BeforeInvocationEvent with required fields."""
    return BeforeInvocationEvent(
        agent=MagicMock(),
    )


class MockPresenceManager:
    def __init__(self, nobody_home=True, open_windows=None, hal=None):
        self._nobody_home = nobody_home
        self._hal = hal
        self.open_windows = open_windows or []

    def nobody_home(self):
        return self._nobody_home


class MockUserProfile:
    def __init__(self, trust_level="admin"):
        self.trust_level = trust_level


# ── Protocol compliance ────────────────────────────────────────────────────


def test_guardrail_provider_has_register_hooks():
    """GuardrailProvider implements HookProvider protocol with register_hooks(**kwargs)."""
    provider = GuardrailProvider()
    assert hasattr(provider, "register_hooks")
    sig = inspect.signature(provider.register_hooks)
    params = sig.parameters
    assert "registry" in params
    assert "kwargs" in params


def test_callbacks_are_not_coroutines():
    """_on_before_tool_call and _on_before_invocation must be plain def, not async def."""
    provider = GuardrailProvider()
    assert not inspect.iscoroutinefunction(provider._on_before_tool_call)
    assert not inspect.iscoroutinefunction(provider._on_before_invocation)


# ── BeforeToolCallEvent attribute access ─────────────────────────────────


def test_reads_tool_name_from_tool_use():
    """_on_before_tool_call reads tool name from event.tool_use['name']."""
    provider = GuardrailProvider()
    event = make_tool_event("lights_control", {"room": "Wohnzimmer"})
    # Should not raise — name is read from tool_use["name"]
    provider._on_before_tool_call(event)


def test_reads_tool_input_from_tool_use():
    """_on_before_tool_call reads tool input from event.tool_use['input']."""
    provider = GuardrailProvider()
    event = make_tool_event("alarm_control", {"action": "status"})
    provider._on_before_tool_call(event)


# ── alarm_requires_presence_check ─────────────────────────────────────────


def test_alarm_arm_blocked_when_someone_home():
    """Alarm arming sets cancel_tool when someone is home."""
    presence = MockPresenceManager(nobody_home=False, open_windows=[])
    ctx = GuardrailContext(presence_manager=presence)
    provider = GuardrailProvider(context=ctx)

    event = make_tool_event("alarm_control", {"action": "arm"})
    provider._on_before_tool_call(event)
    assert event.cancel_tool
    assert "Jemand ist zuhause" in str(event.cancel_tool)


def test_alarm_presence_check_defers_hal_backend_to_async_tool():
    presence = MockPresenceManager(nobody_home=False, open_windows=[], hal=object())
    provider = GuardrailProvider(context=GuardrailContext(presence_manager=presence))

    event = make_tool_event("alarm_control", {"action": "arm"})
    provider._on_before_tool_call(event)

    assert not event.cancel_tool


def test_alarm_arm_passes_when_nobody_home():
    """Alarm arming does not cancel when nobody is home and windows are closed."""
    presence = MockPresenceManager(nobody_home=True, open_windows=[])
    ctx = GuardrailContext(presence_manager=presence)
    provider = GuardrailProvider(context=ctx)

    event = make_tool_event("alarm_control", {"action": "arm"})
    provider._on_before_tool_call(event)
    assert not event.cancel_tool


# ── alarm_requires_windows_closed ─────────────────────────────────────────


def test_alarm_arm_blocked_when_windows_open():
    """Alarm arming sets cancel_tool when windows are open."""
    presence = MockPresenceManager(nobody_home=True, open_windows=["Schlafzimmer Fenster"])
    ctx = GuardrailContext(presence_manager=presence)
    provider = GuardrailProvider(context=ctx)

    event = make_tool_event("alarm_control", {"action": "arm"})
    provider._on_before_tool_call(event)
    assert event.cancel_tool
    assert "Fenster offen" in str(event.cancel_tool)


# ── door_requires_confirmation ─────────────────────────────────────────────


def test_door_open_sets_cancel_tool_without_confirmation():
    """door_open sets cancel_tool (steer) when user_confirmed is False."""
    ctx = GuardrailContext(user_confirmed=False)
    provider = GuardrailProvider(context=ctx)

    event = make_tool_event("door_open", {})
    provider._on_before_tool_call(event)
    assert event.cancel_tool
    assert "Tür" in str(event.cancel_tool) or "Frage" in str(event.cancel_tool)


def test_door_open_passes_with_confirmation():
    """door_open does not cancel when user_confirmed is True."""
    ctx = GuardrailContext(user_confirmed=True)
    provider = GuardrailProvider(context=ctx)

    event = make_tool_event("door_open", {})
    provider._on_before_tool_call(event)
    assert not event.cancel_tool


def test_automation_blocks_all_security_actions():
    provider = GuardrailProvider()
    profile = MockUserProfile(trust_level="admin")
    with request_security_context(user_profile=profile, source=RequestSource.AUTOMATION):
        for tool_name, args in (("alarm_control", {"action": "arm"}), ("door_open", {})):
            event = make_tool_event(tool_name, args)
            provider._on_before_tool_call(event)
            assert event.cancel_tool
            assert "automatisch" in str(event.cancel_tool)


# ── max_tool_calls ─────────────────────────────────────────────────────────


def test_max_tool_calls_blocks_after_15():
    """After 15 tool calls, cancel_tool is set on the 16th call."""
    ctx = GuardrailContext(tool_call_count=0)
    provider = GuardrailProvider(context=ctx)

    final_event = make_tool_event("lights_control", {})
    for _ in range(16):
        final_event = make_tool_event("lights_control", {})
        final_event.cancel_tool = False  # reset before each call (simulate fresh event)
        provider._on_before_tool_call(final_event)

    # The 16th call (tool_call_count becomes 16 after increment) should be blocked
    assert final_event.cancel_tool
    assert "Zu viele" in str(final_event.cancel_tool)


def test_max_tool_calls_counter_increments():
    """tool_call_count increments with each call."""
    ctx = GuardrailContext(tool_call_count=0)
    provider = GuardrailProvider(context=ctx)

    event = make_tool_event("lights_control", {})
    provider._on_before_tool_call(event)
    assert ctx.tool_call_count == 1

    event2 = make_tool_event("blinds_control", {})
    provider._on_before_tool_call(event2)
    assert ctx.tool_call_count == 2


# ── kids_limited_tools ─────────────────────────────────────────────────────


def test_kids_limited_blocks_alarm():
    """kids_limited_tools sets cancel_tool for alarm_control with limited trust_level."""
    profile = MockUserProfile(trust_level="limited")
    set_request_user_profile(profile)
    provider = GuardrailProvider()

    event = make_tool_event("alarm_control", {"action": "status"})
    provider._on_before_tool_call(event)
    assert event.cancel_tool
    assert "Berechtigung" in str(event.cancel_tool)
    set_request_user_profile(None)


def test_kids_can_use_lights():
    """kids_limited_tools does not cancel lights_control."""
    profile = MockUserProfile(trust_level="limited")
    set_request_user_profile(profile)
    provider = GuardrailProvider()

    event = make_tool_event("lights_control", {"room": "Wohnzimmer"})
    provider._on_before_tool_call(event)
    assert not event.cancel_tool
    set_request_user_profile(None)


def test_admin_user_profile_passes_all_tools():
    """Admin trust level does not trigger kids_limited_tools."""
    profile = MockUserProfile(trust_level="admin")
    set_request_user_profile(profile)
    provider = GuardrailProvider()

    event = make_tool_event("alarm_control", {"action": "status"})
    provider._on_before_tool_call(event)
    # Should not be blocked by kids rule (may be blocked by other rules, but not kids)
    # With action=status, no presence/window rule fires either
    assert not event.cancel_tool
    set_request_user_profile(None)


# ── _on_before_invocation ─────────────────────────────────────────────────


def test_on_before_invocation_resets_tool_call_count():
    """_on_before_invocation resets tool_call_count to 0."""
    ctx = GuardrailContext(tool_call_count=42)
    provider = GuardrailProvider(context=ctx)

    event = make_invocation_event()
    provider._on_before_invocation(event)

    assert ctx.tool_call_count == 0


# ── Non-matching tools pass through ────────────────────────────────────────


def test_unrelated_tool_no_cancel():
    """Tools that don't match any rule selector pass through without cancellation."""
    ctx = GuardrailContext()
    provider = GuardrailProvider(context=ctx)

    event = make_tool_event("get_room_state", {"room": "Wohnzimmer"})
    provider._on_before_tool_call(event)
    assert not event.cancel_tool


def test_none_user_profile_does_not_trigger_kids_rule():
    """None user_profile (default) never triggers kids_limited_tools."""
    ctx = GuardrailContext(user_profile=None)
    provider = GuardrailProvider(context=ctx)

    event = make_tool_event("alarm_control", {"action": "status"})
    provider._on_before_tool_call(event)
    # Should not be blocked by kids rule (no profile = no kids restriction)
    assert not event.cancel_tool


# ── kids_batch_restricted ─────────────────────────────────────────────────


def test_kids_can_use_execute_batch_with_lights_blinds():
    """Kids can use execute_batch if all actions are lights/blinds."""
    import json

    profile = MockUserProfile(trust_level="limited")
    set_request_user_profile(profile)
    provider = GuardrailProvider()

    commands = json.dumps(
        [
            {"room": "Wohnzimmer", "action": "lights_off"},
            {"room": "Schlafzimmer", "action": "blinds_down"},
            {"room": "Kueche", "action": "lights_on"},
        ]
    )
    event = make_tool_event("execute_batch", {"commands": commands})
    provider._on_before_tool_call(event)
    assert not event.cancel_tool
    set_request_user_profile(None)


def test_kids_blocked_execute_batch_with_heating():
    """Kids blocked from execute_batch containing heating_set action."""
    import json

    profile = MockUserProfile(trust_level="limited")
    set_request_user_profile(profile)
    provider = GuardrailProvider()

    commands = json.dumps(
        [
            {"room": "Wohnzimmer", "action": "lights_off"},
            {"room": "Wohnzimmer", "action": "heating_set", "temperature": 20},
        ]
    )
    event = make_tool_event("execute_batch", {"commands": commands})
    provider._on_before_tool_call(event)
    assert event.cancel_tool
    assert "Berechtigung" in str(event.cancel_tool)
    set_request_user_profile(None)


def test_kids_blocked_execute_batch_with_ac_action():
    """Kids blocked from execute_batch containing ac_on action."""
    import json

    profile = MockUserProfile(trust_level="limited")
    set_request_user_profile(profile)
    provider = GuardrailProvider()

    commands = json.dumps(
        [
            {"room": "Wohnzimmer", "action": "ac_on"},
        ]
    )
    event = make_tool_event("execute_batch", {"commands": commands})
    provider._on_before_tool_call(event)
    assert event.cancel_tool
    set_request_user_profile(None)


def test_kids_blocked_execute_batch_one_forbidden_in_batch():
    """Kids blocked if even ONE command in batch has a forbidden action."""
    import json

    profile = MockUserProfile(trust_level="limited")
    set_request_user_profile(profile)
    provider = GuardrailProvider()

    commands = json.dumps(
        [
            {"room": "Wohnzimmer", "action": "lights_off"},
            {"room": "Schlafzimmer", "action": "blinds_up"},
            {"room": "Wohnzimmer", "action": "alarm_control"},  # forbidden
        ]
    )
    event = make_tool_event("execute_batch", {"commands": commands})
    provider._on_before_tool_call(event)
    assert event.cancel_tool
    set_request_user_profile(None)


def test_kids_blocked_execute_batch_malformed_json():
    """Kids blocked (safe default) if commands JSON is malformed."""
    profile = MockUserProfile(trust_level="limited")
    set_request_user_profile(profile)
    provider = GuardrailProvider()

    event = make_tool_event("execute_batch", {"commands": "not valid json {{"})
    provider._on_before_tool_call(event)
    assert event.cancel_tool
    set_request_user_profile(None)


def test_admin_can_use_execute_batch_any_actions():
    """Admin users can use execute_batch with any actions."""
    import json

    profile = MockUserProfile(trust_level="admin")
    set_request_user_profile(profile)
    provider = GuardrailProvider()

    commands = json.dumps(
        [
            {"room": "Wohnzimmer", "action": "heating_set", "temperature": 22},
            {"room": "Wohnzimmer", "action": "ac_on"},
        ]
    )
    event = make_tool_event("execute_batch", {"commands": commands})
    provider._on_before_tool_call(event)
    assert not event.cancel_tool
    set_request_user_profile(None)


def test_kids_batch_restricted_before_kids_limited_tools():
    """kids_batch_restricted rule is at lower index than kids_limited_tools in default rules."""
    from src.core.guardrails import _build_default_rules

    rules = _build_default_rules()
    rule_names = [r.name for r in rules]
    assert "kids_batch_restricted" in rule_names
    assert "kids_limited_tools" in rule_names
    batch_idx = rule_names.index("kids_batch_restricted")
    limited_idx = rule_names.index("kids_limited_tools")
    assert batch_idx < limited_idx, (
        f"kids_batch_restricted (idx {batch_idx}) must come before "
        f"kids_limited_tools (idx {limited_idx})"
    )
