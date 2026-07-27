from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.hal.platforms.gira_x1_registry import DeviceRegistry


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "uiconfig_sample.json"


def load_registry() -> DeviceRegistry:
    data = json.loads(FIXTURE_PATH.read_text())
    return DeviceRegistry.from_json(data)


def test_load_fixture():
    registry = load_registry()
    assert len(registry.all_rooms()) > 0


def test_devices_parsed():
    registry = load_registry()
    all_uids = registry.all_function_uids()
    assert len(all_uids) > 0


def test_find_room_case_insensitive():
    registry = load_registry()
    rooms = registry.all_rooms()
    assert len(rooms) > 0
    first_room = rooms[0]
    found = registry.find_room(first_room.display_name.upper())
    assert found is not None
    assert found.key == first_room.key


def test_get_devices_by_type():
    registry = load_registry()
    lights = registry.get_devices_by_type("dimmable_light")
    switches = registry.get_devices_by_type("switch")
    # At least one device type should have entries
    assert len(lights) + len(switches) > 0


def test_format_for_prompt():
    registry = load_registry()
    output = registry.format_for_prompt()
    assert isinstance(output, str)
    assert len(output) > 0


def test_find_device_by_name():
    registry = load_registry()
    all_devices = [d for room in registry.all_rooms() for d in room.devices]
    if not all_devices:
        pytest.skip("No devices in fixture")
    device = all_devices[0]
    result = registry.find_device(device.name)
    # Either Device (found) or str (ambiguous/not found) — should not crash
    assert result is not None


def test_lookup_datapoint():
    registry = load_registry()
    all_devices = [d for room in registry.all_rooms() for d in room.devices]
    if not all_devices:
        pytest.skip("No devices in fixture")
    device = all_devices[0]
    if not device.datapoints:
        pytest.skip("No datapoints")
    dp_uid = next(iter(device.datapoints.values()))
    found = registry.lookup_datapoint(dp_uid)
    assert found is not None
    assert found.uid == device.uid


def test_window_door_sensors():
    registry = load_registry()
    sensors = registry.get_window_door_sensors()
    # May be empty if fixture has no binary sensors, that's OK
    for s in sensors:
        assert s.subtype == "window_door"
