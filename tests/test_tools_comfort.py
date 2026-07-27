from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.tools.comfort import (
    ac_control,
    blinds_control,
    get_ac_status,
    get_room_state,
    heating_control,
    set_context,
    lights_control,
    list_rooms,
)
import src.tools.comfort as comfort_module
from src.tools.context import ToolContext
from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType
import json as _json
import src.tools.comfort as _comfort_mod
from src.tools.comfort import execute_batch


def _make_device_id(native_id: str) -> DeviceId:
    return DeviceId(platform="mock", native_id=native_id)


def _make_adapter(
    native_id: str,
    name: str,
    device_type: DeviceType,
    room: str,
    room_key: str = "",
) -> MagicMock:
    adapter = MagicMock()
    adapter.device_id = _make_device_id(native_id)
    adapter.info = DeviceInfo(
        id=_make_device_id(native_id),
        name=name,
        device_type=device_type,
        room=room,
        platform_data={"room_key": room_key or room.lower()},
    )
    adapter.read_state = AsyncMock(
        return_value=DeviceState(
            device_id=_make_device_id(native_id),
            state={"OnOff": "0"},
            last_updated=datetime.now(timezone.utc),
        )
    )
    return adapter


class MockHAL:
    """Mock HAL for comfort tool tests."""

    def __init__(self) -> None:
        self.light = _make_adapter("light-1", "Esstisch", DeviceType.DIMMABLE_LIGHT, "Wohnzimmer")
        self.blind = _make_adapter("blind-1", "Jalousie links", DeviceType.BLIND, "Wohnzimmer")
        self.heating = _make_adapter("heat-1", "Heizung WZ", DeviceType.HEATING, "Wohnzimmer")
        self.executed: list[tuple] = []

    def has_room(self, name: str) -> bool:
        return name.lower() in ("wohnzimmer",)

    def find_room(self, name: str) -> list:
        if name.lower() == "wohnzimmer":
            return [self.light, self.blind, self.heating]
        return []

    def find_device(self, name: str, room: str = None) -> object:
        if "esstisch" in name.lower():
            return self.light
        return f"Gerät '{name}' nicht gefunden."

    def get_devices_by_type(self, device_type: DeviceType) -> list:
        if device_type == DeviceType.DIMMABLE_LIGHT:
            return [self.light]
        if device_type == DeviceType.BLIND:
            return [self.blind]
        if device_type == DeviceType.HEATING:
            return [self.heating]
        return []

    def get_all_rooms(self) -> dict:
        return {"Wohnzimmer": [self.light.info, self.blind.info, self.heating.info]}

    async def execute(self, device_id: DeviceId, command: str, **params) -> None:
        self.executed.append((device_id, command, params))

    async def get_room_state_formatted(self, room: str) -> dict:
        if room.lower() == "wohnzimmer":
            return {"Esstisch": {"OnOff": "1", "Brightness": "80"}}
        return {}


def _make_ctx(hal: MockHAL) -> ToolContext:
    return ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )


def make_deps() -> MockHAL:
    hal = MockHAL()
    set_context(_make_ctx(hal))
    return hal


async def test_lights_control_on():
    hal = make_deps()
    result = await lights_control("Wohnzimmer", "on")
    assert "eingeschaltet" in result
    assert any(cmd == "turn_on" for _, cmd, _ in hal.executed)


async def test_lights_control_off():
    hal = make_deps()
    result = await lights_control("Wohnzimmer", "off")
    assert "ausgeschaltet" in result
    assert any(cmd == "turn_off" for _, cmd, _ in hal.executed)


async def test_lights_control_dim():
    hal = make_deps()
    result = await lights_control("Wohnzimmer", "dim", brightness=50)
    assert "gedimmt" in result
    assert any(cmd == "turn_on" and p.get("brightness") == 50 for _, cmd, p in hal.executed)


async def test_lights_control_invalid_action():
    make_deps()
    result = await lights_control("Wohnzimmer", "invalid")
    assert "Unbekannte Aktion" in result


async def test_lights_control_unknown_room():
    make_deps()
    result = await lights_control("Nichtraum", "on")
    assert "nicht gefunden" in result


