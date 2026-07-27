"""HAL Rule Engine — dataclasses, state helpers, and built-in rule names.

This module contains the foundational types for the rule engine:
- Violation, RuleConfig, CustomRule dataclasses
- BUILT_IN_RULES constant (single source of truth for all 8 rule names)
- Pure module-level state interpretation helpers (no I/O, no side effects)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .models import DeviceInfo
    from .state import StateStore

from .protocols import ConfigSource, EnergySource, PresenceQuery, TopologyQuery, WeatherSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Built-in rule names — single source of truth.
# Both RuleEngine._evaluate_sync() and list_house_rules use this constant.
# ---------------------------------------------------------------------------

BUILT_IN_RULES: list[str] = [
    "heating_window_conflict",
    "ac_window_conflict",
    "lights_nobody_home",
    "blinds_late",
    "energy_spike",
    "solar_heating_conflict",
    "ac_openplan_window",
    "lights_openplan_nobody_home",
]


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Violation:
    """A single rule violation detected by the rule engine."""

    rule: str
    room: str
    severity: Literal["contradiction", "suggestion"]
    message: str


@dataclass
class RuleConfig:
    """Per-rule enable/disable flag and configurable parameters."""

    enabled: bool = True
    params: dict = field(default_factory=dict)


@dataclass
class CustomRule:
    """A user-defined or reflection-suggested threshold rule for a single device type."""

    name: str
    device_type: str
    sensor_class: str
    room: str
    state_key: str
    operator: str
    threshold: float
    severity: Literal["contradiction", "suggestion"]
    message_template: str
    enabled: bool
    approved: bool
    source: str


# ---------------------------------------------------------------------------
# State interpretation helpers — pure functions, no I/O
# ---------------------------------------------------------------------------


def is_light_on(state: dict) -> bool:
    """Return True if a light device is currently on.

    Checks OnOff/on keys for truthy values AND Brightness/brightness > 0.
    A light is considered on if:
    - OnOff is "1" or truthy (not "0"), or 'on' key is truthy
    - AND if brightness is present, it must be > 0
    """
    on_off = state.get("OnOff")
    on_key = state.get("on")

    if on_off is not None:
        is_on = str(on_off) not in ("0", "False", "false", "")
    elif on_key is not None:
        is_on = bool(on_key)
    else:
        # No on/off key found — not a light or unknown state
        return False

    if not is_on:
        return False

    # Check brightness if present — 0 brightness means effectively off
    brightness = state.get("Brightness", state.get("brightness"))
    if brightness is not None:
        try:
            return float(brightness) > 0
        except (ValueError, TypeError):
            pass

    return True


def is_window_open(state: dict) -> bool:
    """Return True if a window or door sensor reports open state.

    Checks Binary/is_on keys for "1", "True", or "true".
    """
    for key in ("Binary", "is_on"):
        val = state.get(key)
        if val is not None:
            return str(val) in ("1", "True", "true")
    return False


def is_heating_active(state: dict) -> bool:
    """Return True if a heating device is actively set to a non-zero temperature.

    Checks SetValue/temperature keys > 0.
    """
    for key in ("SetValue", "temperature"):
        val = state.get(key)
        if val is not None:
            try:
                return float(val) > 0
            except (ValueError, TypeError):
                pass
    return False


def is_blind_open(state: dict) -> bool:
    """Return True if a blind is open (Up-Down == "0").

    Up-Down "0" = up/open, "1" = down/closed. Returns False when state
    is unknown (no Up-Down value yet — safe default is assume closed).
    """
    val = state.get("Up-Down")
    if val == "0":
        return True
    return False


def is_ac_running(state: dict) -> bool:
    """Return True if an AC/climate device is running (not explicitly off).

    Checks mode and PowerOnOff keys. Returns False only if state clearly
    indicates the unit is powered off.
    """
    _off_values = {"PowerOnOff.OFF", "off", "aus", "0"}

    mode = state.get("mode")
    if mode is not None and str(mode) in _off_values:
        return False

    power = state.get("PowerOnOff")
    if power is not None and str(power) in _off_values:
        return False

    # If no AC-specific key found, treat as not running
    if mode is None and power is None:
        return False

    return True


# ---------------------------------------------------------------------------
# RuleEngine — reactive rule evaluation over in-memory state mirrors
# ---------------------------------------------------------------------------


class RuleEngine:
    """Reactive rule engine that evaluates house rules against StateStore device state.

    Lives on HAL because rules need device state from StateStore. HAL owns
    StateStore, so RuleEngine is a natural child. Created in HAL.__init__() to
    ensure the on_change listener is registered before any devices are added.

    Pure state rules (1, 2, 4, 5, 7, 8) are evaluated synchronously on every
    state change via _evaluate_sync(). Time-dependent rules (3: lights_nobody_home,
    6: solar_heating_conflict) run only in async evaluate(), called by the 60s
    monitor timer.
    """

    def __init__(self, state_store: "StateStore") -> None:
        self._state_store: StateStore = state_store
        # In-memory mirrors of device states and device infos
        self._states: dict[str, dict] = {}  # device_key -> current state dict
        self._devices: dict[str, DeviceInfo] = {}  # device_key -> DeviceInfo
        self._current_violations: list[Violation] = []
        self._rule_configs: dict[str, RuleConfig] = {}  # per-rule enable/disable + params
        self._custom_rules: list[CustomRule] = []
        # Optional dependencies — injected via setters after construction
        self._presence: PresenceQuery | None = None
        self._topology: TopologyQuery | None = None
        self._weather: WeatherSource | None = None
        self._energy: EnergySource | None = None
        self._config: ConfigSource | None = (
            None  # BrainConfig — provides latitude/longitude/timezone
        )
        # Incremental room index: room_key -> list of (device_key, info)
        # Avoids O(n) rebuild on every state change. Updated in register_device()
        # and _on_state_change(). State looked up via self._states[device_key] at eval time.
        self._room_index: dict[str, list[tuple[str, DeviceInfo]]] = {}

        # Register as on_change listener
        state_store.on_change(self._on_state_change)

    def register_device(self, info: "DeviceInfo") -> None:
        """Register a device so its info is available during rule evaluation.

        Called from add_platform() BEFORE populate_initial_state(). By the time
        _on_state_change fires, self._devices is already populated for all devices
        from this platform.
        Also updates _room_index so the incremental index is consistent.
        """
        key = info.id.key
        self._devices[key] = info
        room_key = info.platform_data.get("room_key", info.room.lower())
        if room_key not in self._room_index:
            self._room_index[room_key] = []
        # Avoid duplicates (e.g. if called again for same device)
        if not any(dk == key for dk, _ in self._room_index[room_key]):
            self._room_index[room_key].append((key, info))

    async def _on_state_change(
        self,
        info: "DeviceInfo",
        old_state: dict | None,
        new_state: dict,
        source: str,
    ) -> None:
        """StateStore on_change listener — updates mirror and re-evaluates sync rules.

        This is an async def method as required by the on_change signature:
        Callable[[DeviceInfo, dict | None, dict, str], Awaitable[None]].
        However, _evaluate_sync is fully synchronous with no await points.

        Thread safety note: In single-threaded asyncio, _evaluate_sync() runs to
        completion before any other coroutine can read _current_violations. No lock
        needed -- cooperative scheduling guarantees atomicity of the reference
        replacement.
        """
        key = info.id.key
        self._states[key] = new_state
        # Keep room index up to date if the device is not yet registered
        # (handles edge case where state arrives before register_device)
        if key not in self._devices:
            self._devices[key] = info
            room_key = info.platform_data.get("room_key", info.room.lower())
            if room_key not in self._room_index:
                self._room_index[room_key] = []
            if not any(dk == key for dk, _ in self._room_index[room_key]):
                self._room_index[room_key].append((key, info))
        self._evaluate_sync()

    def _evaluate_sync(self) -> None:
        """Evaluate pure state rules (1, 2, 4, 5, 7, 8) against in-memory mirrors.

        Only pure state rules (1, 2, 4, 5, 7, 8) are evaluated here. Rules 3
        (lights_nobody_home) and 6 (solar_heating_conflict) are async-only --
        see evaluate().

        Reads only from in-memory dicts (self._states, self._devices, etc.),
        never from SQLite or network. Called on every state change, so must be
        fast for large device registries.
        """
        violations: list[Violation] = []

        # Build room_devices from incremental _room_index (O(n) but reads pre-built index).
        # State is looked up fresh from self._states to avoid stale cached state dicts.
        # Format: room_key -> list of (info, current_state_dict) — same shape as before.
        room_devices: dict[str, list[tuple[Any, dict]]] = {}
        for room_key, device_entries in self._room_index.items():
            room_devices[room_key] = [
                (info, self._states.get(dk, {})) for dk, info in device_entries
            ]

        # ------------------------------------------------------------------
        # Rule 1: heating_window_conflict
        # Severity: contradiction (heating + open window in same room)
        # ------------------------------------------------------------------
        if self._rule_configs.get("heating_window_conflict", RuleConfig()).enabled:
            for room_key, devices in room_devices.items():
                heated = any(is_heating_active(s) for _, s in devices)
                if not heated:
                    continue
                open_windows = [info for info, s in devices if is_window_open(s)]
                for info in open_windows:
                    display_room = info.room
                    violations.append(
                        Violation(
                            rule="heating_window_conflict",
                            room=room_key,
                            severity="contradiction",
                            message=(
                                f"Heizung in {display_room} läuft, aber Fenster "
                                f"'{info.name}' ist offen. Soll ich handeln?"
                            ),
                        )
                    )

        # ------------------------------------------------------------------
        # Rule 2: ac_window_conflict
        # Severity: contradiction (AC running + open window in same room)
        # ------------------------------------------------------------------
        if self._rule_configs.get("ac_window_conflict", RuleConfig()).enabled:
            for room_key, devices in room_devices.items():
                ac_on = any(is_ac_running(s) for _, s in devices)
                if not ac_on:
                    continue
                open_windows = [info for info, s in devices if is_window_open(s)]
                for info in open_windows:
                    display_room = info.room
                    violations.append(
                        Violation(
                            rule="ac_window_conflict",
                            room=room_key,
                            severity="contradiction",
                            message=(
                                f"Klimaanlage in {display_room} läuft, aber Fenster "
                                f"'{info.name}' ist offen. Soll ich handeln?"
                            ),
                        )
                    )

        # Rule 3 (lights_nobody_home) is NOT evaluated here.
        # It is inherently time-dependent (2h threshold requires event_history query)
        # and ONLY runs in async evaluate(). Evaluating it here would produce false
        # positives at startup for any light that was already on.

        # ------------------------------------------------------------------
        # Rule 4: blinds_late
        # Severity: suggestion (blinds open late at night when someone is home)
        # Fires ONLY when someone is home — not when nobody is home.
        # Guard: if self._presence and not self._presence.nobody_home() (someone IS home)
        # ------------------------------------------------------------------
        if self._rule_configs.get("blinds_late", RuleConfig()).enabled:
            try:
                after_hour = int(
                    self._rule_configs.get("blinds_late", RuleConfig()).params.get("after_hour", 23)
                )
            except (TypeError, ValueError):
                logger.warning("Invalid blinds_late after_hour; using 23")
                after_hour = 23
            now = datetime.now()
            is_late_night = now.hour >= after_hour or now.hour < 6
            if is_late_night and self._presence and not self._presence.nobody_home():
                for room_key, devices in room_devices.items():
                    open_blinds = [info for info, s in devices if is_blind_open(s)]
                    for info in open_blinds:
                        display_room = info.room
                        violations.append(
                            Violation(
                                rule="blinds_late",
                                room=room_key,
                                severity="suggestion",
                                message=(
                                    f"Jalousien in {display_room} sind noch offen nach "
                                    f"{now.strftime('%H:%M')} Uhr. Soll ich handeln?"
                                ),
                            )
                        )

        # ------------------------------------------------------------------
        # Rule 5: energy_spike
        # Severity: suggestion (estimated watts above threshold)
        # ------------------------------------------------------------------
        if self._rule_configs.get("energy_spike", RuleConfig()).enabled:
            try:
                threshold_watts = float(
                    self._rule_configs.get("energy_spike", RuleConfig()).params.get(
                        "threshold_watts", 5000
                    )
                )
            except (TypeError, ValueError):
                logger.warning("Invalid energy_spike threshold_watts; using 5000")
                threshold_watts = 5000.0
            if self._energy and self._energy.estimated_watts > threshold_watts:
                violations.append(
                    Violation(
                        rule="energy_spike",
                        room="Haus",
                        severity="suggestion",
                        message=(
                            f"Hoher Energieverbrauch erkannt: "
                            f"{self._energy.estimated_watts:.0f}W. Soll ich handeln?"
                        ),
                    )
                )

        # Rule 6 (solar_heating_conflict) is NOT evaluated here.
        # Requires async weather call. Only runs in async evaluate().

        # ------------------------------------------------------------------
        # Rule 7: ac_openplan_window
        # Severity: contradiction (AC in room, open window in connected room)
        # Uses topology to find open-plan connected rooms.
        # ------------------------------------------------------------------
        if self._rule_configs.get("ac_openplan_window", RuleConfig()).enabled:
            if self._topology:
                for room_key, devices in room_devices.items():
                    ac_on = any(is_ac_running(s) for _, s in devices)
                    if not ac_on:
                        continue
                    connected_rooms = self._topology.get_open_to(room_key)
                    if not connected_rooms:
                        continue
                    for conn_room_key in connected_rooms:
                        conn_devices = room_devices.get(conn_room_key, [])
                        open_windows = [info for info, s in conn_devices if is_window_open(s)]
                        for info in open_windows:
                            # Use display room for the AC room
                            ac_display = devices[0][0].room if devices else room_key
                            violations.append(
                                Violation(
                                    rule="ac_openplan_window",
                                    room=room_key,
                                    severity="contradiction",
                                    message=(
                                        f"Klimaanlage in {ac_display} läuft, aber Fenster "
                                        f"in {conn_room_key} (offen verbunden) ist offen. "
                                        f"Soll ich handeln?"
                                    ),
                                )
                            )

        # ------------------------------------------------------------------
        # Rule 8: lights_openplan_nobody_home
        # Severity: contradiction (lights on in open-plan zone, nobody home)
        # NOTE: Guard MUST check both _topology and _presence before method call.
        # Rule 8 is NOT subject to Rule 3's startup false-positive concern:
        # Rule 3 has a TIME threshold (>2h), Rule 8 has NO time threshold.
        # If nobody is home and lights are on, that IS a valid violation.
        # ------------------------------------------------------------------
        if self._rule_configs.get("lights_openplan_nobody_home", RuleConfig()).enabled:
            if self._topology and self._presence and self._presence.nobody_home():
                notified_groups: set[frozenset[str]] = set()
                for room_key, devices in room_devices.items():
                    lit = any(is_light_on(s) for _, s in devices)
                    if not lit:
                        continue
                    connected_rooms = self._topology.get_open_to(room_key)
                    lit_connected = [
                        r
                        for r in (connected_rooms or [])
                        if any(is_light_on(s) for _, s in room_devices.get(r, []))
                    ]
                    if lit_connected:
                        group = frozenset([room_key] + lit_connected)
                        if group in notified_groups:
                            continue
                        notified_groups.add(group)
                        room_list = ", ".join(sorted(group))
                        display_room = devices[0][0].room if devices else room_key
                        violations.append(
                            Violation(
                                rule="lights_openplan_nobody_home",
                                room=room_key,
                                severity="contradiction",
                                message=(
                                    f"Niemand zuhause, aber Licht in offenen Räumen an: "
                                    f"{room_list}. Soll ich handeln?"
                                ),
                            )
                        )

        # ------------------------------------------------------------------
        # Custom rules evaluation
        # Only approved and enabled custom rules are evaluated.
        # Supported operators: >, <, >=, <=, ==, !=
        # Custom rules work with numeric state_key values only.
        # ------------------------------------------------------------------
        for custom_rule in self._custom_rules:
            if not (custom_rule.approved and custom_rule.enabled):
                continue
            if not self._rule_configs.get(custom_rule.name, RuleConfig()).enabled:
                continue
            for room_key, devices in room_devices.items():
                if custom_rule.room and custom_rule.room.lower() not in (
                    room_key,
                    devices[0][0].room.lower() if devices else "",
                ):
                    continue
                for info, state in devices:
                    if custom_rule.device_type and str(info.device_type) != custom_rule.device_type:
                        continue
                    if (
                        custom_rule.sensor_class
                        and str(info.sensor_class) != custom_rule.sensor_class
                    ):
                        continue
                    raw_val = state.get(custom_rule.state_key)
                    if raw_val is None:
                        continue
                    try:
                        val = float(raw_val)
                    except (ValueError, TypeError):
                        continue
                    threshold = custom_rule.threshold
                    triggered = False
                    if custom_rule.operator == ">" and val > threshold:
                        triggered = True
                    elif custom_rule.operator == "<" and val < threshold:
                        triggered = True
                    elif custom_rule.operator == ">=" and val >= threshold:
                        triggered = True
                    elif custom_rule.operator == "<=" and val <= threshold:
                        triggered = True
                    elif custom_rule.operator == "==" and val == threshold:
                        triggered = True
                    elif custom_rule.operator == "!=" and val != threshold:
                        triggered = True
                    if triggered:
                        msg = custom_rule.message_template.format(
                            room=info.room, device=info.name, value=val
                        )
                        violations.append(
                            Violation(
                                rule=custom_rule.name,
                                room=room_key,
                                severity=custom_rule.severity,
                                message=msg,
                            )
                        )

        self._current_violations = violations

    def get_contradictions(self) -> list[str]:
        """Return German violation messages for all contradiction-severity violations.

        Synchronous — reads from in-memory _current_violations only.
        """
        return [v.message for v in self._current_violations if v.severity == "contradiction"]

    def get_current_violations(self) -> list[Violation]:
        """Return a copy of all current violations (both severities).

        Synchronous — reads from in-memory _current_violations only.
        """
        return list(self._current_violations)

    # -- Optional dependency setters ------------------------------------------

    def set_presence(self, presence: PresenceQuery) -> None:
        """Inject PresenceManager for presence-based rules (4, 8)."""
        self._presence = presence

    def set_topology(self, topology: TopologyQuery) -> None:
        """Inject HouseTopology for open-plan rules (7, 8)."""
        self._topology = topology

    def set_weather_source(self, weather: WeatherSource) -> None:
        """Inject WeatherAdapter for solar rules (6)."""
        self._weather = weather

    def set_energy(self, energy: EnergySource) -> None:
        """Inject EnergyAwareness for energy spike rule (5)."""
        self._energy = energy

    def set_config(self, config: ConfigSource) -> None:
        """Inject BrainConfig for latitude/longitude/timezone (Rule 6)."""
        self._config = config

    def stop(self) -> None:
        """Cleanup (no-op — future-proofing for cleanup hooks)."""
        pass

    async def evaluate(self) -> list[Violation]:
        """Run all rules including time-dependent async rules.

        Called by the monitor's 60-second periodic timer. Handles Rules 3 and 6
        which require async operations (event history query and weather fetch).

        Uses local list pattern to avoid concurrent mutation:
        - Snapshot sync violations into a local list
        - Async rules append to the local list only
        - Final assignment is atomic from asyncio's perspective
        """
        # Run sync rules first -- this updates self._current_violations
        self._evaluate_sync()
        # Snapshot sync violations (copy, not reference)
        violations = list(self._current_violations)

        # ------------------------------------------------------------------
        # Rule 3: lights_nobody_home (async -- needs event_history)
        # NOT evaluated in _evaluate_sync() to avoid false positives at startup.
        # Lights already on at startup would trigger immediately without the
        # 2h threshold check. This periodic evaluate() runs within 60s of
        # monitor start, ensuring lights are checked with proper history.
        # ------------------------------------------------------------------
        if (
            self._rule_configs.get("lights_nobody_home", RuleConfig()).enabled
            and self._presence
            and self._presence.nobody_home()
        ):
            raw_hours = self._rule_configs.get("lights_nobody_home", RuleConfig()).params.get(
                "hours", 2.0
            )
            try:
                hours = float(raw_hours)
                minutes = int(hours * 60)
            except (TypeError, ValueError, OverflowError):
                logger.warning("Invalid lights_nobody_home hours; using 2.0")
                hours = 2.0
                minutes = 120
            events = await self._state_store.get_recent_events(minutes=minutes)

            # Find rooms where lights have been on for the full configured period
            # (i.e., the light was turned on BEFORE our lookback window — no
            # "light turned on" event within the window means it was already on)
            rooms_light_on: dict[str, str] = {}  # room_key -> display room name
            for key, info in self._devices.items():
                state = self._states.get(key, {})
                if not is_light_on(state):
                    continue
                room_key = info.platform_data.get("room_key", info.room.lower())
                rooms_light_on[room_key] = info.room

            # Check if there's a "light turned on" event in the window
            # (meaning the light was turned on recently, not the full `hours` ago)
            recently_turned_on_rooms: set[str] = set()
            for event in events:
                new_state = event.get("new_state", {})
                old_state = event.get("old_state") or {}
                # If OnOff changed to 1 or Brightness went from 0 to >0, light was turned on
                if is_light_on(new_state) and not is_light_on(old_state):
                    device_key = event.get("device_key", "")
                    info = self._devices.get(device_key)
                    if info:
                        room_key = info.platform_data.get("room_key", info.room.lower())
                        recently_turned_on_rooms.add(room_key)

            for room_key, display_room in rooms_light_on.items():
                if room_key not in recently_turned_on_rooms:
                    # Light has been on for at least `hours` (no turn-on event in window)
                    violations.append(
                        Violation(
                            rule="lights_nobody_home",
                            room=room_key,
                            severity="suggestion",
                            message=(
                                f"Licht in {display_room} ist seit über "
                                f"{hours:.0f} Stunden an, aber niemand ist "
                                f"zuhause. Soll ich handeln?"
                            ),
                        )
                    )

        # ------------------------------------------------------------------
        # Rule 6: solar_heating_conflict (async -- needs weather fetch)
        # Requires topology, weather adapter, and BrainConfig for lat/lon/tz.
        # Guarded by self._config -- if config not set, Rule 6 is silently skipped.
        # ------------------------------------------------------------------
        if (
            self._rule_configs.get("solar_heating_conflict", RuleConfig()).enabled
            and self._topology
            and self._weather
            and self._config
        ):
            try:
                weather_data = await self._weather.get_current()
                if weather_data is not None:
                    lat = self._config.home.latitude
                    lon = self._config.home.longitude
                    tz = self._config.home.timezone

                    room_devices: dict[str, list[tuple[Any, dict]]] = {}
                    for key, info in self._devices.items():
                        state = self._states.get(key, {})
                        rk = info.platform_data.get("room_key", info.room.lower())
                        if rk not in room_devices:
                            room_devices[rk] = []
                        room_devices[rk].append((info, state))

                    for room_key, devices in room_devices.items():
                        heated = any(is_heating_active(s) for _, s in devices)
                        if not heated:
                            continue
                        try:
                            solar = self._topology.assess_solar_exposure(
                                room_key, weather_data, lat, lon, tz
                            )
                            if solar.get("level") == "hoch":
                                display_room = devices[0][0].room if devices else room_key
                                violations.append(
                                    Violation(
                                        rule="solar_heating_conflict",
                                        room=room_key,
                                        severity="suggestion",
                                        message=(
                                            f"Starke Sonneneinstrahlung in {display_room}, "
                                            f"aber Heizung läuft. "
                                            f"Soll ich die Jalousien schließen?"
                                        ),
                                    )
                                )
                        except Exception as e:
                            import logging

                            logging.getLogger(__name__).debug(
                                "Rule 6 error for room %s: %s", room_key, e
                            )
            except Exception as e:
                import logging

                logging.getLogger(__name__).warning(
                    "RuleEngine: failed to fetch weather for Rule 6: %s", e
                )

        # Atomically replace (no await between build and assign)
        self._current_violations = violations
        return list(violations)

    async def configure_rule(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        hours: float | None = None,
        after_hour: int | None = None,
        threshold_watts: float | None = None,
    ) -> None:
        """Enable/disable or configure a built-in rule.

        Uses explicit keyword parameters (no **kwargs) for type safety and
        proper Strands tool schema generation.

        If all arguments are None, returns early (no-op guard to avoid
        unnecessary SQLite write and re-evaluation).

        This method is async because it persists to SQLite via StateStore.
        """
        if enabled is None and hours is None and after_hour is None and threshold_watts is None:
            return  # No-op guard

        current = self._rule_configs.get(name, RuleConfig())
        new_params = dict(current.params)

        if hours is not None:
            new_params["hours"] = hours
        if after_hour is not None:
            new_params["after_hour"] = after_hour
        if threshold_watts is not None:
            new_params["threshold_watts"] = threshold_watts

        new_enabled = current.enabled if enabled is None else enabled
        self._rule_configs[name] = RuleConfig(enabled=new_enabled, params=new_params)

        await self._state_store.save_rule_config(name, new_enabled, new_params)
        self._evaluate_sync()

        # Filter stale async-only violations: when an async-only rule (3 or 6) is
        # disabled, its violations are removed immediately rather than lingering for
        # up to 60 seconds until the next evaluate() cycle.
        self._current_violations = [
            v
            for v in self._current_violations
            if v.rule != name or self._rule_configs.get(name, RuleConfig()).enabled
        ]

    async def add_custom_rule(self, rule: CustomRule) -> None:
        """Add a new custom rule (unapproved) and persist it to SQLite."""
        self._custom_rules.append(rule)
        await self._state_store.save_custom_rule(rule.__dict__)

    async def load_custom_rules(self) -> None:
        """Load custom rules and rule configs from SQLite.

        Called after populate_initial_state(). Initial evaluations with empty
        custom rules during population are acceptable -- they'll be re-evaluated
        on the next state change after this method completes.
        """
        # Load rule configs
        configs = await self._state_store.load_rule_configs()
        for rule_name, (rule_enabled, params) in configs.items():
            self._rule_configs[rule_name] = RuleConfig(enabled=rule_enabled, params=params)

        # Load custom rules
        rows = await self._state_store.load_custom_rules()
        self._custom_rules = []
        for row in rows:
            self._custom_rules.append(
                CustomRule(
                    name=row["name"],
                    device_type=row["device_type"],
                    sensor_class=row["sensor_class"],
                    room=row["room"],
                    state_key=row["state_key"],
                    operator=row["operator"],
                    threshold=row["threshold"],
                    severity=row["severity"],
                    message_template=row["message_template"],
                    enabled=row["enabled"],
                    approved=row["approved"],
                    source=row["source"],
                )
            )

    async def approve_custom_rule(self, name: str) -> bool:
        """Approve a reflection-suggested custom rule for active evaluation.

        Sets approved=True and enabled=True on the in-memory object and persists
        the change to SQLite.

        Returns True if the rule was found and updated, False if no matching rule.
        """
        found = False
        for rule in self._custom_rules:
            if rule.name == name:
                rule.approved = True
                rule.enabled = True
                found = True
                break

        if not found:
            return False

        await self._state_store.update_custom_rule(name, approved=True, enabled=True)
        return True
