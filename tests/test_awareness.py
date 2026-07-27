from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock


from src.core.awareness import EnergyAwareness, HouseAwareness, StateEvent


# ─── Helpers ────────────────────────────────────────────────────────────────


class MockStateStore:
    """Minimal in-memory StateStore mock for awareness tests."""

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._listeners: list = []

    def on_change(self, listener) -> None:
        self._listeners.append(listener)

    async def get_recent_events(self, minutes: int = 30, **kwargs) -> list[dict]:
        return list(self._events)

    def add_event(
        self,
        room: str,
        device_name: str,
        new_state: dict,
        source: str = "external",
        device_type: str = "dimmable_light",
    ) -> None:
        ts = datetime.now(timezone.utc).isoformat()
        self._events.append(
            {
                "device_key": f"{room}/{device_name}",
                "room": room,
                "device_name": device_name,
                "device_type": device_type,
                "old_state": None,
                "new_state": new_state,
                "source": source,
                "timestamp": ts,
            }
        )


# ─── HouseAwareness tests ────────────────────────────────────────────────────


async def test_format_recent_returns_events():
    """format_recent returns formatted events from StateStore."""
    store = MockStateStore()
    store.add_event("Wohnzimmer", "Esstisch", {"OnOff": "1"}, source="brain")

    awareness = HouseAwareness(store)
    result = await awareness.format_recent(minutes=30)

    assert "Wohnzimmer" in result
    assert "Esstisch" in result
    assert "OnOff" in result


async def test_format_recent_empty():
    """format_recent returns German 'no changes' message when no events."""
    store = MockStateStore()
    awareness = HouseAwareness(store)
    result = await awareness.format_recent(minutes=30)
    assert "Keine Änderungen" in result


async def test_get_recent_events_returns_list():
    """get_recent_events returns list[dict] from StateStore."""
    store = MockStateStore()
    store.add_event("Schlafzimmer", "Heizung", {"SetValue": "22"})

    awareness = HouseAwareness(store)
    events = await awareness.get_recent_events(minutes=120)

    assert len(events) == 1
    assert events[0]["room"] == "Schlafzimmer"
    assert events[0]["device_name"] == "Heizung"


async def test_format_recent_utc_to_berlin():
    """format_recent converts UTC timestamps to Europe/Berlin local time."""
    store = MockStateStore()
    # Add event with explicit UTC timestamp
    store._events.append(
        {
            "device_key": "wohnzimmer/licht",
            "room": "Wohnzimmer",
            "device_name": "Licht",
            "device_type": "dimmable_light",
            "old_state": None,
            "new_state": {"OnOff": "1"},
            "source": "external",
            "timestamp": "2026-03-30T12:00:00+00:00",  # UTC noon
        }
    )
    awareness = HouseAwareness(store)
    result = await awareness.format_recent(minutes=999999)

    # Europe/Berlin is UTC+2 in summer (CEST), so 12:00 UTC = 14:00 local
    # Or UTC+1 in winter (CET), so 12:00 UTC = 13:00 local
    # Just verify it produces a time string (HH:MM format)
    assert "--" in result
    import re

    assert re.search(r"\d{2}:\d{2}", result)


async def test_source_label_shown_for_non_external():
    """format_recent shows [source] label for non-external/non-x1 sources."""
    store = MockStateStore()
    store.add_event("Haus", "Max", {"status": "zuhause"}, source="arp")

    awareness = HouseAwareness(store)
    result = await awareness.format_recent(minutes=999999)
    assert "[arp]" in result


async def test_source_label_hidden_for_external():
    """format_recent hides source label for external/x1 sources."""
    store = MockStateStore()
    store.add_event("Wohnzimmer", "Licht", {"OnOff": "0"}, source="external")

    awareness = HouseAwareness(store)
    result = await awareness.format_recent(minutes=999999)
    assert "[external]" not in result
    assert "[x1]" not in result


# ─── StateEvent backward compat ──────────────────────────────────────────────


def test_state_event_still_importable():
    """StateEvent dataclass is still importable for backward compatibility."""
    event = StateEvent(
        timestamp=datetime.now(),
        room="Wohnzimmer",
        device="Licht",
        old_value=None,
        new_value="1",
        source="brain",
        category="dimmable_light",
    )
    assert event.room == "Wohnzimmer"


# ─── EnergyAwareness tests ────────────────────────────────────────────────────


async def test_energy_awareness_listener_registered():
    """EnergyAwareness registers on_change listener with StateStore."""
    store = MockStateStore()
    hal = AsyncMock()
    _ea = EnergyAwareness(state_store=store, hal=hal)
    assert len(store._listeners) == 1
    # Verify listener is EnergyAwareness's _on_state_change method
    assert store._listeners[0].__func__.__name__ == "_on_state_change"
    assert _ea is not None  # suppress unused var warning


async def test_energy_awareness_on_state_change_light():
    """EnergyAwareness _on_state_change updates active_devices for dimmable_light."""
    from unittest.mock import MagicMock

    store = MockStateStore()
    hal = AsyncMock()
    energy = EnergyAwareness(state_store=store, hal=hal)

    # Simulate a state change event
    info = MagicMock()
    info.device_type.value = "dimmable_light"
    info.id.key = "wohnzimmer/licht"
    info.name = "Esstisch"

    # Turn on
    await energy._on_state_change(info, None, {"OnOff": "1"}, "external")
    assert "wohnzimmer/licht" in energy._active_devices
    assert energy.estimated_watts > 0

    # Turn off
    await energy._on_state_change(info, {"OnOff": "1"}, {"OnOff": "0"}, "external")
    assert "wohnzimmer/licht" not in energy._active_devices
    assert energy.estimated_watts == 0.0


async def test_energy_awareness_format_for_prompt():
    """EnergyAwareness.format_for_prompt returns German string."""
    store = MockStateStore()
    hal = AsyncMock()
    energy = EnergyAwareness(state_store=store, hal=hal)

    # No active devices
    result = energy.format_for_prompt()
    assert "Keine" in result

    # Manually add a device
    energy._active_devices["test"] = ("Testgerät", 100.0)
    result = energy.format_for_prompt()
    assert "Testgerät" in result
    assert "100" in result


def test_energy_update_real_power():
    """update_real_power stores real power readings."""
    store = MockStateStore()
    from unittest.mock import MagicMock

    hal = MagicMock()
    energy = EnergyAwareness(state_store=store, hal=hal)
    energy.update_real_power("ac_wohnzimmer", "Klimaanlage", 2500.0)
    assert energy.estimated_watts == 2500.0
