from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest  # type: ignore[import-not-found]

from src.integrations.owntracks import OwnTracksAdapter, _haversine


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_adapter(**kwargs) -> OwnTracksAdapter:
    defaults: dict[str, Any] = dict(
        mqtt_host="localhost",
        mqtt_port=1883,
        topic_prefix="owntracks",
        home_lat=52.5200,
        home_lon=13.4050,
        geofence_radius_m=150,
        tracked_users={"person1/iphone": "person1_phone", "person2/iphone": "person2_phone"},
        stale_timeout_minutes=30,
        home_region="Home",
    )
    defaults.update(kwargs)
    return OwnTracksAdapter(**defaults)


def transition_payload(event: str, desc: str = "Home") -> bytes:
    import json

    return json.dumps({"_type": "transition", "event": event, "desc": desc}).encode()


def location_payload(lat: float, lon: float) -> bytes:
    import json

    return json.dumps({"_type": "location", "lat": lat, "lon": lon}).encode()


# ── Unit Tests ────────────────────────────────────────────────────────────────


def test_nobody_home_initially():
    """Fresh adapter treats unseen tracked devices as unknown, not away."""
    adapter = make_adapter()
    assert not adapter.nobody_home()
    assert adapter.who_is_home() == {}


async def test_handle_transition_enter():
    """Transition enter event for home region marks device as home."""
    adapter = make_adapter()
    await adapter._on_message("owntracks/person1/iphone", transition_payload("enter", "Home"))
    assert adapter.who_is_home().get("person1_phone")


async def test_handle_transition_leave():
    """Transition leave event marks device as away."""
    adapter = make_adapter()
    adapter._presence["person1_phone"] = True  # Start home
    await adapter._on_message("owntracks/person1/iphone", transition_payload("leave", "Home"))
    assert not adapter.who_is_home().get("person1_phone")


async def test_handle_location_inside_radius():
    """Location update inside geofence marks device as home."""
    adapter = make_adapter(home_lat=52.5200, home_lon=13.4050, geofence_radius_m=150)
    # Same coords as home — distance 0
    await adapter._on_message("owntracks/person1/iphone", location_payload(52.5200, 13.4050))
    assert adapter.who_is_home().get("person1_phone")


async def test_handle_location_outside_radius():
    """Location update far from home marks device as away."""
    adapter = make_adapter(home_lat=52.5200, home_lon=13.4050, geofence_radius_m=150)
    # Frankfurt (approx 70 km away)
    await adapter._on_message("owntracks/person1/iphone", location_payload(53.5, 14.5))
    assert not adapter.who_is_home().get("person1_phone")


async def test_location_hysteresis_keeps_home_within_band():
    """Once marked home via transition, a location just outside the radius keeps state."""
    adapter = make_adapter(home_lat=52.5200, home_lon=13.4050, geofence_radius_m=150)
    await adapter._on_message("owntracks/person1/iphone", transition_payload("enter", "Home"))
    assert adapter.who_is_home().get("person1_phone")
    # Location ~180 m away (within 1.5x hysteresis band of 150 m radius) — must stay home.
    await adapter._on_message("owntracks/person1/iphone", location_payload(52.5206, 13.4050))
    assert adapter.who_is_home().get("person1_phone")


async def test_location_hysteresis_flips_away_when_clearly_outside():
    """A location well beyond radius * 1.5 flips a home device to away."""
    adapter = make_adapter(home_lat=52.5200, home_lon=13.4050, geofence_radius_m=150)
    await adapter._on_message("owntracks/person1/iphone", transition_payload("enter", "Home"))
    # Frankfurt — clearly outside any reasonable hysteresis band.
    await adapter._on_message("owntracks/person1/iphone", location_payload(53.5, 14.5))
    assert not adapter.who_is_home().get("person1_phone")


async def test_handle_transition_wrong_region():
    """Transition enter for non-home region is ignored."""
    adapter = make_adapter()
    await adapter._on_message("owntracks/person1/iphone", transition_payload("enter", "Work"))
    # Should not be marked home
    assert adapter.who_is_home().get("person1_phone") is None


async def test_handle_transition_leave_wrong_region():
    """Leave event for non-home region must NOT flip device to away.

    A user who has multiple OwnTracks waypoints (Work, Gym, etc.) can leave
    those regions while still inside the home geofence — leaves outside the
    home region must be ignored.
    """
    adapter = make_adapter()
    adapter._presence["person1_phone"] = True  # currently home
    await adapter._on_message("owntracks/person1/iphone", transition_payload("leave", "Work"))
    assert adapter.who_is_home().get("person1_phone")


async def test_fire_change_calls_callback():
    """Registered callback is invoked when presence changes."""
    adapter = make_adapter()
    called_with: list[tuple[str, bool]] = []

    async def my_callback(device_id: str, is_home: bool) -> None:
        called_with.append((device_id, is_home))

    adapter.on_change(my_callback)
    await adapter._on_message("owntracks/person1/iphone", transition_payload("enter", "Home"))

    assert len(called_with) == 1
    device_id, is_home = called_with[0]
    assert device_id == "person1_phone"
    assert is_home