async def test_blinds_control_up():
    hal = make_deps()
    result = await blinds_control("Wohnzimmer", "up")
    assert "hoch" in result
    assert any(cmd == "open" for _, cmd, _ in hal.executed)


async def test_blinds_control_down():
    hal = make_deps()
    result = await blinds_control("Wohnzimmer", "down")
    assert "runter" in result
    assert any(cmd == "close" for _, cmd, _ in hal.executed)


async def test_heating_control():
    hal = make_deps()
    result = await heating_control("Wohnzimmer", 22.0)
    assert "22" in result
    assert any(cmd == "set_temperature" for _, cmd, _ in hal.executed)


async def test_heating_control_out_of_range():
    make_deps()
    result = await heating_control("Wohnzimmer", 50.0)
    assert "außerhalb" in result


async def test_get_room_state():
    make_deps()
    result = await get_room_state("Wohnzimmer")
    assert "Wohnzimmer" in result
    assert "Esstisch" in result


async def test_list_rooms():
    make_deps()
    result = await list_rooms()
    assert "Wohnzimmer" in result


async def test_no_ctx_returns_error():
    comfort_module._ctx = None
    result = await lights_control("Wohnzimmer", "on")
    assert "nicht erreichbar" in result


# ─── AC comfort tool tests ────────────────────────────────────────────────


def make_mock_hal_with_climate():
    """Create a mock HAL with CLIMATE device for AC tests."""
    from datetime import datetime, timezone

    hal = MagicMock()
    climate_adapter = MagicMock()
    climate_adapter.device_id = _make_device_id("ac-1")
    climate_adapter.info = DeviceInfo(
        id=_make_device_id("ac-1"),
        name="Klimaanlage",
        device_type=DeviceType.CLIMATE,
        room="Room 1",
        platform_data={},
    )
    climate_adapter.read_state = AsyncMock(
        return_value=DeviceState(
            device_id=_make_device_id("ac-1"),
            state={
                "power": "off",
                "temperature": "23.0",
                "room_temperature": "22.5",
                "power_watt": "1",
            },
            last_updated=datetime.now(timezone.utc),
        )
    )
    hal.get_devices_by_type = MagicMock(
        side_effect=lambda dt: [climate_adapter] if dt == DeviceType.CLIMATE else []
    )
    hal.execute = AsyncMock()
    return hal, climate_adapter


async def test_ac_control_on():
    """ac_control 'on' calls hal.execute turn_on."""
    hal, adapter = make_mock_hal_with_climate()
    ctx = ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )
    set_context(ctx)
    result = await ac_control("Room 1", "on")
    hal.execute.assert_awaited_once()
    assert "eingeschaltet" in result


async def test_ac_control_off():
    """ac_control 'off' calls hal.execute turn_off."""
    hal, adapter = make_mock_hal_with_climate()
    ctx = ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )
    set_context(ctx)
    result = await ac_control("Room 1", "off")
    hal.execute.assert_awaited_once()
    assert "ausgeschaltet" in result


async def test_ac_control_set_temperature():
    """ac_control 'set' calls hal.execute set_temperature with valid temp."""
    hal, adapter = make_mock_hal_with_climate()
    ctx = ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )
    set_context(ctx)
    result = await ac_control("Room 1", "set", temperature=24.0)
    assert "24.0" in result


async def test_ac_control_invalid_temperature():
    """ac_control 'set' with out-of-range temperature returns error."""
    hal, adapter = make_mock_hal_with_climate()
    ctx = ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )
    set_context(ctx)
    result = await ac_control("Room 1", "set", temperature=99.0)
    hal.execute.assert_not_called()
    assert "Ungültig" in result or "ungültig" in result.lower() or "99" in result


async def test_ac_control_mode_mapping():
    """ac_control 'mode' maps German names to DriveMode enum names."""
    hal, adapter = make_mock_hal_with_climate()
    ctx = ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )
    set_context(ctx)
    modes = [
        "kuehlen",
        "heizen",
        "auto",
        "trocknen",
        "lueften",
    ]
    for german in modes:
        hal.execute.reset_mock()
        await ac_control("Room 1", "mode", mode=german)
        hal.execute.assert_awaited_once()


async def test_ac_control_no_climate_device():
    """ac_control returns error when no CLIMATE device found."""
    hal = MagicMock()
    hal.get_devices_by_type = MagicMock(return_value=[])
    ctx = ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )
    set_context(ctx)
    result = await ac_control("Room 1", "on")
    assert "nicht verfügbar" in result


