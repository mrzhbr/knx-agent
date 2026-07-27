"""Gira X1 device registry -- private implementation detail of GiraX1Platform.

This module is internal to src/hal/platforms/ and must not be imported
by any code outside GiraX1Platform. External consumers use HAL's unified
DeviceInfo / get_all_rooms() / get_all_devices() interface instead.
"""

from __future__ import annotations

# UIConfig Structure (from official Gira IoT REST API v2 docs):
#
# GET /api/v2/uiconfig?expand=parameters,dataPointFlags,locations,trades
# {
#   "uid": "<uiconfig uid>",
#   "functions": [
#     {
#       "uid": "<function uid>",
#       "functionType": "<function type URN>",   # → device type
#       "channelType": "<channel type URN>",      # → datapoint structure
#       "displayName": "<display name>",
#       "dataPoints": [
#         {"uid": "<dp uid>", "name": "<name>", "canRead": bool, "canWrite": bool, "canEvent": bool}
#       ],
#       "parameters": [{"set": "...", "key": "...", "value": "..."}]
#     }
#   ],
#   "locations": [               # nested: floors → rooms → function UIDs
#     {
#       "displayName": "<floor/room name>",
#       "locationType": "<predefined type>",
#       "functions": [{"uid": "<function uid>"}],
#       "locations": [...]        # recursive children
#     }
#   ],
#   "trades": [...]
# }
#
# Function Type URN → device type:
#   de.gira.schema.functions.KNX.Light       → dimmable_light
#   de.gira.schema.functions.Switch          → switch
#   de.gira.schema.functions.Covering        → blind
#   de.gira.schema.functions.KNX.HeatingCooling → heating
#   de.gira.schema.functions.BinaryStatus    → binary_sensor
#   de.gira.schema.functions.Camera          → camera
#   de.gira.schema.functions.PressAndHold    → trigger
#   de.gira.schema.functions.Trigger         → trigger_onoff
#
# Channel Type URN → datapoint names:
#   KNX.Dimmer: OnOff, Shift, Brightness
#   Switch: OnOff
#   BlindWithPos: Step-Up-Down, Up-Down
#   KNX.HeatingCoolingSwitchable: Current, Set-Point, OnOff, Presence, Heating, Cooling
#   Binary: Binary
#   Camera: Camera
#   Trigger: Trigger

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

FUNCTION_TYPE_MAP: dict[str, str] = {
    "de.gira.schema.functions.KNX.Light": "dimmable_light",
    "de.gira.schema.functions.Switch": "switch",
    "de.gira.schema.functions.ColoredLight": "colored_light",
    "de.gira.schema.functions.TunableLight": "tunable_light",
    "de.gira.schema.functions.Covering": "blind",
    "de.gira.schema.functions.KNX.HeatingCooling": "heating",
    "de.gira.schema.functions.SaunaHeating": "sauna",
    "de.gira.schema.functions.BinaryStatus": "binary_sensor",
    "de.gira.schema.functions.Camera": "camera",
    "de.gira.schema.functions.PressAndHold": "trigger",
    "de.gira.schema.functions.Trigger": "trigger_onoff",
    "de.gira.schema.functions.Scene": "scene",
}

ALARM_READINESS_NAME_MARKERS = ("scharf.bereit", "alarm ready", "alarm readiness")
GROUP_CONTROLLER_NAME_SUFFIXES = (" alle", " alles", " all")


@dataclass
class Device:
    uid: str
    name: str
    type: str
    subtype: str  # For binary_sensor: "alarm_readiness" or "window_door". Others: ""
    room_key: str
    datapoints: dict[str, str]  # name -> uid
    is_group_controller: bool = False


@dataclass
class Room:
    key: str
    display_name: str
    floor: str
    devices: list[Device] = field(default_factory=list)


