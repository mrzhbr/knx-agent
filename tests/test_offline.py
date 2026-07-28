from __future__ import annotations

import pytest  # type: ignore[import-not-found]
from src.agents.offline import try_offline, execute_offline


@pytest.mark.parametrize(
    "text,expected_action",
    [
        ("Licht an im Wohnzimmer", "lights_on"),
        ("Wohnzimmer Licht an", "lights_on"),
        ("Licht aus", "lights_off"),
        ("Alle Lichter aus", "lights_all_off"),
        ("Alles aus", "all_off"),
        ("Jalousie hoch", "blinds_up"),
        ("Küche Jalousien runter", "blinds_down"),
        ("Alle Jalousien hoch", "blinds_all_up"),
        ("Alle Jalousien runter", "blinds_all_down"),
        ("Heizung auf 22 Grad", "heating_set"),
        ("Temperatur auf 20,5°C", "heating_set"),
        ("Wer ist zuhause?", "who_is_home"),
        ("Ist jemand da?", "who_is_home"),
        ("Tür aufmachen", "door_open"),
        ("Status", "house_status"),
        ("Was ist an?", "what_is_on"),
        ("Fenster?", "window_status"),
        ("Alarm Status?", "alarm_status"),
        ("Ich gehe jetzt weg", "leaving"),
        ("Bin gleich zuhause", "arriving_soon"),
    ],
)
def test_pattern_matches(text, expected_action):
    result = try_offline(text)
    assert result is not None, f"Pattern did not match: '{text}'"
    action_type, params = result
    assert action_type == expected_action, (
        f"Expected {expected_action}, got {action_type} for '{text}'"
    )


def test_no_match_for_complex_query():
    result = try_offline("Kannst du mir erklären wie das Heizsystem funktioniert?")
    assert result is None


def test_heating_extracts_temperature():
    result = try_offline("Heizung auf 22,5 Grad")
    assert result is not None
    action, params = result
    assert action == "heating_set"
    assert params.get("temperature") == 22.5


def test_lights_on_extracts_room():
    result = try_offline("Licht an im Wohnzimmer")
    assert result is not None
    action, params = result
    assert action == "lights_on"
    assert "wohnzimmer" in params.get("room", "").lower()


async def test_offline_device_routing_accepts_any_primary_knx_platform():
    result = await execute_offline(
        "lights_on",
        {"room": "Wohnzimmer"},
        {"home_control_available": True},
    )

    assert result == "offline:lights_on:room=Wohnzimmer"


async def test_offline_device_routing_reports_missing_primary_platform():
    result = await execute_offline(
        "lights_on",
        {"room": "Wohnzimmer"},
        {"home_control_available": False},
    )

    assert result == "KNX-Gerätesteuerung nicht verfügbar."


async def test_execute_offline_who_is_home_no_presence():
    result = await execute_offline("who_is_home", {}, {})
    assert "nicht verfügbar" in result.lower()


async def test_execute_offline_door_open_returns_message():
    result = await execute_offline("door_open", {}, {})
    assert "Bestätigung" in result or "erforder" in result.lower()


async def test_execute_offline_unknown_action():
    result = await execute_offline("unknown_xyz", {}, {})
    assert "Unbekannte" in result


# ─── AC pattern tests (Task 12.4) ─────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected_action",
    [
        ("klima an im schlafzimmer", "ac_on"),
        ("klima ein", "ac_on"),
        ("klimaanlage an", "ac_on"),
        ("klima aus", "ac_off"),
        ("klimaanlage ausschalten", "ac_off"),
        ("ac aus", "ac_off"),
        ("klima auf 22", "ac_set_temp"),
        ("klimaanlage auf 22 grad", "ac_set_temp"),
        ("kuehlen person1 room", "ac_cool"),
        ("kühlen im schlafzimmer", "ac_cool"),
    ],
)
def test_ac_pattern_matches(text, expected_action):
    """AC patterns match correctly."""
    result = try_offline(text)
    assert result is not None, f"Pattern did not match: '{text}'"
    action, params = result
    assert action == expected_action, f"Expected {expected_action}, got {action} for '{text}'"


async def test_ac_offline_execute_on():
    """execute_offline for ac_on returns expected marker."""
    result = await execute_offline("ac_on", {"room": "Schlafzimmer"}, {})
    assert "ac_on" in result
    assert "Schlafzimmer" in result