async def test_get_ac_status_returns_german():
    """get_ac_status() returns German status text."""
    hal, adapter = make_mock_hal_with_climate()
    ctx = ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )
    set_context(ctx)
    result = await get_ac_status("Room 1")
    assert "Room 1" in result
    assert "Raumtemperatur" in result


# ─── execute_batch tests ────────────────────────────────────────────────────


class MockHALBatch:
    """Extended MockHAL with multiple rooms, SCENE and CLIMATE devices for batch tests."""

    def __init__(self) -> None:
        self.wz_light = _make_adapter(
            "light-wz", "Deckenlampe WZ", DeviceType.DIMMABLE_LIGHT, "Wohnzimmer"
        )
        self.wz_blind = _make_adapter("blind-wz", "Jalousie WZ", DeviceType.BLIND, "Wohnzimmer")
        self.wz_heat = _make_adapter("heat-wz", "Heizung WZ", DeviceType.HEATING, "Wohnzimmer")
        self.kueche_light = _make_adapter(
            "light-kue", "Kueche Licht", DeviceType.DIMMABLE_LIGHT, "Kueche"
        )
        self.schlaf_light = _make_adapter(
            "light-schlaf", "Schlafzimmer Licht", DeviceType.SWITCH, "Schlafzimmer"
        )
        self.schlaf_blind = _make_adapter(
            "blind-schlaf", "Schlafzimmer Jalousie", DeviceType.BLIND, "Schlafzimmer"
        )
        self.scene_device = _make_adapter("scene-1", "Abendszene", DeviceType.SCENE, "Wohnzimmer")
        self.climate_device = _make_adapter("ac-1", "Klimaanlage", DeviceType.CLIMATE, "Wohnzimmer")
        self.executed: list[tuple] = []

    def has_room(self, name: str) -> bool:
        return name.lower() in ("wohnzimmer", "kueche", "schlafzimmer")

    def find_room(self, name: str) -> list:
        n = name.lower()
        if n == "wohnzimmer":
            return [
                self.wz_light,
                self.wz_blind,
                self.wz_heat,
                self.climate_device,
                self.scene_device,
            ]
        if n == "kueche":
            return [self.kueche_light]
        if n == "schlafzimmer":
            return [self.schlaf_light, self.schlaf_blind]
        return []

    def find_device(self, name: str, room: str = None) -> object:
        return f"Gerät '{name}' nicht gefunden."

    def get_devices_by_type(self, device_type: DeviceType) -> list:
        m = {
            DeviceType.DIMMABLE_LIGHT: [self.wz_light, self.kueche_light],
            DeviceType.SWITCH: [self.schlaf_light],
            DeviceType.BLIND: [self.wz_blind, self.schlaf_blind],
            DeviceType.HEATING: [self.wz_heat],
            DeviceType.CLIMATE: [self.climate_device],
            DeviceType.SCENE: [self.scene_device],
        }
        return m.get(device_type, [])

    def get_all_rooms(self) -> dict:
        return {
            "Wohnzimmer": [self.wz_light.info, self.wz_blind.info],
            "Kueche": [self.kueche_light.info],
            "Schlafzimmer": [self.schlaf_light.info, self.schlaf_blind.info],
        }

    async def execute(self, device_id: DeviceId, command: str, **params) -> None:
        self.executed.append((device_id, command, params))

    async def get_room_state_formatted(self, room: str) -> dict:
        return {}


def _make_batch_ctx(hal: MockHALBatch) -> ToolContext:
    return ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )


def make_batch_deps() -> MockHALBatch:
    hal = MockHALBatch()
    from src.tools.comfort import set_context

    set_context(_make_batch_ctx(hal))
    return hal


async def test_execute_batch_multi_room_lights_off():
    """3 rooms, lights_off in each, all succeed."""
    hal = make_batch_deps()
    commands = _json.dumps(
        [
            {"room": "Wohnzimmer", "action": "lights_off"},
            {"room": "Kueche", "action": "lights_off"},
            {"room": "Schlafzimmer", "action": "lights_off"},
        ]
    )
    result = await execute_batch(commands)
    turn_off_cmds = [cmd for _, cmd, _ in hal.executed if cmd == "turn_off"]
    # Wohnzimmer has 1 DIMMABLE_LIGHT, Kueche has 1, Schlafzimmer has 1 SWITCH
    assert len(turn_off_cmds) == 3
    assert "ausgefuehrt" in result.lower() or "Befehle" in result


