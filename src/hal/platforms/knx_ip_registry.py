"""Authoritative semantic device mapping for the native KNX/IP platform."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from src.hal.models import DeviceType, SensorClass


class KNXMapError(ValueError):
    """Raised when an authoritative KNX device map is invalid."""


@dataclass(frozen=True, slots=True)
class KNXAddressBinding:
    """A configured device role bound to one KNX group address."""

    device_id: str
    role: str
    address: str


@dataclass(frozen=True, slots=True)
class KNXMappedDevice:
    """One logical HAL device defined explicitly by the deployment map."""

    device_id: str
    name: str
    room: str
    floor: str
    device_type: DeviceType
    sensor_class: SensorClass
    addresses: dict[str, str]
    dpts: dict[str, str]
    scene_number: int | None = None

    def dpt_for(self, role: str) -> str | None:
        """Return the explicit or conventional DPT for an address role."""
        explicit = self.dpts.get(role)
        if explicit is not None:
            return explicit
        if role.endswith("_state"):
            paired_role = role.removesuffix("_state")
            explicit = self.dpts.get(paired_role)
            if explicit is not None:
                return explicit
        return _ROLE_DPT_DEFAULTS.get(role)


@dataclass(frozen=True, slots=True)
class _DeviceSpec:
    required: frozenset[str]
    optional: frozenset[str]

    @property
    def allowed(self) -> frozenset[str]:
        return self.required | self.optional


_DEVICE_SPECS: dict[DeviceType, _DeviceSpec] = {
    DeviceType.LIGHT: _DeviceSpec(
        required=frozenset({"switch"}),
        optional=frozenset({"switch_state"}),
    ),
    DeviceType.SWITCH: _DeviceSpec(
        required=frozenset({"switch"}),
        optional=frozenset({"switch_state"}),
    ),
    DeviceType.DIMMABLE_LIGHT: _DeviceSpec(
        required=frozenset({"switch", "brightness"}),
        optional=frozenset({"switch_state", "brightness_state"}),
    ),
    DeviceType.BLIND: _DeviceSpec(
        required=frozenset({"move", "stop"}),
        optional=frozenset({"move_state", "position", "position_state"}),
    ),
    DeviceType.HEATING: _DeviceSpec(
        required=frozenset({"setpoint"}),
        optional=frozenset({"setpoint_state", "temperature_state", "switch", "switch_state"}),
    ),
    DeviceType.BINARY_SENSOR: _DeviceSpec(
        required=frozenset({"value_state"}),
        optional=frozenset(),
    ),
    DeviceType.TEMPERATURE_SENSOR: _DeviceSpec(
        required=frozenset({"temperature_state"}),
        optional=frozenset(),
    ),
    DeviceType.SCENE: _DeviceSpec(
        required=frozenset({"scene"}),
        optional=frozenset({"scene_state"}),
    ),
    DeviceType.TRIGGER: _DeviceSpec(
        required=frozenset({"trigger"}),
        optional=frozenset({"trigger_state"}),
    ),
}

_ROLE_DPT_DEFAULTS: dict[str, str] = {
    "switch": "1.001",
    "switch_state": "1.001",
    "brightness": "5.001",
    "brightness_state": "5.001",
    "move": "1.008",
    "move_state": "1.008",
    "stop": "1.007",
    "position": "5.001",
    "position_state": "5.001",
    "setpoint": "9.001",
    "setpoint_state": "9.001",
    "temperature_state": "9.001",
    "scene": "17.001",
    "scene_state": "17.001",
    "trigger": "1.001",
    "trigger_state": "1.001",
}

_ROLE_ALLOWED_DPTS: dict[str, frozenset[str]] = {
    "switch": frozenset({"1", "1.001"}),
    "switch_state": frozenset({"1", "1.001"}),
    "brightness": frozenset({"5.001"}),
    "brightness_state": frozenset({"5.001"}),
    "move": frozenset({"1", "1.008"}),
    "move_state": frozenset({"1", "1.008"}),
    "stop": frozenset({"1", "1.007"}),
    "position": frozenset({"5.001"}),
    "position_state": frozenset({"5.001"}),
    "setpoint": frozenset({"9", "9.001"}),
    "setpoint_state": frozenset({"9", "9.001"}),
    "temperature_state": frozenset({"9", "9.001"}),
    "scene": frozenset({"17.001", "18.001"}),
    "scene_state": frozenset({"17.001", "18.001"}),
    "trigger": frozenset({"1", "1.001"}),
    "trigger_state": frozenset({"1", "1.001"}),
}

_DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_DPT_RE = re.compile(r"^(?P<main>\d{1,3})(?:\.(?P<sub>\d{1,3}))?$")
_DEVICE_FIELDS = {
    "name",
    "room",
    "floor",
    "type",
    "sensor_class",
    "addresses",
    "dpts",
    "scene_number",
}


class KNXDeviceRegistry:
    """Validated in-memory view of an authoritative KNX device map."""

    def __init__(self, devices: list[KNXMappedDevice]) -> None:
        self._devices = tuple(devices)
        self._by_id = {device.device_id: device for device in devices}
        by_address: dict[int, list[KNXAddressBinding]] = {}
        for device in devices:
            for role, address in device.addresses.items():
                binding = KNXAddressBinding(device.device_id, role, address)
                by_address.setdefault(group_address_raw(address), []).append(binding)
        self._by_address = {address: tuple(bindings) for address, bindings in by_address.items()}

    @property
    def devices(self) -> tuple[KNXMappedDevice, ...]:
        return self._devices

    def get(self, device_id: str) -> KNXMappedDevice | None:
        return self._by_id.get(device_id)

    def bindings_for_address(self, address: str) -> tuple[KNXAddressBinding, ...]:
        try:
            raw_address = group_address_raw(address)
        except KNXMapError:
            return ()
        return self._by_address.get(raw_address, ())

    @classmethod
    def load(cls, path: str | Path) -> KNXDeviceRegistry:
        mapping_path = Path(path)
        try:
            with mapping_path.open(encoding="utf-8") as mapping_file:
                raw = yaml.safe_load(mapping_file) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise KNXMapError(f"Could not load KNX device map: {mapping_path}") from exc
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: Any) -> KNXDeviceRegistry:
        if not isinstance(raw, dict):
            raise KNXMapError("KNX device map must be a YAML object")
        if raw.get("schema_version") != 1:
            raise KNXMapError("Unsupported KNX device-map schema_version; expected 1")
        raw_devices = raw.get("devices")
        if not isinstance(raw_devices, dict):
            raise KNXMapError("KNX device map requires a devices object")
        if not raw_devices:
            raise KNXMapError("KNX device map requires at least one device")

        devices = [cls._parse_device(device_id, value) for device_id, value in raw_devices.items()]
        return cls(devices)

    @staticmethod
    def _parse_device(device_id: Any, raw: Any) -> KNXMappedDevice:
        if not isinstance(device_id, str) or not _DEVICE_ID_RE.fullmatch(device_id):
            raise KNXMapError(f"Invalid KNX device id: {device_id!r}")
        if not isinstance(raw, dict):
            raise KNXMapError(f"Device {device_id!r} must be a YAML object")
        unknown_fields = set(raw) - _DEVICE_FIELDS
        if unknown_fields:
            fields = ", ".join(sorted(unknown_fields))
            raise KNXMapError(f"Device {device_id!r} has unsupported fields: {fields}")

        name = _required_text(raw, "name", device_id)
        room = _required_text(raw, "room", device_id)
        floor = _optional_text(raw, "floor", device_id)
        try:
            device_type = DeviceType(raw.get("type"))
        except (TypeError, ValueError) as exc:
            raise KNXMapError(
                f"Device {device_id!r} has unsupported type {raw.get('type')!r}"
            ) from exc
        spec = _DEVICE_SPECS.get(device_type)
        if spec is None:
            raise KNXMapError(f"Device type {device_type.value!r} is not supported by KNX/IP")

        addresses = _parse_addresses(device_id, raw.get("addresses"), spec)
        dpts = _parse_dpts(device_id, raw.get("dpts", {}), addresses)
        try:
            sensor_class = SensorClass(raw.get("sensor_class", SensorClass.GENERIC))
        except ValueError as exc:
            raise KNXMapError(
                f"Device {device_id!r} has invalid sensor_class {raw.get('sensor_class')!r}"
            ) from exc

        scene_number = _parse_scene_number(device_id, device_type, raw.get("scene_number"))
        return KNXMappedDevice(
            device_id=device_id,
            name=name,
            room=room,
            floor=floor,
            device_type=device_type,
            sensor_class=sensor_class,
            addresses=addresses,
            dpts=dpts,
            scene_number=scene_number,
        )


def _parse_scene_number(
    device_id: str,
    device_type: DeviceType,
    raw: Any,
) -> int | None:
    if device_type is DeviceType.SCENE:
        if not isinstance(raw, int) or isinstance(raw, bool) or not 1 <= raw <= 64:
            raise KNXMapError(f"Scene device {device_id!r} requires scene_number from 1 to 64")
        return raw
    if raw is not None:
        raise KNXMapError(f"Device {device_id!r} may define scene_number only for type scene")
    return None


def _required_text(raw: dict[str, Any], field: str, device_id: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise KNXMapError(f"Device {device_id!r} requires non-empty {field}")
    return value.strip()


def _optional_text(raw: dict[str, Any], field: str, device_id: str) -> str:
    value = raw.get(field, "")
    if not isinstance(value, str):
        raise KNXMapError(f"Device {device_id!r} field {field} must be text")
    return value.strip()


def _parse_addresses(device_id: str, raw: Any, spec: _DeviceSpec) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise KNXMapError(f"Device {device_id!r} requires an addresses object")
    roles = set(raw)
    missing = spec.required - roles
    unsupported = roles - spec.allowed
    if missing:
        raise KNXMapError(
            f"Device {device_id!r} is missing address role(s): {', '.join(sorted(missing))}"
        )
    if unsupported:
        raise KNXMapError(
            f"Device {device_id!r} has unsupported address role(s): {', '.join(sorted(unsupported))}"
        )
    return {
        str(role): _validate_group_address(str(address), str(role)) for role, address in raw.items()
    }


def group_address_raw(address: str) -> int:
    """Return the 16-bit value shared by equivalent two/three-level addresses."""
    try:
        values = [int(part) for part in address.split("/")]
    except ValueError as exc:
        raise KNXMapError(f"Invalid group address {address!r}") from exc
    if len(values) == 3 and 0 <= values[0] <= 31 and 0 <= values[1] <= 7 and 0 <= values[2] <= 255:
        return (values[0] << 11) | (values[1] << 8) | values[2]
    if len(values) == 2 and 0 <= values[0] <= 31 and 0 <= values[1] <= 2047:
        return (values[0] << 11) | values[1]
    raise KNXMapError(f"Invalid group address {address!r}")


def _validate_group_address(address: str, role: str) -> str:
    try:
        group_address_raw(address)
    except KNXMapError as exc:
        raise KNXMapError(f"Invalid group address {address!r} for role {role}") from exc
    return address


def _parse_dpts(device_id: str, raw: Any, addresses: dict[str, str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise KNXMapError(f"Device {device_id!r} dpts must be an object")
    unsupported = set(raw) - set(addresses)
    if unsupported:
        raise KNXMapError(
            f"Device {device_id!r} defines DPT for unmapped role(s): {', '.join(sorted(unsupported))}"
        )
    return {str(role): validate_role_dpt(str(role), value) for role, value in raw.items()}


def validate_role_dpt(role: str, value: Any) -> str:
    """Normalize a DPT and reject encodings incompatible with a semantic role."""
    dpt = _normalize_dpt(value)
    if role == "value_state" and (dpt == "1" or dpt.startswith("1.")):
        return dpt
    allowed = _ROLE_ALLOWED_DPTS.get(role, frozenset())
    if dpt not in allowed:
        raise KNXMapError(f"DPT {dpt} is not compatible with role {role!r}")
    return dpt


def _normalize_dpt(value: Any) -> str:
    if not isinstance(value, str):
        raise KNXMapError(f"Invalid DPT {value!r}")
    match = _DPT_RE.fullmatch(value)
    if match is None:
        raise KNXMapError(f"Invalid DPT {value!r}")
    try:
        main = int(match.group("main"))
        sub_text = match.group("sub")
        sub = int(sub_text) if sub_text is not None else None
    except (TypeError, ValueError) as exc:
        raise KNXMapError(f"Invalid DPT {value!r}") from exc
    if not 1 <= main <= 999:
        raise KNXMapError(f"Invalid DPT {value!r}")
    if sub is None:
        return str(main)
    if not 0 <= sub <= 999:
        raise KNXMapError(f"Invalid DPT {value!r}")
    return f"{main}.{sub:03d}"
