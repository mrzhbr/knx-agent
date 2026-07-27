from __future__ import annotations

import logging

from .devices import DeviceAdapter, create_adapter
from .models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass
from .platform import Platform
from .rules import RuleEngine
from .state import StateStore

logger = logging.getLogger(__name__)


class HAL:
    """Hardware Abstraction Layer — single entry point for consumers.

    Manages one or more Platform backends, routes state updates through StateStore,
    and exposes a unified device API.
    """

    def __init__(self, state_store: StateStore) -> None:
        self._state_store = state_store
        self._platforms: dict[str, Platform] = {}
        self._devices: dict[str, DeviceAdapter] = {}
        self._device_infos: list[DeviceInfo] = []
        self._rule_engine = RuleEngine(state_store)
        self._topology_rooms: set[str] | None = None  # lowercase display names from topology

    def set_topology(self, topology: object) -> None:
        """Set the topology reference to filter rooms to physical rooms only.

        Builds a set of lowercase display names from topology graph nodes.
        When set, get_all_rooms/has_room/find_room only return topology rooms.
        """
        graph = getattr(topology, "graph", None)
        if graph is None:
            return
        self._topology_rooms = {graph.nodes[n].get("display_name", n).lower() for n in graph.nodes}

    async def add_platform(self, platform: Platform) -> None:
        """Start platform, discover devices, create adapters, and subscribe to events."""
        await platform.start()
        devices = await platform.discover_devices()

        for info in devices:
            adapter = create_adapter(info, platform)
            key = info.id.key
            self._devices[key] = adapter
            self._device_infos.append(info)
            self._state_store.register_device(info)
            self._rule_engine.register_device(info)

        async def _event_callback(state: DeviceState) -> None:
            await self._state_store.update(state)

        await platform.subscribe_events(_event_callback)
        # Wire StateStore into platform if it supports it (duck-typed, avoids Platform ABC change)
        if hasattr(platform, "set_state_store"):
            platform.set_state_store(self._state_store)  # type: ignore[union-attr]
        self._platforms[platform.name] = platform

    def find_device(self, name: str, room: str | None = None) -> DeviceAdapter | str:
        """Find a device by name with substring fallback.

        Returns the DeviceAdapter on success, or a German error string on failure.
        Tries exact case-insensitive match first, then substring match.
        Optionally filtered by room name (case-insensitive, also with substring fallback).
        """
        name_lower = name.lower()
        pool = self._device_infos

        # Filter by room first (exact, then substring)
        if room is not None:
            room_lower = room.lower()
            room_pool = [i for i in pool if i.room.lower() == room_lower]
            if not room_pool:
                room_pool = [i for i in pool if room_lower in i.room.lower()]
            pool = room_pool

        # Exact name match
        candidates = [i for i in pool if i.name.lower() == name_lower]

        # Substring fallback
        if not candidates:
            candidates = [i for i in pool if name_lower in i.name.lower()]

        if len(candidates) == 0:
            if room is not None:
                return f"Gerät '{name}' im Raum '{room}' nicht gefunden."
            return f"Gerät '{name}' nicht gefunden."

        if len(candidates) > 1:
            names = ", ".join(f"{c.name} ({c.room})" for c in candidates)
            return f"Mehrere Geräte gefunden für '{name}': {names}. Bitte genauer angeben."

        return self._devices[candidates[0].id.key]

    def find_room(self, display_name: str) -> list[DeviceAdapter]:
        """Return all adapters for devices in the matching room.

        Tries case-insensitive display name match first, then room_key slug,
        then substring match on display name.
        When topology is set, only matches rooms that exist in the topology.
        """
        room_lower = display_name.strip().lower()
        pool = [i for i in self._device_infos if self._is_topology_room(i.room)]
        # First: try case-insensitive display name match
        by_display = [
            self._devices[info.id.key] for info in pool if info.room.lower() == room_lower
        ]
        if by_display:
            return by_display
        # Fallback: exact match on room_key slug in platform_data
        by_slug = [
            self._devices[info.id.key]
            for info in pool
            if info.platform_data.get("room_key") == room_lower
        ]
        if by_slug:
            return by_slug
        # Fallback: substring match on display name
        return [self._devices[info.id.key] for info in pool if room_lower in info.room.lower()]

    def get_devices_by_type(self, device_type: DeviceType) -> list[DeviceAdapter]:
        """Return all adapters for devices of the given type."""
        return [
            self._devices[info.id.key]
            for info in self._device_infos
            if info.device_type == device_type
        ]

    def get_sensors_by_class(self, sensor_class: SensorClass) -> list[DeviceAdapter]:
        """Return all adapters for devices matching the given sensor_class.

        Filters by sensor_class only — any device_type is included.
        """
        return [
            self._devices[info.id.key]
            for info in self._device_infos
            if info.sensor_class == sensor_class
        ]

    def has_room(self, display_name: str) -> bool:
        """Return True if any device lives in a room matching this display name or room_key slug.

        When topology is set, only matches rooms that exist in the topology.
        """
        name_lower = display_name.strip().lower()
        for info in self._device_infos:
            if info.room.lower() == name_lower or info.platform_data.get("room_key") == name_lower:
                return self._is_topology_room(info.room)
        return False

    def get_platform(self, name: str) -> "Platform | None":
        """Return a platform by name, or None if not loaded."""
        return self._platforms.get(name)

    def iter_platforms(self) -> dict[str, "Platform"]:
        """Return all registered platforms as a dict view.

        Returns a shallow copy to prevent external mutation.
        """
        return dict(self._platforms)

    def _is_topology_room(self, display_name: str) -> bool:
        """Check if a room display name is in the topology (or topology not set)."""
        if self._topology_rooms is None:
            return True
        return display_name.lower() in self._topology_rooms

    def get_all_rooms(self) -> dict[str, list[DeviceInfo]]:
        """Return all devices grouped by room display name.

        When topology is set, only returns rooms that exist in the topology
        (physical rooms). System/virtual locations are excluded.
        """
        result: dict[str, list[DeviceInfo]] = {}
        for info in self._device_infos:
            if not self._is_topology_room(info.room):
                continue
            if info.room not in result:
                result[info.room] = []
            result[info.room].append(info)
        return result

    def get_all_devices(self) -> list[DeviceInfo]:
        """Return flat list of all known devices across all rooms."""
        return list(self._device_infos)

    @property
    def state_store(self) -> StateStore:
        """Public access to the StateStore for listener registration."""
        return self._state_store

    @property
    def rules(self) -> RuleEngine:
        """Public access to the RuleEngine for wiring external dependencies."""
        return self._rule_engine

    @property
    def device_count(self) -> int:
        """Number of discovered devices across all platforms."""
        return len(self._device_infos)

    async def get_room_state_formatted(self, room_name: str) -> dict[str, dict[str, str]]:
        """Return {device_name: {state_key: value}} for all devices in the room.

        Accepts both display names (e.g., 'Wohnzimmer') and room_key slugs (e.g., 'wohnzimmer').
        """
        # Step 1: Normalize input
        input_lower = room_name.strip().lower()

        # Step 2: Try case-insensitive display name match
        resolved_display_name: str | None = None
        for info in self._device_infos:
            if info.room.lower() == input_lower:
                resolved_display_name = info.room
                break

        # Step 3: If no match, try exact room_key match
        if resolved_display_name is None:
            for info in self._device_infos:
                if info.platform_data.get("room_key") == input_lower:
                    resolved_display_name = info.room
                    break

        if resolved_display_name is None:
            return {}

        # Step 4: Query StateStore with resolved display name
        raw_states = await self._state_store.get_room_state(resolved_display_name)

        # Step 5 & 6: Map device_keys back to device names
        # _device_infos is a list — build a lookup dict keyed by device_key
        device_key_to_info: dict[str, DeviceInfo] = {
            info.id.key: info for info in self._device_infos
        }
        return {
            device_key_to_info[device_key].name: {k: str(v) for k, v in state_dict.items()}
            for device_key, state_dict in raw_states.items()
            if device_key in device_key_to_info
        }

    async def get_full_state_summary(self) -> str:
        """Return a compact German-language summary of active devices.

        Groups by room display name. Shows lights that are on and heating at non-default.
        Returns 'Alle Lichter aus.' when no devices are active.
        """
        raw_states = await self._state_store.get_full_state_summary()

        # Build device_key -> info lookup (_device_infos is a list)
        device_key_to_info: dict[str, DeviceInfo] = {
            info.id.key: info for info in self._device_infos
        }

        # Group active devices by room
        by_room: dict[str, list[str]] = {}
        for device_key, state_dict in raw_states.items():
            info = device_key_to_info.get(device_key)
            if info is None:
                continue

            parts: list[str] = []
            on_val = state_dict.get("OnOff") or state_dict.get("on")
            brightness_val = state_dict.get("Brightness") or state_dict.get("brightness")
            temp_val = state_dict.get("SetValue") or state_dict.get("temperature")

            if on_val is not None and str(on_val) in ("1", "True", "true", "on"):
                if brightness_val is not None:
                    parts.append(f"an, {brightness_val}%")
                else:
                    parts.append("an")
            elif temp_val is not None:
                try:
                    temp_float = float(temp_val)
                    if temp_float != 20.0:
                        parts.append(f"{temp_float}°C")
                except (ValueError, TypeError):
                    pass

            if parts:
                room = info.room
                if room not in by_room:
                    by_room[room] = []
                by_room[room].append(f"{info.name}: {', '.join(parts)}")

        if not by_room:
            return "Alle Lichter aus."

        lines: list[str] = []
        for room in sorted(by_room.keys()):
            lines.append(f"{room}:")
            for device_str in by_room[room]:
                lines.append(f"  {device_str}")
        return "\n".join(lines)

    async def populate_initial_state(self) -> None:
        """Populate StateStore from platform read_state() for all discovered devices.

        MUST be called after start_runtime() so the platform has a warm state cache.
        Reads each device state via platform.read_state() which reads from the
        StateStore or falls back to the live API, then writes to StateStore.
        """
        for _key, adapter in self._devices.items():
            try:
                state = await adapter.platform.read_state(adapter.info.id)
                await self._state_store.update(state)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "populate_initial_state: skipping %s — %s",
                    adapter.info.name,
                    exc,
                )

    async def execute(self, device_id: DeviceId, command: str, **params: object) -> None:
        """Execute a command on a device.

        Marks brain action BEFORE send_command to avoid race with push callbacks.
        """
        self._state_store.mark_brain_action(device_id)
        adapter = self._devices.get(device_id.key)
        if adapter is None:
            return
        await adapter.platform.send_command(device_id, command, **params)

    async def stop(self) -> None:
        """Gracefully shut down: unsubscribe events, stop all platforms and state store."""
        self._rule_engine.stop()
        for platform in self._platforms.values():
            await platform.unsubscribe_events()
            await platform.stop()
        await self._state_store.stop()