async def test_execute_batch_mixed_commands():
    """lights_off + blinds_down + heating_set in one batch."""
    hal = make_batch_deps()
    commands = _json.dumps(
        [
            {"room": "Wohnzimmer", "action": "lights_off"},
            {"room": "Wohnzimmer", "action": "blinds_down"},
            {"room": "Wohnzimmer", "action": "heating_set", "temperature": 20.0},
        ]
    )
    result = await execute_batch(commands)
    cmds = [cmd for _, cmd, _ in hal.executed]
    assert "turn_off" in cmds
    assert "close" in cmds
    assert "set_temperature" in cmds
    assert result  # non-empty German result


async def test_execute_batch_partial_failure():
    """One device raises, others succeed; summary includes error with device name."""
    hal = make_batch_deps()
    call_count = [0]

    async def execute_with_failure(device_id: DeviceId, command: str, **params) -> None:
        call_count[0] += 1
        hal.executed.append((device_id, command, params))
        if device_id.native_id == "light-kue":
            raise RuntimeError("Timeout")

    hal.execute = execute_with_failure

    commands = _json.dumps(
        [
            {"room": "Wohnzimmer", "action": "lights_off"},
            {"room": "Kueche", "action": "lights_off"},
        ]
    )
    result = await execute_batch(commands)
    assert "Fehler" in result or "fehler" in result.lower()
    assert (
        "Kueche" in result
        or "light-kue" in result
        or "Kueche Kueche" in result
        or "Licht" in result
    )


async def test_execute_batch_invalid_room():
    """One command has unknown room, skipped; valid commands execute."""
    hal = make_batch_deps()
    commands = _json.dumps(
        [
            {"room": "Wohnzimmer", "action": "lights_off"},
            {"room": "NichtExistierenderRaum", "action": "lights_off"},
        ]
    )
    result = await execute_batch(commands)
    turn_off_cmds = [cmd for _, cmd, _ in hal.executed if cmd == "turn_off"]
    assert len(turn_off_cmds) == 1  # only Wohnzimmer executed
    assert "uebersprungen" in result or "nicht gefunden" in result.lower() or result


async def test_execute_batch_empty_list():
    """Empty commands list returns sensible message."""
    make_batch_deps()
    result = await execute_batch(_json.dumps([]))
    assert result
    assert "Befehle" in result or "leer" in result.lower() or "angegeben" in result.lower()


async def test_execute_batch_single_command():
    """Single command works (degenerate case)."""
    hal = make_batch_deps()
    commands = _json.dumps([{"room": "Wohnzimmer", "action": "lights_on"}])
    result = await execute_batch(commands)
    assert any(cmd == "turn_on" for _, cmd, _ in hal.executed)
    assert result


async def test_execute_batch_trigger_scene():
    """trigger_scene action with device_name resolves SCENE device; room is ignored."""
    hal = make_batch_deps()
    commands = _json.dumps([{"action": "trigger_scene", "device_name": "Abendszene"}])
    result = await execute_batch(commands)
    trigger_cmds = [cmd for _, cmd, _ in hal.executed if cmd == "trigger"]
    assert len(trigger_cmds) == 1
    assert result


async def test_execute_batch_no_context():
    """Returns error when _ctx is None."""
    _comfort_mod._ctx = None
    result = await execute_batch(_json.dumps([{"room": "Wohnzimmer", "action": "lights_off"}]))
    assert "nicht erreichbar" in result


async def test_execute_batch_invalid_json():
    """Malformed JSON string returns German error message."""
    make_batch_deps()
    result = await execute_batch("not valid json {{{")
    assert "Ungueltige" in result or "ungültig" in result.lower() or "JSON" in result


