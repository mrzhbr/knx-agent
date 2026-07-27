from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.tools.presence import get_window_states, set_context, who_is_home
import src.tools.presence as presence_module
from src.tools.context import ToolContext
from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass


def _make_device_id(native_id: str) -> DeviceId:
    return DeviceId(platform="mock", native_id=native_id)


def _make_window_adapter(native_id: str, name: str, room: str, is_open: bool) -> MagicMock:
    adapter = MagicMock()
    adapter.device_id = _make_device_id(native_id)
    adapter.info = DeviceInfo(
        id=_make_device_id(native_id),
        name=name,
        device_type=DeviceType.BINARY_SENSOR,
        room=room,
        sensor_class=SensorClass.WINDOW,
    )
    adapter.read_state = AsyncMock(
        return_value=DeviceState(
            device_id=_make_device_id(native_id),
            state={"Binary": "1" if is_open else "0"},
            last_updated=datetime.now(timezone.utc),
        )
    )
    return adapter


class MockPresenceManager:
    def format_for_prompt(self) -> str:
        return "Zuhause: Max\nAlle Fenster/Türen geschlossen."

    @property
    def open_windows(self) -> list[str]:
        return ["Schlafzimmer"]


class MockHAL:
    def __init__(self, has_open: bool = True) -> None:
        if has_open:
            self._sensors = [
                _make_window_adapter("w-1", "Schlafzimmer Fenster", "Schlafzimmer", True),
                _make_window_adapter("w-2", "Wohnzimmer Fenster", "Wohnzimmer", False),
            ]
        else:
            self._sensors = [
                _make_window_adapter("w-1", "Küche Fenster", "Küche", False),
            ]

    def get_sensors_by_class(self, sensor_class: SensorClass) -> list:
        if sensor_class == SensorClass.WINDOW:
            return self._sensors
        return []


def _make_ctx(presence=None, hal=None) -> ToolContext:
    return ToolContext(
        hal=hal,
        presence_manager=presence or MockPresenceManager(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )


async def test_who_is_home_with_presence():
    set_context(_make_ctx(presence=MockPresenceManager()))
    result = await who_is_home()
    assert "Max" in result


async def test_who_is_home_no_presence():
    presence_module._ctx = None
    result = await who_is_home()
    assert "nicht verfügbar" in result


async def test_get_window_states_fallback_to_presence():
    """Without HAL, falls back to presence.open_windows."""
    set_context(_make_ctx(presence=MockPresenceManager(), hal=None))
    result = await get_window_states()
    assert "Schlafzimmer" in result or "geschlossen" in result.lower()


async def test_get_window_states_with_hal():
    """With HAL, reads window sensor states via adapter.read_state()."""
    hal = MockHAL(has_open=True)
    set_context(_make_ctx(presence=MockPresenceManager(), hal=hal))
    result = await get_window_states()
    assert "Schlafzimmer" in result
    assert "OFFEN" in result or "offen" in result.lower()


async def test_get_window_states_all_closed():
    hal = MockHAL(has_open=False)
    set_context(_make_ctx(presence=MockPresenceManager(), hal=hal))
    result = await get_window_states()
    assert "geschlossen" in result.lower() or "Küche" in result


async def test_get_window_states_no_sensors():
    class EmptyHAL:
        def get_sensors_by_class(self, sensor_class):
            return []

    set_context(_make_ctx(presence=MockPresenceManager(), hal=EmptyHAL()))
    result = await get_window_states()
    assert "konfiguriert" in result.lower() or "Sensoren" in result
