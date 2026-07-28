"""Contract tests for the authoritative KNX/IP device map."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest  # type: ignore[import-not-found]

from src.hal.models import DeviceType, SensorClass
from src.hal.platforms.knx_ip_registry import (  # type: ignore[import-not-found]
    KNXDeviceRegistry,
    KNXMapError,
)


def _map(**device_overrides: object) -> dict:
    device: dict[str, object] = {
        "name": "Living ceiling",
        "room": "Living room",
        "floor": "Ground floor",
        "type": "dimmable_light",
        "addresses": {
            "switch": "1/0/1",
            "switch_state": "1/1/1",
            "brightness": "1/0/2",
            "brightness_state": "1/1/2",
        },
        "dpts": {"switch": "1.001", "brightness": "5.001"},
    }
    device.update(device_overrides)
    return {"schema_version": 1, "devices": {"living_ceiling": device}}


def test_registry_parses_authoritative_device_definition():
    registry = KNXDeviceRegistry.from_dict(_map())

    device = registry.get("living_ceiling")
    assert device is not None
    assert device.name == "Living ceiling"
    assert device.room == "Living room"
    assert device.floor == "Ground floor"
    assert device.device_type is DeviceType.DIMMABLE_LIGHT
    assert device.addresses["brightness_state"] == "1/1/2"
    assert device.dpt_for("brightness") == "5.001"
    assert device.dpt_for("brightness_state") == "5.001"


def test_registry_supports_short_and_long_group_address_styles():
    mapping = _map(
        addresses={
            "switch": "1/1",
            "switch_state": "1/2",
            "brightness": "1/2/3",
            "brightness_state": "1/2/4",
        }
    )

    registry = KNXDeviceRegistry.from_dict(mapping)

    assert registry.get("living_ceiling") is not None


@pytest.mark.parametrize("address", ["32/0/1", "1/8/1", "1/0/256", "1/2048", "bad"])
def test_registry_rejects_invalid_group_addresses(address: str):
    mapping = _map()
    mapping["devices"]["living_ceiling"]["addresses"]["switch"] = address

    with pytest.raises(KNXMapError, match="switch"):
        KNXDeviceRegistry.from_dict(mapping)


def test_registry_rejects_missing_required_role():
    mapping = _map(addresses={"switch": "1/0/1", "switch_state": "1/1/1"})

    with pytest.raises(KNXMapError, match="brightness"):
        KNXDeviceRegistry.from_dict(mapping)


def test_registry_rejects_role_not_supported_by_device_type():
    mapping = _map()
    mapping["devices"]["living_ceiling"]["addresses"]["alarm"] = "1/0/5"

    with pytest.raises(KNXMapError, match="alarm"):
        KNXDeviceRegistry.from_dict(mapping)


def test_registry_rejects_dpt_for_unmapped_role():
    mapping = _map()
    mapping["devices"]["living_ceiling"]["dpts"]["position"] = "5.001"

    with pytest.raises(KNXMapError, match="position"):
        KNXDeviceRegistry.from_dict(mapping)


def test_registry_rejects_dpt_incompatible_with_role():
    mapping = _map(dpts={"switch": "9.001"})

    with pytest.raises(KNXMapError, match="not compatible.*switch"):
        KNXDeviceRegistry.from_dict(mapping)


def test_registry_rejects_invalid_dpt():
    mapping = _map(dpts={"switch": "boolean"})

    with pytest.raises(KNXMapError, match="boolean"):
        KNXDeviceRegistry.from_dict(mapping)


def test_binary_sensor_keeps_explicit_sensor_class():
    mapping = _map(
        type="binary_sensor",
        sensor_class="window",
        addresses={"value_state": "2/1/1"},
        dpts={"value_state": "1.019"},
    )

    device = KNXDeviceRegistry.from_dict(mapping).get("living_ceiling")

    assert device is not None
    assert device.sensor_class is SensorClass.WINDOW
    assert device.dpt_for("value_state") == "1.019"


def test_temperature_sensor_is_read_only_and_uses_temperature_dpt():
    mapping = _map(
        type="temperature_sensor",
        addresses={"temperature_state": "2/2/1"},
        dpts={},
    )

    device = KNXDeviceRegistry.from_dict(mapping).get("living_ceiling")

    assert device is not None
    assert device.device_type is DeviceType.TEMPERATURE_SENSOR
    assert device.dpt_for("temperature_state") == "9.001"


def test_scene_requires_fixed_scene_number():
    mapping = _map(type="scene", addresses={"scene": "3/0/1"}, dpts={})

    with pytest.raises(KNXMapError, match="scene_number"):
        KNXDeviceRegistry.from_dict(mapping)

    mapping["devices"]["living_ceiling"]["scene_number"] = 4
    device = KNXDeviceRegistry.from_dict(mapping).get("living_ceiling")
    assert device is not None
    assert device.scene_number == 4
    assert device.dpt_for("scene") == "17.001"


def test_registry_matches_equivalent_address_styles_for_telegrams():
    mapping = _map(
        addresses={
            "switch": "1/1",
            "switch_state": "1/258",
            "brightness": "1/2",
            "brightness_state": "1/259",
        }
    )
    registry = KNXDeviceRegistry.from_dict(mapping)

    bindings = registry.bindings_for_address("1/1/2")

    assert len(bindings) == 1
    assert bindings[0].role == "switch_state"


def test_registry_indexes_every_address_role():
    registry = KNXDeviceRegistry.from_dict(_map())

    bindings = registry.bindings_for_address("1/1/2")

    assert len(bindings) == 1
    assert bindings[0].device_id == "living_ceiling"
    assert bindings[0].role == "brightness_state"


def test_registry_loads_yaml_file(tmp_path: Path):
    mapping_path = tmp_path / "knx_devices.yaml"
    mapping_path.write_text(
        dedent(
            """\
            schema_version: 1
            devices:
              window:
                name: Window
                room: Living room
                type: binary_sensor
                sensor_class: window
                addresses:
                  value_state: 2/1/1
                dpts:
                  value_state: '1.019'
            """
        ),
        encoding="utf-8",
    )

    registry = KNXDeviceRegistry.load(mapping_path)

    assert registry.get("window") is not None


def test_public_example_mapping_is_valid():
    example = Path(__file__).parent.parent / "knx_devices.yaml.example"

    registry = KNXDeviceRegistry.load(example)

    assert len(registry.devices) == 8


def test_registry_rejects_empty_device_map():
    with pytest.raises(KNXMapError, match="at least one device"):
        KNXDeviceRegistry.from_dict({"schema_version": 1, "devices": {}})


def test_registry_rejects_unknown_schema_version():
    with pytest.raises(KNXMapError, match="schema_version"):
        KNXDeviceRegistry.from_dict({"schema_version": 2, "devices": {}})
