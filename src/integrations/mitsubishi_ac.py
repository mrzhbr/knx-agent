"""Mitsubishi AC adapter for KNX Agent.

Controls Mitsubishi Wi-Fi adapters via pymitsubishi (sync HTTP wrapped with asyncio.to_thread).
Polls every poll_interval seconds and logs state changes.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import MitsubishiACConfig

logger = logging.getLogger(__name__)


@dataclass
class _ACUnitState:
    """Internal state for one AC unit."""

    name: str
    room: str
    ip: str
    controller: Any  # MitsubishiController
    api: Any  # MitsubishiAPI — kept for cleanup via api.close()
    last_state: dict = field(default_factory=dict)
    poll_task: asyncio.Task | None = None


class MitsubishiACAdapter:
    """Integration adapter for Mitsubishi AC units via pymitsubishi.

    Implements the Integration protocol: name, start(), stop(), health_check().
    Also provides: set_power(), set_temperature(), set_mode(), get_status().

    pymitsubishi is synchronous — all calls are wrapped with asyncio.to_thread().
    """

    name: str = "mitsubishi_ac"

    def __init__(
        self,
        config: "MitsubishiACConfig",
    ) -> None:
        self._config = config
        self._units: list[_ACUnitState] = []

    async def start(self) -> None:
        """Initialize controllers and start poll loops for all configured AC units."""
        try:
            from pymitsubishi import MitsubishiAPI, MitsubishiController  # type: ignore[import-not-found]
        except ImportError:
            logger.error("pymitsubishi not installed — AC adapter disabled")
            return

        for unit_cfg in self._config.units:
            try:
                api = MitsubishiAPI(device_host_port=unit_cfg.ip)
                controller = MitsubishiController(api=api)

                # Initial status fetch
                try:
                    await asyncio.to_thread(controller.fetch_status)
                    last_state = self._extract_state(controller)
                except Exception as e:
                    logger.warning("Initial fetch for %s failed: %s", unit_cfg.name, e)
                    last_state = {}

                unit = _ACUnitState(
                    name=unit_cfg.name,
                    room=unit_cfg.room,
                    ip=unit_cfg.ip,
                    controller=controller,
                    api=api,
                    last_state=last_state,
                )
                self._units.append(unit)

                # Start poll task
                unit.poll_task = asyncio.create_task(
                    self._poll_loop(unit),
                    name=f"ac_poll_{unit_cfg.name}",
                )
                logger.info("MitsubishiACAdapter: started unit %s @ %s", unit_cfg.name, unit_cfg.ip)
            except Exception as e:
                logger.error("Failed to init AC unit %s: %s", unit_cfg.name, e)

    async def stop(self) -> None:
        """Cancel all poll tasks and close API connections."""
        for unit in self._units:
            if unit.poll_task and not unit.poll_task.done():
                unit.poll_task.cancel()
                with suppress(asyncio.CancelledError):
                    await unit.poll_task
            try:
                await asyncio.to_thread(unit.api.close)
            except Exception as e:
                logger.debug("AC api.close() failed for %s: %s", unit.name, e)
        self._units.clear()
        logger.info("MitsubishiACAdapter stopped")

    async def health_check(self) -> bool:
        """Return True if at least one unit responds to fetch_status."""
        if not self._units:
            return False
        for unit in self._units:
            try:
                await asyncio.to_thread(unit.controller.fetch_status)
                return True
            except Exception:
                continue
        return False

    async def _poll_loop(self, unit: _ACUnitState) -> None:
        """Poll one AC unit every poll_interval seconds, fire awareness events on change."""
        interval = self._config.poll_interval
        while True:
            try:
                await asyncio.sleep(interval)
                await asyncio.to_thread(unit.controller.fetch_status)
                new_state = self._extract_state(unit.controller)
                self._detect_changes(unit, new_state)
                unit.last_state = new_state
                logger.debug("AC poll %s: %s", unit.name, new_state)
            except Exception as e:
                logger.warning("AC poll error for %s: %s", unit.name, e)

    def _extract_state(self, controller: Any) -> dict:
        """Extract a serializable state snapshot from a controller."""
        try:
            state = controller.state
            return {
                "power": str(state.general.power_on_off),
                "mode": str(state.general.drive_mode),
                "set_temp": state.general.fine_temperature,
                "room_temp": state.sensors.inside_temperature_1_fine,
                "power_watt": state.energy.power_watt,
                "energy_kwh": state.energy.energy_hecto_watt_hour / 10.0,
            }
        except Exception as e:
            logger.debug("State extraction failed: %s", e)
            return {}

    def _detect_changes(self, unit: _ACUnitState, new_state: dict) -> None:
        """Compare old and new state; log changes."""
        old = unit.last_state
        if not old or not new_state:
            return
        for key in ("power", "mode", "set_temp"):
            old_val = old.get(key)
            new_val = new_state.get(key)
            if old_val is not None and new_val is not None and old_val != new_val:
                logger.info("AC state change: %s/%s %s→%s", unit.room, key, old_val, new_val)

    def _find_unit(self, room: str) -> _ACUnitState | None:
        """Find a unit by room name (case-insensitive)."""
        room_lower = room.lower()
        for unit in self._units:
            if unit.room.lower() == room_lower:
                return unit
        return None

    async def set_power(self, room: str, on: bool) -> None:
        """Turn AC power on or off for the specified room."""
        unit = self._find_unit(room)
        if unit is None:
            raise ValueError(f"No AC unit found for room '{room}'")
        await asyncio.to_thread(unit.controller.set_power, on)

    async def set_temperature(self, room: str, temp: float) -> None:
        """Set target temperature for AC in the specified room."""
        unit = self._find_unit(room)
        if unit is None:
            raise ValueError(f"No AC unit found for room '{room}'")
        await asyncio.to_thread(unit.controller.set_temperature, temp)

    async def set_mode(self, room: str, mode: str) -> None:
        """Set drive mode for AC. mode is a DriveMode enum name string."""
        unit = self._find_unit(room)
        if unit is None:
            raise ValueError(f"No AC unit found for room '{room}'")
        try:
            from pymitsubishi import DriveMode  # type: ignore[import-not-found]

            drive_mode = DriveMode[mode.upper()]
        except (ImportError, KeyError) as e:
            raise ValueError(f"Invalid mode '{mode}': {e}") from e
        await asyncio.to_thread(unit.controller.set_mode, drive_mode)

    def get_status(self, room: str | None = None) -> dict:
        """Return status dict for one room or all units (from cached state)."""
        if room is not None:
            unit = self._find_unit(room)
            if unit is None:
                return {}
            return {unit.room: unit.last_state}
        return {u.room: u.last_state for u in self._units}

    def get_room_temperature(self, room: str) -> float | None:
        """Return inside temperature for the specified room."""
        unit = self._find_unit(room)
        if unit is None:
            return None
        return unit.last_state.get("room_temp")
