from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from src.hal.hal import HAL
    from src.hal.state import StateStore

logger = logging.getLogger(__name__)

# Power estimates in watts per device type (for energy monitoring)
POWER_ESTIMATES_W: dict[str, float] = {
    "dimmable_light": 12.0,  # LED, avg per fixture
    "switch": 5.0,  # misc switched loads
    "blind": 8.0,  # motor draw while moving
    "heating": 1500.0,  # electric heating element
    "sauna": 6000.0,  # sauna heater
}

LOCAL_TZ = ZoneInfo("Europe/Berlin")


# DEPRECATED: use StateStore.get_recent_events() instead
@dataclass
class StateEvent:
    """A single state change recorded in the house awareness stream."""

    timestamp: datetime
    room: str
    device: str
    old_value: str | None
    new_value: str
    source: str  # "brain" or "external"
    category: str  # device type: "light", "blind", "heating", "sensor", etc.


class HouseAwareness:
    """Thin async query layer over StateStore for house state history.

    Reads event history from StateStore. No in-memory deque.
    format_recent() and get_recent_events() are async.
    """

    def __init__(self, state_store: "StateStore") -> None:
        self._state_store = state_store

    async def format_recent(self, minutes: int = 30) -> str:
        """Format recent events as a German-language list for the LLM context."""
        events = await self._state_store.get_recent_events(minutes=minutes)
        if not events:
            return "Keine Änderungen in den letzten 30 Minuten."
        lines = []
        for event in events:
            ts = event["timestamp"]
            # Convert UTC timestamps to Europe/Berlin local time
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts).astimezone(LOCAL_TZ)
                time_str = dt.strftime("%H:%M")
            else:
                time_str = ts.astimezone(LOCAL_TZ).strftime("%H:%M")
            room = event.get("room", "")
            device = event.get("device_name", "")
            source = event.get("source", "external")
            src_str = "" if source in ("external", "x1") else f" [{source}]"
            # Format new state as summary
            new_state = event.get("new_state", {})
            state_str = ", ".join(f"{k}={v}" for k, v in new_state.items())
            lines.append(f"{time_str} -- {room}/{device}: {state_str}{src_str}")
        return "\n".join(lines)

    async def get_recent_events(self, minutes: int = 120) -> list[dict]:
        """Return events from the last N minutes (time-filtered).

        Returns list[dict] in StateStore format (not StateEvent objects).
        """
        return await self._state_store.get_recent_events(minutes=minutes)


class EnergyAwareness:
    """Tracks estimated energy consumption based on device states.

    Registers as a StateStore listener and updates estimated watts
    from device type + on/off state. Not a billing-grade meter.
    """

    def __init__(self, state_store: "StateStore", hal: "HAL") -> None:
        self._state_store = state_store
        self._hal = hal
        self._active_devices: dict[str, tuple[str, float]] = {}  # key -> (name, estimated watts)
        self._real_power: dict[str, tuple[str, float]] = {}  # device_id -> (name, actual watts)
        state_store.on_change(self._on_state_change)

    async def _on_state_change(self, info, old_state, new_state, source) -> None:
        """Update energy tracking when a device state changes."""
        from src.hal.rules import is_light_on

        device_type = info.device_type.value  # StrEnum.value, NOT str() which adds class prefix
        watts = POWER_ESTIMATES_W.get(device_type, 0.0)
        if watts > 0:
            key = info.id.key
            is_on = is_light_on(new_state)  # works for lights
            if device_type == "heating":
                from src.hal.rules import is_heating_active

                is_on = is_heating_active(new_state)
            if is_on:
                self._active_devices[key] = (info.name, watts)
            else:
                self._active_devices.pop(key, None)

    def format_for_prompt(self) -> str:
        """Return German-language energy summary for LLM context."""
        if not self._active_devices and not self._real_power:
            return "Keine aktiven Verbraucher erkannt."

        estimated = sum(w for _, w in self._active_devices.values())
        real = sum(w for _, w in self._real_power.values())
        total_w = estimated + real
        lines: list[str] = [f"Gesamtverbrauch: ~{total_w:.0f} W"]
        for uid, (name, watts) in self._active_devices.items():
            if watts > 0:
                lines.append(f"  {name}: ~{watts:.0f} W (Schätzung)")
        for device_id, (name, watts) in self._real_power.items():
            if watts > 0:
                lines.append(f"  {name}: {watts:.0f} W (Messung)")
        return "\n".join(lines)

    @property
    def estimated_watts(self) -> float:
        """Total estimated power consumption in watts (estimated + real readings)."""
        estimated = sum(w for _, w in self._active_devices.values())
        real = sum(w for _, w in self._real_power.values())
        return estimated + real

    # ── Real power readings (Task 12.2) ────────────────────────────────────

    def update_real_power(self, device_id: str, device_name: str, watts: float) -> None:
        """Update real power reading for a device (overrides estimates).

        Used by AC platform adapters to report actual power draw.

        Args:
            device_id: Unique device identifier (e.g., "ac_living_room")
            device_name: Human-readable device name for display
            watts: Actual power draw in watts
        """
        self._real_power[device_id] = (device_name, watts)
        logger.debug("Real power update: %s = %.0fW", device_id, watts)