class DeviceRegistry:
    """Dynamically built from X1 uiconfig API response.

    The X1 is the single source of truth for devices, rooms, UIDs.
    No hardcoded device lists.
    """

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._devices: dict[str, Device] = {}  # uid -> Device
        self._dp_to_device: dict[str, Device] = {}  # datapoint uid -> Device

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "DeviceRegistry":
        """Build registry from a uiconfig JSON dict (for testing or cached responses)."""
        registry = cls()
        registry._parse(data)
        return registry

    def _parse(self, data: dict[str, Any]) -> None:
        """Parse uiconfig JSON into rooms and devices."""
        functions_raw = data.get("functions", [])
        locations_raw = data.get("locations", [])

        # Parse locations to build uid -> (room_key, floor, room_display_name)
        uid_to_room: dict[str, tuple[str, str, str]] = {}
        self._parse_locations(locations_raw, floor_name="", uid_to_room=uid_to_room)

        # Parse each function into a Device
        for func in functions_raw:
            uid = func["uid"]
            name = func.get("displayName", "Unknown")
            function_type = func.get("functionType", "")
            device_type = FUNCTION_TYPE_MAP.get(function_type)
            if device_type is None:
                logger.warning("Unknown functionType %r for uid %s (%s)", function_type, uid, name)
                device_type = "unknown"

            # Parse datapoints: {name: uid}
            datapoints: dict[str, str] = {}
            for dp in func.get("dataPoints", []):
                dp_name = dp.get("name", dp["uid"])
                datapoints[dp_name] = dp["uid"]

            # Determine subtype
            subtype = ""
            normalized_name = name.casefold()
            if device_type == "binary_sensor":
                if any(marker in normalized_name for marker in ALARM_READINESS_NAME_MARKERS):
                    subtype = "alarm_readiness"
                else:
                    subtype = "window_door"

            # Group controllers are identified by generic display-name suffixes;
            # deployment-specific function UIDs must never be embedded in code.
            is_group = any(
                normalized_name.endswith(suffix) for suffix in GROUP_CONTROLLER_NAME_SUFFIXES
            )

            # Room
            room_info = uid_to_room.get(uid)
            if room_info:
                floor, room_key, room_display = room_info
            else:
                floor, room_key, room_display = "", "unassigned", "Unassigned"

            device = Device(
                uid=uid,
                name=name,
                type=device_type,
                subtype=subtype,
                room_key=room_key,
                datapoints=datapoints,
                is_group_controller=is_group,
            )
            self._devices[uid] = device

            # Map datapoint UIDs back to device
            for dp_uid in datapoints.values():
                self._dp_to_device[dp_uid] = device

            # Ensure room exists and add device
            if room_key not in self._rooms:
                self._rooms[room_key] = Room(
                    key=room_key,
                    display_name=room_display,
                    floor=floor,
                )
            self._rooms[room_key].devices.append(device)

        logger.info(
            "DeviceRegistry: %d devices in %d rooms",
            len(self._devices),
            len(self._rooms),
        )

    def _parse_locations(
        self,
        locations: list[dict],
        floor_name: str,
        uid_to_room: dict[str, tuple[str, str, str]],
    ) -> None:
        """Recursively parse location tree."""
        for loc in locations:
            display = loc.get("displayName", "")
            loc_type = loc.get("locationType", "")
            children = loc.get("locations", [])
            functions = loc.get("functions", [])

            # Determine if this is a floor or room
            if loc_type == "Floor" or children:
                # This is a floor-level location — recurse
                current_floor = display
                if functions:
                    # Floor has direct functions — treat it as both floor and room
                    room_key = display.lower().replace(" ", "_").replace("/", "_")
                    for f in functions:
                        # Live API: flat string UIDs. Fixture: {"uid": "..."} dicts.
                        uid = f["uid"] if isinstance(f, dict) else f
                        uid_to_room[uid] = (current_floor, room_key, display)
                self._parse_locations(children, current_floor, uid_to_room)
            else:
                # Leaf room
                room_key = display.lower().replace(" ", "_").replace("/", "_")
                for f in functions:
                    uid = f["uid"] if isinstance(f, dict) else f
                    uid_to_room[uid] = (floor_name, room_key, display)

    def all_rooms(self) -> list[Room]:
        return list(self._rooms.values())

    def get_room(self, key: str) -> Room | None:
        return self._rooms.get(key)

    def find_room(self, display_name: str) -> Room | None:
        """Find a room by display name (case-insensitive)."""
        name_lower = display_name.lower().strip()
        for room in self._rooms.values():
            if room.display_name.lower() == name_lower:
                return room
        # Fuzzy: partial match
        for room in self._rooms.values():
            if name_lower in room.display_name.lower() or room.display_name.lower() in name_lower:
                return room
        return None

    def find_device(self, name: str, room: str | None = None) -> "Device | str":
        """Find a device by name, optionally within a specific room.

        Returns:
            Device — exact match found
            str — error message (ambiguous name, room required; or not found)
        """
        name_lower = name.lower().strip()
        candidates: list[Device] = []

        if room:
            room_obj = self.find_room(room)
            if room_obj is None:
                return f"Raum '{room}' nicht gefunden."
            candidates = [d for d in room_obj.devices if d.name.lower() == name_lower]
            if not candidates:
                return f"Gerät '{name}' nicht gefunden in Raum '{room_obj.display_name}'."
            if len(candidates) == 1:
                return candidates[0]
            return f"Mehrere Geräte '{name}' in '{room_obj.display_name}' gefunden."
        else:
            for device in self._devices.values():
                if device.name.lower() == name_lower:
                    candidates.append(device)
            if not candidates:
                return f"Gerät '{name}' nicht gefunden."
            if len(candidates) == 1:
                return candidates[0]
            rooms = sorted({d.room_key for d in candidates})
            room_displays = [self._rooms[r].display_name for r in rooms if r in self._rooms]
            return (
                f"Mehrere Geräte '{name}' gefunden in: {', '.join(room_displays)}. "
                f"Bitte Raum angeben."
            )

    def lookup_datapoint(self, uid: str) -> Device | None:
        """Look up a device by a datapoint UID."""
        return self._dp_to_device.get(uid)

    def all_function_uids(self) -> list[str]:
        return list(self._devices.keys())

    def get_devices_by_type(self, device_type: str) -> list[Device]:
        return [d for d in self._devices.values() if d.type == device_type]

    def get_window_door_sensors(self) -> list[Device]:
        return [d for d in self._devices.values() if d.subtype == "window_door"]

    def get_alarm_readiness_sensors(self) -> list[Device]:
        return [d for d in self._devices.values() if d.subtype == "alarm_readiness"]

    def format_for_prompt(self) -> str:
        """Compact room listing for system prompt. One line per room."""
        lines: list[str] = []
        for room in sorted(self._rooms.values(), key=lambda r: r.floor + r.display_name):
            type_counts: dict[str, int] = {}
            for device in room.devices:
                type_counts[device.type] = type_counts.get(device.type, 0) + 1
            type_summary = ", ".join(f"{count} {t}" for t, count in sorted(type_counts.items()))
            floor = f" ({room.floor})" if room.floor else ""
            lines.append(f"{room.display_name}{floor}: {type_summary}")
        return "\n".join(lines)
