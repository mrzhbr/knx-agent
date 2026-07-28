"""Contract tests for the native KNX/IP HAL platform."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import timezone
from typing import Any

import pytest  # type: ignore[import-not-found]

from src.config import KNXIPConfig
from src.hal.models import DeviceId, DeviceState, DeviceType, SensorClass
from src.hal.platforms.knx_ip import KNXIPPlatform  # type: ignore[import-not-found]
from src.hal.platforms.knx_ip_registry import (  # type: ignore[import-not-found]
    KNXDeviceRegistry,
    KNXMapError,
)
from src.integrations.knx_ip import KNXConnectionError


class _FakeAdapter:
    def __init__(self, *, start_error: Exception | None = None) -> None:
        self.start_error = start_error
        self.started = False
        self.start_count = 0
        self.stopped = False
        self.connected = False
        self.address_dpts: dict[str, str] = {}
        self.update_callback: Callable[[str, object], Awaitable[None]] | None = None
        self.writes: list[tuple[str, object, str]] = []
        self.read_values: dict[str, object | Exception | None] = {}
        self.read_calls: list[str] = []

    async def start(self) -> None:
        self.started = True
        self.start_count += 1
        if self.start_error is not None:
            raise self.start_error
        self.connected = True

    async def stop(self) -> None:
        self.stopped = True
        self.connected = False

    async def health_check(self) -> bool:
        return self.connected

    async def write(self, address: str, value: object, dpt: str) -> None:
        if not self.connected:
            raise RuntimeError("KNX/IP is not connected")
        self.writes.append((address, value, dpt))

    async def read(self, address: str, dpt: str, *, timeout: float) -> object | None:
        del dpt, timeout
        self.read_calls.append(address)
        value = self.read_values.get(address)
        if isinstance(value, Exception):
            raise value
        return value

    def set_update_callback(
        self,
        callback: Callable[[str, object], Awaitable[None]] | None,
    ) -> None:
        self.update_callback = callback

    async def emit(self, address: str, value: object) -> None:
        if self.update_callback is not None:
            await self.update_callback(address, value)


def _registry() -> KNXDeviceRegistry:
    return KNXDeviceRegistry.from_dict(
        {
            "schema_version": 1,
            "devices": {
                "ceiling": {
                    "name": "Ceiling",
                    "room": "Living room",
                    "floor": "Ground floor",
                    "type": "dimmable_light",
                    "addresses": {
                        "switch": "1/0/1",
                        "switch_state": "1/1/1",
                        "brightness": "1/0/2",
                        "brightness_state": "1/1/2",
                    },
                    "dpts": {},
                },
                "blind": {
                    "name": "Window blind",
                    "room": "Living room",
                    "type": "blind",
                    "addresses": {
                        "move": "1/0/3",
                        "move_state": "1/1/3",
                        "stop": "1/0/4",
                    },
                },
                "thermostat": {
                    "name": "Thermostat",
                    "room": "Living room",
                    "type": "heating",
                    "addresses": {
                        "setpoint": "1/0/5",
                        "setpoint_state": "1/1/5",
                        "temperature_state": "1/1/6",
                        "switch": "1/0/7",
                        "switch_state": "1/1/7",
                    },
                },
                "window": {
                    "name": "Window",
                    "room": "Living room",
                    "type": "binary_sensor",
                    "sensor_class": "window",
                    "addresses": {"value_state": "2/1/1"},
                    "dpts": {"value_state": "1.019"},
                },
                "temperature": {
                    "name": "Room temperature",
                    "room": "Living room",
                    "type": "temperature_sensor",
                    "addresses": {"temperature_state": "2/2/1"},
                },
                "evening": {
                    "name": "Evening",
                    "room": "Living room",
                    "type": "scene",
                    "scene_number": 4,
                    "addresses": {"scene": "3/0/1"},
                },
            },
        }
    )


def _platform(
    fake: _FakeAdapter | None = None,
    registry: KNXDeviceRegistry | None = None,
) -> tuple[KNXIPPlatform, _FakeAdapter]:
    adapter = fake or _FakeAdapter()
    mapped_devices = registry or _registry()

    def adapter_factory(config: KNXIPConfig, **kwargs: Any) -> _FakeAdapter:
        del config
        adapter.address_dpts = dict(kwargs["address_dpts"])
        adapter.set_update_callback(kwargs["update_callback"])
        return adapter

    platform = KNXIPPlatform(
        KNXIPConfig(),
        registry_loader=lambda _: mapped_devices,
        adapter_factory=adapter_factory,
    )
    return platform, adapter


def test_platform_is_registered_for_configuration_loading():
    from src.hal.platforms import PLATFORM_REGISTRY

    assert PLATFORM_REGISTRY["knx_ip"] is KNXIPPlatform


async def test_platform_discovers_only_authoritative_mapped_devices():
    platform, adapter = _platform()
    await platform.start()

    devices = await platform.discover_devices()

    assert adapter.started
    assert len(devices) == 6
    ceiling = next(device for device in devices if device.id.native_id == "ceiling")
    assert ceiling.id.platform == "knx_ip"
    assert ceiling.device_type is DeviceType.DIMMABLE_LIGHT
    assert ceiling.room == "Living room"
    assert ceiling.floor == "Ground floor"
    assert "Brightness" in ceiling.capabilities
    assert not any("1/" in str(value) for value in ceiling.platform_data.values())


async def test_platform_preserves_binary_sensor_class():
    platform, _ = _platform()
    await platform.start()

    devices = await platform.discover_devices()
    window = next(device for device in devices if device.id.native_id == "window")

    assert window.sensor_class is SensorClass.WINDOW


@pytest.mark.parametrize(
    ("device", "command", "params", "expected"),
    [
        ("ceiling", "turn_on", {}, ("1/0/1", True, "1.001")),
        ("ceiling", "turn_off", {}, ("1/0/1", False, "1.001")),
        ("ceiling", "set_brightness", {"value": 45}, ("1/0/2", 45, "5.001")),
        ("blind", "open", {}, ("1/0/3", False, "1.008")),
        ("blind", "close", {}, ("1/0/3", True, "1.008")),
        ("blind", "stop", {}, ("1/0/4", True, "1.007")),
        ("thermostat", "set_temperature", {"value": 21.5}, ("1/0/5", 21.5, "9.001")),
        ("thermostat", "turn_on", {}, ("1/0/7", True, "1.001")),
        ("thermostat", "turn_off", {}, ("1/0/7", False, "1.001")),
        ("evening", "trigger", {}, ("3/0/1", 4, "17.001")),
    ],
)
async def test_platform_translates_hal_commands(
    device: str,
    command: str,
    params: dict[str, object],
    expected: tuple[str, object, str],
):
    platform, adapter = _platform()
    await platform.start()

    await platform.send_command(DeviceId(platform="knx_ip", native_id=device), command, **params)

    assert adapter.writes[-1] == expected


async def test_set_binary_parses_disarm_string_as_false():
    registry = KNXDeviceRegistry.from_dict(
        {
            "schema_version": 1,
            "devices": {
                "alarm": {
                    "name": "Alarm",
                    "room": "Utility room",
                    "type": "switch",
                    "sensor_class": "alarm_readiness",
                    "addresses": {"switch": "4/0/1", "switch_state": "4/1/1"},
                }
            },
        }
    )
    platform, adapter = _platform(registry=registry)
    await platform.start()

    await platform.send_command(
        DeviceId(platform="knx_ip", native_id="alarm"),
        "set_binary",
        value="0",
    )

    _address, value, _dpt = adapter.writes[-1]
    assert not bool(value)

    adapter.writes.clear()
    with pytest.raises(ValueError, match="brightness"):
        await platform.send_command(
            DeviceId(platform="knx_ip", native_id="alarm"),
            "turn_on",
            brightness=50,
        )
    assert not adapter.writes


async def test_scene_control_dpt_uses_activate_payload():
    registry = _registry()
    scene = registry.get("evening")
    assert scene is not None
    scene.dpts["scene"] = "18.001"
    platform, adapter = _platform(registry=registry)
    await platform.start()

    await platform.send_command(
        DeviceId(platform="knx_ip", native_id="evening"),
        "trigger",
    )

    address, value, dpt = adapter.writes[-1]
    assert address == "3/0/1"
    assert value == {"scene_number": 4, "learn": False}
    assert dpt == "18.001"


async def test_turn_on_with_brightness_writes_switch_then_level():
    platform, adapter = _platform()
    await platform.start()

    await platform.send_command(
        DeviceId(platform="knx_ip", native_id="ceiling"),
        "turn_on",
        brightness=60,
    )

    assert len(adapter.writes) == 2
    switch_write, brightness_write = adapter.writes
    assert switch_write[0] == "1/0/1"
    assert bool(switch_write[1])
    assert switch_write[2] == "1.001"
    assert brightness_write[0] == "1/0/2"
    assert brightness_write[1] == 60
    assert brightness_write[2] == "5.001"


async def test_platform_rejects_invalid_or_unsupported_commands():
    platform, adapter = _platform()
    await platform.start()

    with pytest.raises(ValueError, match="brightness"):
        await platform.send_command(
            DeviceId(platform="knx_ip", native_id="ceiling"),
            "turn_on",
            brightness=101,
        )
    assert not adapter.writes
    with pytest.raises(ValueError, match="Unsupported command"):
        await platform.send_command(
            DeviceId(platform="knx_ip", native_id="window"),
            "turn_on",
        )


async def test_initial_sync_reads_only_status_addresses_and_read_state_uses_cache():
    platform, adapter = _platform()
    adapter.read_values.update({"1/1/1": True, "1/1/2": 55})
    await platform.start()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    before = len(adapter.read_calls)
    state = await platform.read_state(DeviceId(platform="knx_ip", native_id="ceiling"))

    assert "1/0/1" not in adapter.read_calls
    assert "1/0/2" not in adapter.read_calls
    assert "1/1/1" in adapter.read_calls
    assert "1/1/2" in adapter.read_calls
    assert len(adapter.read_calls) == before
    assert state.state["OnOff"]
    assert state.state["Brightness"] == 55


async def test_read_state_uses_status_addresses_and_keeps_failed_values_unknown():
    platform, adapter = _platform()
    adapter.read_values.update({"2/1/1": OSError("timeout")})
    await platform.start()

    state = await platform.read_state(DeviceId(platform="knx_ip", native_id="window"))

    assert state.state == {"Binary": None}
    assert state.last_updated.tzinfo is timezone.utc


async def test_command_telegram_is_not_promoted_to_authoritative_device_state():
    platform, adapter = _platform()
    updates: list[DeviceState] = []

    async def on_update(state: DeviceState) -> None:
        updates.append(state)

    await platform.start()
    await platform.subscribe_events(on_update)

    await adapter.emit("1/0/2", 80)

    assert not updates
    state = await platform.read_state(DeviceId(platform="knx_ip", native_id="ceiling"))
    assert state.state["Brightness"] is None


async def test_telegram_update_publishes_canonical_device_state():
    platform, adapter = _platform()
    updates: list[DeviceState] = []

    async def on_update(state: DeviceState) -> None:
        updates.append(state)

    await platform.start()
    await platform.subscribe_events(on_update)

    await adapter.emit("1/1/2", 55)
    await adapter.emit("1/1/1", True)

    assert len(updates) == 2
    assert updates[0].device_id.native_id == "ceiling"
    assert updates[0].state == {"OnOff": None, "Brightness": 55}
    assert updates[1].state == {"OnOff": True, "Brightness": 55}
    assert updates[1].source == "external"


async def test_optional_ets_project_supplies_only_missing_dpt():
    registry = KNXDeviceRegistry.from_dict(
        {
            "schema_version": 1,
            "devices": {
                "contact": {
                    "name": "Contact",
                    "room": "Living room",
                    "type": "binary_sensor",
                    "addresses": {"value_state": "2/1/1"},
                }
            },
        }
    )
    project = {
        "group_addresses": {
            "2/1/1": {
                "address": "2/1/1",
                "dpt": {"main": 1, "sub": 19},
                "communication_object_ids": ["co-1"],
            }
        },
        "communication_objects": {"co-1": {"dpts": []}},
    }
    adapter = _FakeAdapter()

    def adapter_factory(config: KNXIPConfig, **kwargs: Any) -> _FakeAdapter:
        del config
        adapter.address_dpts = dict(kwargs["address_dpts"])
        adapter.set_update_callback(kwargs["update_callback"])
        return adapter

    platform = KNXIPPlatform(
        KNXIPConfig(project_file="project.knxproj"),
        registry_loader=lambda _: registry,
        project_loader=lambda _path, _password: project,
        adapter_factory=adapter_factory,
    )

    await platform.start()

    assert adapter.address_dpts == {"2/1/1": "1.019"}
    device = (await platform.discover_devices())[0]
    assert device.name == "Contact"
    assert device.room == "Living room"


async def test_data_secure_ets_mapping_requires_keyfile():
    registry = KNXDeviceRegistry.from_dict(
        {
            "schema_version": 1,
            "devices": {
                "contact": {
                    "name": "Contact",
                    "room": "Living room",
                    "type": "binary_sensor",
                    "addresses": {"value_state": "2/1/1"},
                }
            },
        }
    )
    project = {
        "group_addresses": {
            "2/1/1": {
                "address": "2/1/1",
                "dpt": {"main": 1, "sub": 19},
                "communication_object_ids": ["co-1"],
                "data_secure": True,
            }
        },
        "communication_objects": {"co-1": {"dpts": []}},
    }
    platform = KNXIPPlatform(
        KNXIPConfig(project_file="project.knxproj"),
        registry_loader=lambda _: registry,
        project_loader=lambda _path, _password: project,
        adapter_factory=lambda *_args, **_kwargs: _FakeAdapter(),
    )

    with pytest.raises(KNXMapError, match="Data Secure"):
        await platform.start()


async def test_initial_connection_failure_keeps_mapping_discoverable_and_unhealthy():
    platform, _ = _platform(_FakeAdapter(start_error=KNXConnectionError("offline")))

    await platform.start()

    assert not await platform.health_check()
    assert len(await platform.discover_devices()) == 6
    await platform.stop()


async def test_platform_can_restart_after_clean_stop():
    platform, adapter = _platform()
    await platform.start()
    await platform.stop()

    await platform.start()

    assert adapter.start_count == 2
    assert await platform.health_check()
    assert len(await platform.discover_devices()) == 6
    await platform.stop()


async def test_stop_disconnects_adapter_and_removes_callback():
    platform, adapter = _platform()
    await platform.start()

    await platform.stop()

    assert adapter.stopped
    assert adapter.update_callback is None
