"""Comprehensive tests for the RuleEngine and helper functions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from src.hal.rules import (
    BUILT_IN_RULES,
    CustomRule,
    RuleConfig,
    RuleEngine,
    Violation,
    is_ac_running,
    is_blind_open,
    is_heating_active,
    is_light_on,
    is_window_open,
)


# ─── State helper tests ────────────────────────────────────────────────────


def test_is_light_on_onoff_key():
    assert is_light_on({"OnOff": "1"}) is True
    assert is_light_on({"OnOff": "0"}) is False


def test_is_light_on_brightness_zero():
    assert is_light_on({"OnOff": "1", "Brightness": 0}) is False


def test_is_light_on_brightness_nonzero():
    assert is_light_on({"OnOff": "1", "Brightness": 50}) is True


def test_is_light_on_on_key():
    assert is_light_on({"on": True}) is True
    assert is_light_on({"on": False}) is False


def test_is_light_on_no_key():
    assert is_light_on({}) is False
    assert is_light_on({"other": "value"}) is False


def test_is_window_open_binary():
    assert is_window_open({"Binary": "1"}) is True
    assert is_window_open({"Binary": "0"}) is False
    assert is_window_open({"Binary": "True"}) is True
    assert is_window_open({"Binary": "true"}) is True


def test_is_window_open_is_on():
    assert is_window_open({"is_on": "1"}) is True
    assert is_window_open({"is_on": "0"}) is False


def test_is_window_open_no_key():
    assert is_window_open({}) is False


def test_is_heating_active_setvalue():
    assert is_heating_active({"SetValue": 22}) is True
    assert is_heating_active({"SetValue": 0}) is False
    assert is_heating_active({"SetValue": "21.5"}) is True


def test_is_heating_active_temperature():
    assert is_heating_active({"temperature": 20}) is True
    assert is_heating_active({"temperature": 0}) is False


def test_is_blind_open():
    assert is_blind_open({"Up-Down": "0"}) is True
    assert is_blind_open({"Up-Down": "1"}) is False
    assert is_blind_open({}) is False


def test_is_ac_running_mode():
    assert is_ac_running({"mode": "cool"}) is True
    assert is_ac_running({"mode": "off"}) is False
    assert is_ac_running({"mode": "aus"}) is False
    assert is_ac_running({"mode": "PowerOnOff.OFF"}) is False


def test_is_ac_running_poweronoff():
    assert is_ac_running({"PowerOnOff": "PowerOnOff.ON"}) is True
    assert is_ac_running({"PowerOnOff": "PowerOnOff.OFF"}) is False


def test_is_ac_running_no_keys():
    assert is_ac_running({}) is False


# ─── BUILT_IN_RULES constant ───────────────────────────────────────────────


def test_built_in_rules_has_eight_entries():
    assert len(BUILT_IN_RULES) == 8


def test_built_in_rules_contains_expected():
    assert "heating_window_conflict" in BUILT_IN_RULES
    assert "ac_window_conflict" in BUILT_IN_RULES
    assert "lights_nobody_home" in BUILT_IN_RULES
    assert "lights_openplan_nobody_home" in BUILT_IN_RULES


# ─── RuleEngine fixtures ───────────────────────────────────────────────────


def _make_state_store():
    """Create a minimal StateStore mock with on_change registration."""
    store = MagicMock()
    store.on_change = MagicMock()  # captures the callback
    store.get_recent_events = AsyncMock(return_value=[])
    store.load_rule_configs = AsyncMock(return_value={})
    store.load_custom_rules = AsyncMock(return_value=[])
    store.save_rule_config = AsyncMock()
    store.save_custom_rule = AsyncMock(return_value=1)
    store.update_custom_rule = AsyncMock(return_value=True)
    return store


def _make_device_info(key: str, room: str, device_type: str = "light", room_key: str | None = None):
    """Create a mock DeviceInfo."""
    info = MagicMock()
    info.id.key = key
    info.room = room
    info.name = key
    info.device_type = device_type
    info.sensor_class = "generic"
    info.platform_data = {"room_key": room_key or room.lower()} if room_key or room else {}
    return info


def _make_engine_with_devices(devices: list[tuple[str, str, dict]]) -> RuleEngine:
    """Create a RuleEngine and register devices with initial states.

    devices: list of (key, room, state_dict)
    """
    store = _make_state_store()
    engine = RuleEngine(store)

    for key, room, state in devices:
        info = _make_device_info(key, room)
        engine.register_device(info)
        engine._states[key] = state

    return engine


# ─── Rule 1: heating_window_conflict ─────────────────────────────────────


async def test_rule1_heating_and_window_same_room():
    """Heating on + open window in same room -> contradiction."""
    engine = _make_engine_with_devices(
        [
            ("heat1", "Schlafzimmer", {"SetValue": 22}),
            ("win1", "Schlafzimmer", {"Binary": "1"}),
        ]
    )
    engine._evaluate_sync()
    violations = engine.get_contradictions()
    assert len(violations) == 1
    assert "Schlafzimmer" in violations[0] or "heating_window_conflict" in str(
        engine._current_violations
    )


async def test_rule1_different_rooms_no_conflict():
    """Heating in Room A, open window in Room B -> no conflict."""
    engine = _make_engine_with_devices(
        [
            ("heat1", "Küche", {"SetValue": 22}),
            ("win1", "Schlafzimmer", {"Binary": "1"}),
        ]
    )
    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "heating_window_conflict"]
    assert len(violations) == 0


# ─── Rule 2: ac_window_conflict ──────────────────────────────────────────


async def test_rule2_ac_and_window_same_room():
    """AC running + open window in same room -> contradiction."""
    engine = _make_engine_with_devices(
        [
            ("ac1", "Büro", {"mode": "cool"}),
            ("win1", "Büro", {"Binary": "1"}),
        ]
    )
    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "ac_window_conflict"]
    assert len(violations) == 1


async def test_rule2_different_rooms_no_conflict():
    """AC in Room A, window in Room B -> no conflict."""
    engine = _make_engine_with_devices(
        [
            ("ac1", "Büro", {"mode": "cool"}),
            ("win1", "Wohnzimmer", {"Binary": "1"}),
        ]
    )
    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "ac_window_conflict"]
    assert len(violations) == 0


# ─── Rule 3: lights_nobody_home (async only) ──────────────────────────────


async def test_rule3_not_in_evaluate_sync():
    """lights_nobody_home must NOT appear in _evaluate_sync results."""
    engine = _make_engine_with_devices(
        [
            ("light1", "Wohnzimmer", {"OnOff": "1", "Brightness": 50}),
        ]
    )
    presence = MagicMock()
    presence.nobody_home.return_value = True
    engine.set_presence(presence)

    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "lights_nobody_home"]
    assert len(violations) == 0, "Rule 3 must not fire in _evaluate_sync"


async def test_rule3_fires_in_async_evaluate_with_old_events():
    """lights_nobody_home fires in evaluate() when presence says nobody home and no recent turn-on."""
    store = _make_state_store()
    # get_recent_events returns empty list (no recent light-on events)
    store.get_recent_events = AsyncMock(return_value=[])
    engine = RuleEngine(store)

    info = _make_device_info("light1", "Wohnzimmer")
    engine.register_device(info)
    engine._states["light1"] = {"OnOff": "1", "Brightness": 50}

    presence = MagicMock()
    presence.nobody_home.return_value = True
    engine.set_presence(presence)

    violations = await engine.evaluate()
    rule3_violations = [v for v in violations if v.rule == "lights_nobody_home"]
    assert len(rule3_violations) == 1


async def test_rule3_uses_configurable_hours():
    """Rule 3 reads hours param from rule_configs (no hardcoded 120 minutes)."""
    store = _make_state_store()
    store.get_recent_events = AsyncMock(return_value=[])
    engine = RuleEngine(store)
    engine._rule_configs["lights_nobody_home"] = RuleConfig(enabled=True, params={"hours": 1.0})

    info = _make_device_info("light1", "Wohnzimmer")
    engine.register_device(info)
    engine._states["light1"] = {"OnOff": "1"}

    presence = MagicMock()
    presence.nobody_home.return_value = True
    engine.set_presence(presence)

    await engine.evaluate()
    # Verify get_recent_events was called with 60 minutes (1.0 * 60)
    store.get_recent_events.assert_awaited_with(minutes=60)


# ─── Rule 4: blinds_late ──────────────────────────────────────────────────


async def test_rule4_fires_only_when_someone_home():
    """blinds_late fires ONLY when someone IS home (not when nobody home)."""
    from unittest.mock import patch
    from datetime import datetime

    engine = _make_engine_with_devices(
        [
            ("blind1", "Wohnzimmer", {"Up-Down": "0"}),
        ]
    )
    presence = MagicMock()
    presence.nobody_home.return_value = False  # Someone IS home
    engine.set_presence(presence)

    # Mock to late night hour
    with patch("src.hal.rules.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 29, 23, 30)
        engine._evaluate_sync()

    violations = [v for v in engine._current_violations if v.rule == "blinds_late"]
    assert len(violations) == 1


async def test_rule4_no_fire_when_nobody_home():
    """blinds_late does NOT fire when nobody is home."""
    from unittest.mock import patch
    from datetime import datetime

    engine = _make_engine_with_devices(
        [
            ("blind1", "Wohnzimmer", {"Up-Down": "0"}),
        ]
    )
    presence = MagicMock()
    presence.nobody_home.return_value = True  # Nobody home
    engine.set_presence(presence)

    with patch("src.hal.rules.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 29, 23, 30)
        engine._evaluate_sync()

    violations = [v for v in engine._current_violations if v.rule == "blinds_late"]
    assert len(violations) == 0


async def test_rule4_no_crash_when_presence_none():
    """blinds_late does not crash when _presence is None."""
    from unittest.mock import patch
    from datetime import datetime

    engine = _make_engine_with_devices(
        [
            ("blind1", "Wohnzimmer", {"Up-Down": "0"}),
        ]
    )
    # No presence set

    with patch("src.hal.rules.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 3, 29, 23, 30)
        engine._evaluate_sync()  # Should not raise

    violations = [v for v in engine._current_violations if v.rule == "blinds_late"]
    assert len(violations) == 0


# ─── Rule 5: energy_spike ─────────────────────────────────────────────────


async def test_rule5_energy_spike_fires():
    """energy_spike fires when estimated_watts > threshold."""
    engine = _make_engine_with_devices([])
    energy = MagicMock()
    energy.estimated_watts = 6000
    engine.set_energy(energy)

    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "energy_spike"]
    assert len(violations) == 1


async def test_rule5_energy_below_threshold_no_fire():
    """energy_spike does NOT fire when watts below threshold."""
    engine = _make_engine_with_devices([])
    energy = MagicMock()
    energy.estimated_watts = 1000
    engine.set_energy(energy)

    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "energy_spike"]
    assert len(violations) == 0


# ─── Rule 6: solar_heating_conflict (async only) ─────────────────────────


async def test_rule6_not_in_evaluate_sync():
    """solar_heating_conflict must NOT appear in _evaluate_sync results."""
    engine = _make_engine_with_devices(
        [
            ("heat1", "Wohnzimmer", {"SetValue": 22}),
        ]
    )
    topology = MagicMock()
    weather = MagicMock()
    config = MagicMock()
    engine.set_topology(topology)
    engine.set_weather_source(weather)
    engine.set_config(config)

    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "solar_heating_conflict"]
    assert len(violations) == 0, "Rule 6 must not fire in _evaluate_sync"


async def test_rule6_fires_in_evaluate_when_solar_high():
    """solar_heating_conflict fires in evaluate() when solar level is 'hoch'."""
    store = _make_state_store()
    engine = RuleEngine(store)

    info = _make_device_info("heat1", "Wohnzimmer")
    engine.register_device(info)
    engine._states["heat1"] = {"SetValue": 22}

    topology = MagicMock()
    topology.get_open_to.return_value = []
    topology.assess_solar_exposure.return_value = {"level": "hoch"}

    weather = MagicMock()
    weather.get_current = AsyncMock(return_value={"solar_irradiance": 800})

    config = MagicMock()
    config.home.latitude = 48.7
    config.home.longitude = 9.3
    config.home.timezone = "Europe/Berlin"

    engine.set_topology(topology)
    engine.set_weather_source(weather)
    engine.set_config(config)

    violations = await engine.evaluate()
    rule6_violations = [v for v in violations if v.rule == "solar_heating_conflict"]
    assert len(rule6_violations) == 1


# ─── Rule 7: ac_openplan_window ──────────────────────────────────────────


async def test_rule7_ac_openplan_window_conflict():
    """AC in Room A + open window in connected Room B -> contradiction."""
    store = _make_state_store()
    engine = RuleEngine(store)

    ac_info = _make_device_info("ac1", "Büro", room_key="buero")
    win_info = _make_device_info("win1", "Flur", room_key="flur")
    engine.register_device(ac_info)
    engine.register_device(win_info)
    engine._states["ac1"] = {"mode": "cool"}
    engine._states["win1"] = {"Binary": "1"}

    topology = MagicMock()
    topology.get_open_to.side_effect = lambda room: ["flur"] if room == "buero" else []
    engine.set_topology(topology)

    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "ac_openplan_window"]
    assert len(violations) == 1


async def test_rule7_no_conflict_when_window_closed():
    """AC in Room A, closed window in connected Room B -> no conflict."""
    store = _make_state_store()
    engine = RuleEngine(store)

    ac_info = _make_device_info("ac1", "Büro", room_key="buero")
    win_info = _make_device_info("win1", "Flur", room_key="flur")
    engine.register_device(ac_info)
    engine.register_device(win_info)
    engine._states["ac1"] = {"mode": "cool"}
    engine._states["win1"] = {"Binary": "0"}  # closed

    topology = MagicMock()
    topology.get_open_to.side_effect = lambda room: ["flur"] if room == "buero" else []
    engine.set_topology(topology)

    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "ac_openplan_window"]
    assert len(violations) == 0


# ─── Rule 8: lights_openplan_nobody_home ─────────────────────────────────


async def test_rule8_lights_openplan_nobody_home():
    """Lights on in open-plan zone, nobody home -> contradiction."""
    store = _make_state_store()
    engine = RuleEngine(store)

    light1 = _make_device_info("light1", "Wohnzimmer", room_key="wohnzimmer")
    light2 = _make_device_info("light2", "Esszimmer", room_key="esszimmer")
    engine.register_device(light1)
    engine.register_device(light2)
    engine._states["light1"] = {"OnOff": "1", "Brightness": 50}
    engine._states["light2"] = {"OnOff": "1", "Brightness": 30}

    topology = MagicMock()
    topology.get_open_to.side_effect = lambda room: (
        ["esszimmer"] if room == "wohnzimmer" else (["wohnzimmer"] if room == "esszimmer" else [])
    )
    engine.set_topology(topology)

    presence = MagicMock()
    presence.nobody_home.return_value = True
    engine.set_presence(presence)

    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "lights_openplan_nobody_home"]
    assert len(violations) == 1


async def test_rule8_no_crash_when_presence_none():
    """Rule 8 guard checks _topology AND _presence before method call."""
    store = _make_state_store()
    engine = RuleEngine(store)

    light1 = _make_device_info("light1", "Wohnzimmer", room_key="wohnzimmer")
    engine.register_device(light1)
    engine._states["light1"] = {"OnOff": "1"}

    topology = MagicMock()
    topology.get_open_to.return_value = ["esszimmer"]
    engine.set_topology(topology)
    # _presence is NOT set (None) — must not crash

    engine._evaluate_sync()  # Should not raise AttributeError


async def test_rule8_no_fire_when_someone_home():
    """Rule 8 does NOT fire when someone is home."""
    store = _make_state_store()
    engine = RuleEngine(store)

    light1 = _make_device_info("light1", "Wohnzimmer", room_key="wohnzimmer")
    engine.register_device(light1)
    engine._states["light1"] = {"OnOff": "1"}

    topology = MagicMock()
    topology.get_open_to.return_value = ["esszimmer"]
    engine.set_topology(topology)

    presence = MagicMock()
    presence.nobody_home.return_value = False  # Someone IS home
    engine.set_presence(presence)

    engine._evaluate_sync()
    violations = [v for v in engine._current_violations if v.rule == "lights_openplan_nobody_home"]
    assert len(violations) == 0


# ─── get_contradictions and get_current_violations ───────────────────────


async def test_get_contradictions_returns_only_contradictions():
    """get_contradictions() returns only severity='contradiction' messages."""
    engine = _make_engine_with_devices(
        [
            ("heat1", "Schlafzimmer", {"SetValue": 22}),
            ("win1", "Schlafzimmer", {"Binary": "1"}),
        ]
    )
    engine._evaluate_sync()
    contradictions = engine.get_contradictions()
    assert isinstance(contradictions, list)
    # All returned should be contradiction strings
    for c in contradictions:
        assert isinstance(c, str)


async def test_get_current_violations_returns_all():
    """get_current_violations() returns all violations."""
    engine = _make_engine_with_devices(
        [
            ("heat1", "Schlafzimmer", {"SetValue": 22}),
            ("win1", "Schlafzimmer", {"Binary": "1"}),
        ]
    )
    engine._evaluate_sync()
    violations = engine.get_current_violations()
    assert isinstance(violations, list)
    assert all(isinstance(v, Violation) for v in violations)


# ─── configure_rule ──────────────────────────────────────────────────────


async def test_configure_rule_disable():
    """Disabling a rule removes its violations immediately."""
    store = _make_state_store()
    engine = RuleEngine(store)
    info = _make_device_info("heat1", "Schlafzimmer")
    win_info = _make_device_info("win1", "Schlafzimmer")
    engine.register_device(info)
    engine.register_device(win_info)
    engine._states["heat1"] = {"SetValue": 22}
    engine._states["win1"] = {"Binary": "1"}
    engine._evaluate_sync()
    assert len(engine._current_violations) == 1

    await engine.configure_rule("heating_window_conflict", enabled=False)
    violations = [v for v in engine._current_violations if v.rule == "heating_window_conflict"]
    assert len(violations) == 0


async def test_configure_rule_noop_when_all_none():
    """configure_rule returns early (no-op) when all params are None."""
    store = _make_state_store()
    engine = RuleEngine(store)
    await engine.configure_rule("heating_window_conflict")
    store.save_rule_config.assert_not_awaited()


# ─── Custom rules lifecycle ──────────────────────────────────────────────


async def test_add_and_evaluate_custom_rule():
    """Custom rule added and approved is evaluated in _evaluate_sync."""
    store = _make_state_store()
    engine = RuleEngine(store)

    info = _make_device_info("temp1", "Keller")
    engine.register_device(info)
    engine._states["temp1"] = {"temperature": 30.0}

    rule = CustomRule(
        name="keller_temp_high",
        device_type="",
        sensor_class="",
        room="keller",
        state_key="temperature",
        operator=">",
        threshold=25.0,
        severity="suggestion",
        message_template="Temperatur in {room}: {value}°C",
        enabled=True,
        approved=True,
        source="user",
    )
    await engine.add_custom_rule(rule)
    engine._evaluate_sync()

    violations = [v for v in engine._current_violations if v.rule == "keller_temp_high"]
    assert len(violations) == 1


async def test_approve_custom_rule_returns_true():
    """approve_custom_rule returns True on success, False if not found."""
    store = _make_state_store()
    engine = RuleEngine(store)

    rule = CustomRule(
        name="test_rule",
        device_type="",
        sensor_class="",
        room="",
        state_key="val",
        operator=">",
        threshold=0,
        severity="suggestion",
        message_template="Test",
        enabled=False,
        approved=False,
        source="reflection",
    )
    engine._custom_rules.append(rule)

    result = await engine.approve_custom_rule("test_rule")
    assert result is True
    assert rule.approved is True
    assert rule.enabled is True


async def test_approve_custom_rule_returns_false_if_not_found():
    """approve_custom_rule returns False for unknown rule name."""
    store = _make_state_store()
    engine = RuleEngine(store)

    result = await engine.approve_custom_rule("nonexistent_rule")
    assert result is False


# ─── State change re-evaluation ──────────────────────────────────────────


async def test_on_state_change_triggers_evaluate():
    """State change via on_change listener updates violations."""
    store = _make_state_store()
    # Capture the registered callback
    captured_callback = []
    store.on_change.side_effect = lambda cb: captured_callback.append(cb)
    engine = RuleEngine(store)
    assert len(captured_callback) == 1

    info = _make_device_info("heat1", "Schlafzimmer")
    win_info = _make_device_info("win1", "Schlafzimmer")
    engine.register_device(info)
    engine.register_device(win_info)
    engine._states["win1"] = {"Binary": "1"}
    engine._states["heat1"] = {"SetValue": 0}

    # No violations yet
    engine._evaluate_sync()
    assert len(engine._current_violations) == 0

    # Trigger state change — turn heating on
    await captured_callback[0](info, {"SetValue": 0}, {"SetValue": 22}, "external")
    violations = [v for v in engine._current_violations if v.rule == "heating_window_conflict"]
    assert len(violations) == 1


# ─── evaluate() local list pattern ───────────────────────────────────────


async def test_evaluate_returns_complete_violations():
    """evaluate() returns both sync and async violations."""
    store = _make_state_store()
    store.get_recent_events = AsyncMock(return_value=[])
    engine = RuleEngine(store)

    # Set up heating+window conflict (sync rule)
    heat_info = _make_device_info("heat1", "Schlafzimmer")
    win_info = _make_device_info("win1", "Schlafzimmer")
    engine.register_device(heat_info)
    engine.register_device(win_info)
    engine._states["heat1"] = {"SetValue": 22}
    engine._states["win1"] = {"Binary": "1"}

    violations = await engine.evaluate()
    assert any(v.rule == "heating_window_conflict" for v in violations)
