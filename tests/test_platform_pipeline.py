"""Integration tests for the full HAL Platform pipeline.

Tests the complete flow:
    Platform.discover() -> HAL.add_platform() -> StateStore.update() -> listener fires
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from src.hal.hal import HAL
from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType
from src.hal.platform import Platform
from src.hal.state import StateStore


class MockPlatform(Platform):
    """A simple mock platform for pipeline testing."""

    def __init__(self, name: str, devices: list[DeviceInfo]) -> None:
        self._name = name
        self._devices = devices
        self._event_callback: Callable[[DeviceState], Awaitable[None]] | None = None
        self.started = False
        self.stopped = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return self._name.capitalize()

    @property
    def error_message(self) -> str:
        return f"⚠ {self._name.capitalize()} ist offline."

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def health_check(self) -> bool:
        return True

    async def discover_devices(self) -> list[DeviceInfo]:
        return list(self._devices)

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        return DeviceState(
            device_id=device_id,
            state={"value": "test"},
            last_updated=datetime.now(),
            source=self._name,
        )

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        self._event_callback = callback

    async def emit_event(self, device_id: DeviceId, state_dict: dict) -> None:
        """Helper: emit a state change from this platform."""
        if self._event_callback:
            state = DeviceState(
                device_id=device_id,
                state=state_dict,
                last_updated=datetime.now(),
                source=self._name,
            )
            await self._event_callback(state)


def _make_device(platform_name: str, native_id: str, name: str, room: str) -> DeviceInfo:
    device_id = DeviceId(platform=platform_name, native_id=native_id)
    return DeviceInfo(
        id=device_id,
        name=name,
        device_type=DeviceType.SWITCH,
        room=room,
        platform_data={},
    )


@pytest.fixture
async def state_store(tmp_path: Path):
    store = StateStore(db_path=tmp_path / "test_hal.db")
    await store.start()
    yield store
    await store.stop()


@pytest.fixture
async def hal(state_store: StateStore):
    return HAL(state_store)


@pytest.mark.asyncio
async def test_platform_discover_populates_hal_devices(hal: HAL):
    """HAL.add_platform() discovers devices and makes them accessible."""
    devices = [_make_device("mock", "d1", "Licht", "Wohnzimmer")]
    platform = MockPlatform("mock", devices)

    await hal.add_platform(platform)

    assert hal.device_count == 1
    assert platform.started is True


@pytest.mark.asyncio
async def test_platform_event_reaches_state_store(hal: HAL):
    """Platform emit -> event callback -> StateStore.update() is called."""
    devices = [_make_device("mock", "d1", "Licht", "Wohnzimmer")]
    platform = MockPlatform("mock", devices)

    await hal.add_platform(platform)

    device_id = DeviceId(platform="mock", native_id="d1")
    await platform.emit_event(device_id, {"on": True})

    # StateStore should have the updated state
    raw = await hal.state_store.get_room_state("Wohnzimmer")
    assert "mock:d1" in raw
    assert raw["mock:d1"].get("on") is True


@pytest.mark.asyncio
async def test_state_store_listener_fires_on_platform_event(hal: HAL):
    """StateStore listener is called when platform emits a state change."""
    devices = [_make_device("mock", "d1", "Licht", "Wohnzimmer")]
    platform = MockPlatform("mock", devices)

    await hal.add_platform(platform)

    received: list[tuple] = []

    async def listener(info: DeviceInfo, old_state: Any, new_state: Any, source: str) -> None:
        received.append((info, old_state, new_state, source))

    hal.state_store.on_change(listener)

    device_id = DeviceId(platform="mock", native_id="d1")
    await platform.emit_event(device_id, {"on": True})

    # Give the listener a chance to fire (create_task() needs a loop tick)
    await asyncio.sleep(0)
    assert len(received) == 1
    info, old_state, new_state, source = received[0]
    assert info.name == "Licht"
    assert new_state.get("on") is True


@pytest.mark.asyncio
async def test_multiple_platforms_coexist(hal: HAL):
    """Multiple platforms can be added; devices from all are accessible."""
    devices_a = [_make_device("platform_a", "a1", "Licht A", "Zimmer A")]
    devices_b = [_make_device("platform_b", "b1", "Licht B", "Zimmer B")]
    platform_a = MockPlatform("platform_a", devices_a)
    platform_b = MockPlatform("platform_b", devices_b)

    await hal.add_platform(platform_a)
    await hal.add_platform(platform_b)

    assert hal.device_count == 2
    all_rooms = hal.get_all_rooms()
    assert "Zimmer A" in all_rooms
    assert "Zimmer B" in all_rooms


@pytest.mark.asyncio
async def test_populate_initial_state_reads_from_platform(hal: HAL):
    """populate_initial_state() calls read_state() for each device."""
    devices = [_make_device("mock", "d1", "Licht", "Wohnzimmer")]
    platform = MockPlatform("mock", devices)

    await hal.add_platform(platform)
    await hal.populate_initial_state()

    raw = await hal.state_store.get_room_state("Wohnzimmer")
    assert "mock:d1" in raw
    assert raw["mock:d1"].get("value") == "test"


@pytest.mark.asyncio
async def test_hal_find_room_includes_platform_devices(hal: HAL):
    """hal.find_room() returns devices from all platforms."""
    devices = [_make_device("mock", "d1", "Licht", "Wohnzimmer")]
    platform = MockPlatform("mock", devices)

    await hal.add_platform(platform)

    room_adapters = hal.find_room("Wohnzimmer")
    assert len(room_adapters) == 1
    assert room_adapters[0].info.name == "Licht"


@pytest.mark.asyncio
async def test_listener_receives_source_from_platform(hal: HAL):
    """The listener source param matches the platform name."""
    devices = [_make_device("test_plat", "d1", "Gerät", "Raum")]
    platform = MockPlatform("test_plat", devices)

    await hal.add_platform(platform)

    received_sources: list[str] = []

    async def listener(info: Any, old_state: Any, new_state: Any, source: str) -> None:
        received_sources.append(source)

    hal.state_store.on_change(listener)

    device_id = DeviceId(platform="test_plat", native_id="d1")
    await platform.emit_event(device_id, {"value": 42})

    await asyncio.sleep(0)
    assert received_sources == ["test_plat"]