async def test_ac_offline_execute_set_temp():
    """execute_offline for ac_set_temp returns temperature marker."""
    result = await execute_offline("ac_set_temp", {"temperature": 22.0}, {})
    assert "ac_set_temp" in result
    assert "22.0" in result


async def test_ac_pattern_extracts_room():
    """AC on pattern extracts room from message."""
    result = try_offline("klima an im schlafzimmer")
    assert result is not None
    action, params = result
    assert action == "ac_on"
    assert "schlafzimmer" in params.get("room", "").lower()


async def test_ac_set_temp_extracts_temperature():
    """AC set temp pattern extracts temperature value."""
    result = try_offline("klimaanlage auf 22 grad")
    assert result is not None
    action, params = result
    assert action == "ac_set_temp"
    assert params.get("temperature") == 22.0


# ─── Schedule pattern tests (Task 14.5) ─────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected_action",
    [
        ("timer 30 minuten", "schedule_timer"),
        ("timer 5 min", "schedule_timer"),
        ("erinnere mich um 22:00", "schedule_reminder"),
        ("erinnere um 07:30", "schedule_reminder"),
        ("wecker 08:00", "schedule_alarm"),
        ("routine bettfertig", "execute_routine"),
        ("routine guten morgen", "execute_routine"),
    ],
)
def test_schedule_pattern_matches(text, expected_action):
    """Schedule-related patterns match correctly."""
    result = try_offline(text)
    assert result is not None, f"Pattern did not match: '{text}'"
    action_type, params = result
    assert action_type == expected_action, (
        f"Expected {expected_action}, got {action_type} for '{text}'"
    )


def test_timer_extracts_minutes():
    """Timer pattern extracts minute count."""
    result = try_offline("timer 30 minuten")
    assert result is not None
    action, params = result
    assert action == "schedule_timer"
    assert params.get("minutes") == 30


def test_reminder_extracts_time():
    """Reminder pattern extracts HH:MM time."""
    result = try_offline("erinnere mich um 22:00")
    assert result is not None
    action, params = result
    assert action == "schedule_reminder"
    assert params.get("time") == "22:00"


def test_routine_extracts_name():
    """Routine pattern extracts routine name."""
    result = try_offline("routine bettfertig")
    assert result is not None
    action, params = result
    assert action == "execute_routine"
    assert "bettfertig" in params.get("name", "").lower()


async def test_execute_offline_schedule_timer_returns_online_only():
    """Timer offline action returns 'online only' message."""
    result = await execute_offline("schedule_timer", {"minutes": 30}, {})
    assert "online" in result.lower() or "können nur" in result.lower()


async def test_execute_offline_schedule_reminder_returns_online_only():
    """Reminder offline action returns 'online only' message."""
    result = await execute_offline("schedule_reminder", {"time": "22:00"}, {})
    assert "online" in result.lower() or "können nur" in result.lower()


async def test_execute_offline_schedule_alarm_returns_online_only():
    """Alarm offline action returns 'online only' message."""
    result = await execute_offline("schedule_alarm", {"time": "08:00"}, {})
    assert "online" in result.lower() or "können nur" in result.lower()


async def test_execute_offline_execute_routine_calls_schedule_adapter():
    """execute_offline for execute_routine calls adapters['schedule'].execute_routine()."""
    from unittest.mock import AsyncMock, MagicMock

    mock_engine = MagicMock()
    mock_engine.execute_routine = AsyncMock(return_value="Bettfertig ausgeführt.")

    adapters = {"schedule": mock_engine}
    result = await execute_offline("execute_routine", {"name": "bettfertig"}, adapters)

    mock_engine.execute_routine.assert_called_once_with("bettfertig")
    assert "bettfertig" in result.lower() or "ausgeführt" in result.lower()


async def test_execute_offline_execute_routine_no_schedule_adapter():
    """execute_offline returns error when schedule adapter not in adapters."""
    result = await execute_offline("execute_routine", {"name": "test"}, {})
    assert "verfügbar" in result.lower() or "nicht" in result.lower()


def test_bettfertig_no_longer_matches_offline():
    """After scene pattern removal, 'Bettfertig' should NOT match any offline pattern."""
    result = try_offline("Bettfertig")
    assert result is None, f"'Bettfertig' should not match offline patterns, got: {result}"


def test_execute_routine_still_matches():
    """'routine X ausfuehren' should still match execute_routine pattern."""
    result = try_offline("routine bettfertig ausführen")
    assert result is not None, "execute_routine pattern should still match"
    action_type, params = result
    assert action_type == "execute_routine"