async def test_fire_change_not_called_on_same_state():
    """Callback is NOT called if presence state does not change."""
    adapter = make_adapter()
    adapter._presence["person1_phone"] = True  # Already home
    called_with: list[tuple[str, bool]] = []

    async def my_callback(device_id: str, is_home: bool) -> None:
        called_with.append((device_id, is_home))

    adapter.on_change(my_callback)
    # Enter again — same state
    await adapter._on_message("owntracks/person1/iphone", transition_payload("enter", "Home"))
    assert called_with == []


async def test_unknown_type_ignored():
    """Unknown _type (e.g. 'waypoint') does not crash or change state."""
    import json

    adapter = make_adapter()
    payload = json.dumps({"_type": "waypoint", "desc": "Work"}).encode()
    await adapter._on_message("owntracks/person1/iphone", payload)
    assert adapter.who_is_home() == {}  # No state change


async def test_malformed_json_ignored():
    """Non-JSON payload logs warning and does not crash."""
    adapter = make_adapter()
    await adapter._on_message("owntracks/person1/iphone", b"not-json{{{")
    # No exception, state unchanged
    assert adapter.who_is_home() == {}


async def test_unmapped_user_ignored():
    """Message from unknown user/device topic is silently ignored."""
    adapter = make_adapter()
    await adapter._on_message("owntracks/unknown/device", transition_payload("enter", "Home"))
    assert adapter.who_is_home() == {}


def test_haversine_basic():
    """Haversine returns 0 for identical coords and reasonable distance for known points."""
    # Same point
    assert _haversine(52.5200, 13.4050, 52.5200, 13.4050) == pytest.approx(0.0, abs=0.01)
    # Two public example points are roughly 131 km apart.
    dist = _haversine(52.5200, 13.4050, 53.5, 14.5)
    assert 120_000 < dist < 140_000, f"Expected ~131km, got {dist:.0f}m"


def test_format_for_prompt_german():
    """format_for_prompt returns German summary of presence state."""
    adapter = make_adapter()
    adapter._presence["person1_phone"] = True
    adapter._presence["person2_phone"] = False
    now = datetime.now(timezone.utc)
    adapter._last_seen["person1_phone"] = now
    adapter._last_seen["person2_phone"] = now
    result = adapter.format_for_prompt()
    assert "Zuhause" in result
    assert "person1_phone" in result
    assert "Abwesend" in result
    assert "person2_phone" in result


def test_format_for_prompt_no_devices():
    """Empty tracked_users returns 'Keine Geräte konfiguriert.'"""
    adapter = make_adapter(tracked_users={})
    result = adapter.format_for_prompt()
    assert "Keine" in result


def test_format_for_prompt_no_data_yet():
    """Configured users but no MQTT data yet returns the 'no data' phrasing.

    Distinct from 'no devices configured' so the prompt isn't misleading right
    after a brain restart before the first OwnTracks message arrives.
    """
    adapter = make_adapter()  # tracked_users populated, _presence empty
    result = adapter.format_for_prompt()
    assert "noch keine frischen Daten" in result
    assert "Keine Geräte" not in result


def test_haversine_clamps_floating_point_drift():
    """Antipodal points must not crash sqrt due to FP rounding (a clamped to [0,1])."""
    # Antipodal pair — `a` ≈ 1 ± epsilon depending on rounding.
    dist = _haversine(0.0, 0.0, 0.0, 180.0)
    # Half the Earth's circumference, ~20015 km.
    assert 19_000_000 < dist < 21_000_000


async def test_stale_timeout_becomes_unknown_not_away():
    """A stale device becomes unknown and cannot satisfy nobody_home()."""
    adapter = make_adapter(stale_timeout_minutes=30)
    adapter._presence["person1_phone"] = True
    # Set last_seen to 31 minutes ago
    adapter._last_seen["person1_phone"] = datetime.now(timezone.utc) - timedelta(minutes=31)

    await adapter._check_stale()

    assert adapter.presence_snapshot()["person1_phone"] is None
    assert "person1_phone" not in adapter.who_is_home()
    assert not adapter.nobody_home()


async def test_stale_timeout_no_change_if_not_home():
    """Stale timeout does not fire callback if device was already away."""
    adapter = make_adapter(stale_timeout_minutes=30)
    adapter._presence["person1_phone"] = False
    adapter._last_seen["person1_phone"] = datetime.now(timezone.utc) - timedelta(minutes=31)
    called: list[bool] = []

    async def cb(device_id: str, is_home: bool) -> None:
        called.append(is_home)

    adapter.on_change(cb)
    await adapter._check_stale()

    assert called == []  # No callback fired