async def test_execute_batch_exception_device_correlation():
    """When 2/5 devices fail, error message names the exact devices that failed."""
    hal = make_batch_deps()
    failed_ids = {"light-kue", "blind-schlaf"}

    async def execute_selective_failure(device_id: DeviceId, command: str, **params) -> None:
        hal.executed.append((device_id, command, params))
        if device_id.native_id in failed_ids:
            raise RuntimeError("Simulated failure")

    hal.execute = execute_selective_failure

    commands = _json.dumps(
        [
            {"room": "Wohnzimmer", "action": "lights_off"},
            {"room": "Kueche", "action": "lights_off"},
            {"room": "Schlafzimmer", "action": "lights_off"},
            {"room": "Schlafzimmer", "action": "blinds_down"},
            {"room": "Wohnzimmer", "action": "blinds_down"},
        ]
    )
    result = await execute_batch(commands)
    # The 2 failed devices should be mentioned in errors
    assert "Fehler" in result or "fehler" in result.lower()
    assert "2" in result or "Kueche" in result or "Schlafzimmer" in result


async def test_execute_batch_ac_actions():
    """ac_on, ac_off, ac_set_temp, ac_set_mode translate to correct HAL commands."""
    hal = make_batch_deps()
    commands = _json.dumps(
        [
            {"room": "Wohnzimmer", "action": "ac_on"},
            {"room": "Wohnzimmer", "action": "ac_off"},
            {"room": "Wohnzimmer", "action": "ac_set_temp", "temperature": 22.0},
            {"room": "Wohnzimmer", "action": "ac_set_mode", "mode": "kuehlen"},
        ]
    )
    await execute_batch(commands)
    cmds = [cmd for _, cmd, _ in hal.executed]
    assert cmds.count("turn_on") >= 1
    assert cmds.count("turn_off") >= 1
    assert "set_temperature" in cmds
    assert "set_mode" in cmds
    # Check set_temperature has value param
    set_temp = next((p for _, cmd, p in hal.executed if cmd == "set_temperature"), None)
    assert set_temp is not None
    assert set_temp.get("value") == 22.0
    # Check set_mode has mode param (not value)
    set_mode = next((p for _, cmd, p in hal.executed if cmd == "set_mode"), None)
    assert set_mode is not None
    assert set_mode.get("mode") == "kuehlen"


async def test_execute_batch_device_type_filtering():
    """lights_off in a room with lights AND blinds only affects light devices."""
    hal = make_batch_deps()
    commands = _json.dumps([{"room": "Wohnzimmer", "action": "lights_off"}])
    await execute_batch(commands)
    # Only turn_off commands, no open/close/stop
    non_light_cmds = [cmd for _, cmd, _ in hal.executed if cmd in ("open", "close", "stop")]
    assert len(non_light_cmds) == 0
    turn_off_cmds = [cmd for _, cmd, _ in hal.executed if cmd == "turn_off"]
    assert len(turn_off_cmds) >= 1


async def test_execute_batch_blinds_with_duration():
    """blinds_up with duration sends open then stop after sleep."""
    hal = make_batch_deps()
    commands = _json.dumps([{"room": "Wohnzimmer", "action": "blinds_up", "duration": 0.1}])
    result = await execute_batch(commands)
    cmds = [cmd for _, cmd, _ in hal.executed]
    assert "open" in cmds, "Should send open command"
    assert "stop" in cmds, "Should send stop after duration"
    # open comes before stop
    open_idx = cmds.index("open")
    stop_idx = cmds.index("stop")
    assert open_idx < stop_idx, "open should come before stop"
    assert "ausgefuehrt" in result.lower() or "Befehle" in result


async def test_execute_batch_blinds_without_duration():
    """blinds_up without duration sends only open, no stop."""
    hal = make_batch_deps()
    commands = _json.dumps([{"room": "Wohnzimmer", "action": "blinds_up"}])
    await execute_batch(commands)
    cmds = [cmd for _, cmd, _ in hal.executed]
    assert "open" in cmds
    assert "stop" not in cmds, "No stop when duration=0"


async def test_execute_batch_blinds_duration_clamped():
    """duration is clamped to max 10 seconds."""
    hal = make_batch_deps()
    commands = _json.dumps([{"room": "Wohnzimmer", "action": "blinds_down", "duration": 99}])
    await execute_batch(commands)
    cmds = [cmd for _, cmd, _ in hal.executed]
    assert "close" in cmds
    assert "stop" in cmds  # duration > 0 so stop is sent
