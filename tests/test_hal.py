from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.hal.devices import BlindAdapter, LightAdapter
from src.hal.models import SensorClass
from src.hal.hal import HAL
from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType
from src.hal.platform import Platform
from src.hal.state import StateStore


class MockPlatform(Platform):
    """In-memory mock platform for HAL tests.

    Provides 3 devices: a light and blind in Wohnzimmer, a sensor in Schlafzimmer.
    """

    def __init__(self) -> None:
        self._commands: list[tuple] = []
        self._started = False
        self._stopped = False
        self._unsubscribed = False

    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock"

    @property
    def error_message(self) -> str:
        return "⚠ Mock ist offline."

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._stopped = True

    async def health_check(self) -> bool:
        return True

    async def discover_devices(self) -> list[DeviceInfo]:
        return [
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="light-1"),
                name="Deckenlampe",
                device_type=DeviceType.LIGHT,
                room="Wohnzimmer",
            ),
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="blind-1"),
                name="Rollo",
                device_type=DeviceType.BLIND,
                room="Wohnzimmer",
            ),
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="sensor-1"),
                name="Fensterkontakt",
                device_type=DeviceType.BINARY_SENSOR,
                room="Schlafzimmer",
            ),
        ]

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        return DeviceState(
            device_id=device_id,
            state={},
            last_updated=datetime.now(timezone.utc),
        )

    async def send_command(self, device_id: DeviceId, command: str, **params) -> None:
        self._commands.append((device_id, command, params))

    async def unsubscribe_events(self) -> None:
        self._unsubscribed = True


async def _make_hal(tmp_path: Path) -> tuple[HAL, MockPlatform]:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    hal = HAL(store)
    platform = MockPlatform()
    await hal.add_platform(platform)
    return hal, platform


async def test_add_platform_discovers_devices(tmp_path: Path) -> None:
    hal, _ = await _make_hal(tmp_path)
    assert len(hal._devices) == 3


async def test_find_device_by_name(tmp_path: Path) -> None:
    hal, _ = await _make_hal(tmp_path)
    result = hal.find_device("Deckenlampe")
    assert isinstance(result, LightAdapter)
    assert result.info.name == "Deckenlampe"


async def test_find_device_by_name_and_room(tmp_path: Path) -> None:
    hal, _ = await _make_hal(tmp_path)
    result = hal.find_device("Rollo", room="Wohnzimmer")
    assert isinstance(result, BlindAdapter)


async def test_find_device_not_found(tmp_path: Path) -> None:
    hal, _ = await _make_hal(tmp_path)
    result = hal.find_device("NichtVorhanden")
    assert isinstance(result, str)
    assert "NichtVorhanden" in result


async def test_find_device_ambiguous(tmp_path: Path) -> None:
    """When two devices have the same name in different rooms, return a German hint."""
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    hal = HAL(store)

    class DupePlatform(MockPlatform):
        async def discover_devices(self) -> list[DeviceInfo]:
            return [
                DeviceInfo(
                    id=DeviceId(platform="mock", native_id="a"),
                    name="Lampe",
                    device_type=DeviceType.LIGHT,
                    room="Wohnzimmer",
                ),
                DeviceInfo(
                    id=DeviceId(platform="mock", native_id="b"),
                    name="Lampe",
                    device_type=DeviceType.LIGHT,
                    room="Schlafzimmer",
                ),
            ]

    await hal.add_platform(DupePlatform())
    result = hal.find_device("Lampe")
    assert isinstance(result, str)
    assert "Raum" in result or "Wohnzimmer" in result or "Schlafzimmer" in result


async def test_find_room(tmp_path: Path) -> None:
    hal, _ = await _make_hal(tmp_path)
    adapters = hal.find_room("Wohnzimmer")
    assert len(adapters) == 2


async def test_find_room_not_found(tmp_path: Path) -> None:
    hal, _ = await _make_hal(tmp_path)
    adapters = hal.find_room("NichtExistierenderRaum")
    assert adapters == []


async def test_get_devices_by_type(tmp_path: Path) -> None:
    hal, _ = await _make_hal(tmp_path)
    lights = hal.get_devices_by_type(DeviceType.LIGHT)
    assert len(lights) == 1
    assert isinstance(lights[0], LightAdapter)


async def test_execute_dispatches_command(tmp_path: Path) -> None:
    hal, platform = await _make_hal(tmp_path)
    device_id = DeviceId(platform="mock", native_id="light-1")
    await hal.execute(device_id, "turn_on", brightness=80)
    assert len(platform._commands) == 1
    assert platform._commands[0][1] == "turn_on"
    assert platform._commands[0][2]["brightness"] == 80


async def test_stop_cleans_up(tmp_path: Path) -> None:
    hal, platform = await _make_hal(tmp_path)
    await hal.stop()
    assert platform._stopped
    assert platform._unsubscribed


# ── Task 1.9: Tests for new HAL facade methods ─────────────────────────────


class MockPlatformWithSensors(Platform):
    """Mock platform with window sensors and room_key in platform_data."""

    def __init__(self) -> None:
        self._commands: list[tuple] = []
        self._started = False
        self._stopped = False
        self._unsubscribed = False

    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock"

    @property
    def error_message(self) -> str:
        return "⚠ Mock ist offline."

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._stopped = True

    async def health_check(self) -> bool:
        return True

    async def discover_devices(self) -> list[DeviceInfo]:
        return [
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="light-1"),
                name="Deckenlampe",
                device_type=DeviceType.LIGHT,
                room="Wohnzimmer",
                platform_data={"room_key": "wohnzimmer"},
            ),
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="window-1"),
                name="Fensterkontakt",
                device_type=DeviceType.BINARY_SENSOR,
                room="Schlafzimmer",
                sensor_class=SensorClass.WINDOW,
                platform_data={"room_key": "schlafzimmer"},
            ),
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="alarm-1"),
                name="Alarmbereitschaft",
                device_type=DeviceType.BINARY_SENSOR,
                room="Keller",
                sensor_class=SensorClass.ALARM_READINESS,
                platform_data={"room_key": "keller"},
            ),
        ]

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        return DeviceState(
            device_id=device_id,
            state={},
            last_updated=datetime.now(timezone.utc),
        )

    async def send_command(self, device_id: DeviceId, command: str, **params) -> None:
        self._commands.append((device_id, command, params))

    async def unsubscribe_events(self) -> None:
        self._unsubscribed = True


async def _make_hal_with_sensors(tmp_path: Path) -> tuple[HAL, MockPlatformWithSensors]:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    hal = HAL(store)
    platform = MockPlatformWithSensors()
    await hal.add_platform(platform)
    return hal, platform


async def test_get_sensors_by_class_window(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    sensors = hal.get_sensors_by_class(SensorClass.WINDOW)
    assert len(sensors) == 1
    assert sensors[0].info.name == "Fensterkontakt"


async def test_get_sensors_by_class_alarm(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    sensors = hal.get_sensors_by_class(SensorClass.ALARM_READINESS)
    assert len(sensors) == 1
    assert sensors[0].info.name == "Alarmbereitschaft"


async def test_get_sensors_by_class_empty(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    sensors = hal.get_sensors_by_class(SensorClass.MOTION)
    assert sensors == []


async def test_has_room_by_display_name(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    assert hal.has_room("Wohnzimmer")
    assert hal.has_room("wohnzimmer")  # case-insensitive
    assert hal.has_room("WOHNZIMMER")


async def test_has_room_by_slug(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    assert hal.has_room("schlafzimmer")  # room_key slug
    assert hal.has_room("keller")


async def test_has_room_not_found(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    assert not hal.has_room("Küche")
    assert not hal.has_room("kueche")


async def test_find_room_with_slug(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    # room_key slug fallback
    adapters = hal.find_room("wohnzimmer")
    assert len(adapters) == 1
    assert adapters[0].info.name == "Deckenlampe"


async def test_find_room_with_display_name(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    adapters = hal.find_room("Wohnzimmer")
    assert len(adapters) == 1
    assert adapters[0].info.name == "Deckenlampe"


async def test_get_all_rooms(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    rooms = hal.get_all_rooms()
    assert "Wohnzimmer" in rooms
    assert "Schlafzimmer" in rooms
    assert "Keller" in rooms
    assert rooms["Wohnzimmer"][0].name == "Deckenlampe"


async def test_state_store_property(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    from src.hal.state import StateStore as SS

    assert isinstance(hal.state_store, SS)


async def test_device_count(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    assert hal.device_count == 3


async def test_get_room_state_formatted_by_display_name(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    # Insert some state data
    from src.hal.models import DeviceState

    device_id = DeviceId(platform="mock", native_id="light-1")
    await hal.state_store.update(
        DeviceState(
            device_id=device_id,
            state={"OnOff": "1", "Brightness": "80"},
            last_updated=datetime.now(timezone.utc),
        )
    )
    result = await hal.get_room_state_formatted("Wohnzimmer")
    assert "Deckenlampe" in result
    assert result["Deckenlampe"]["OnOff"] == "1"
    assert result["Deckenlampe"]["Brightness"] == "80"


async def test_get_room_state_formatted_by_slug(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    from src.hal.models import DeviceState

    device_id = DeviceId(platform="mock", native_id="light-1")
    await hal.state_store.update(
        DeviceState(
            device_id=device_id,
            state={"OnOff": "1"},
            last_updated=datetime.now(timezone.utc),
        )
    )
    result = await hal.get_room_state_formatted("wohnzimmer")
    assert "Deckenlampe" in result


async def test_get_room_state_formatted_not_found(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    result = await hal.get_room_state_formatted("NichtExistierend")
    assert result == {}


async def test_get_full_state_summary_all_off(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    result = await hal.get_full_state_summary()
    assert result == "Alle Lichter aus."


async def test_get_full_state_summary_with_active(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    from src.hal.models import DeviceState

    device_id = DeviceId(platform="mock", native_id="light-1")
    await hal.state_store.update(
        DeviceState(
            device_id=device_id,
            state={"OnOff": "1", "Brightness": "80"},
            last_updated=datetime.now(timezone.utc),
        )
    )
    result = await hal.get_full_state_summary()
    assert isinstance(result, str)
    assert "Wohnzimmer" in result
    assert "Deckenlampe" in result
    assert "an" in result


async def test_populate_initial_state(tmp_path: Path) -> None:
    hal, _ = await _make_hal_with_sensors(tmp_path)
    await hal.populate_initial_state()
    # After populate, state_store should have entries (mock read_state returns empty state)
    # Just verify it completes without error and store is accessible
    summary = await hal.state_store.get_full_state_summary()
    # 3 devices, each with empty state {}
    assert len(summary) == 3


# ─── RuleEngine integration tests ─────────────────────────────────────────


async def test_hal_has_rules_property(tmp_path: Path) -> None:
    """hal.rules returns a RuleEngine instance."""
    from src.hal.rules import RuleEngine

    state_store = StateStore(tmp_path / "test.db")
    await state_store.start()
    hal = HAL(state_store=state_store)
    assert isinstance(hal.rules, RuleEngine)
    await state_store.stop()


async def test_hal_add_platform_registers_devices_with_rules(tmp_path: Path) -> None:
    """After add_platform, rule_engine has devices registered."""
    state_store = StateStore(tmp_path / "test.db")
    await state_store.start()
    hal = HAL(state_store=state_store)
    platform = MockPlatform()
    await hal.add_platform(platform)

    # Rule engine should have devices from the platform
    devices = hal.rules._devices
    assert len(devices) == 3  # MockPlatform has 3 devices

    await state_store.stop()


# ─── Substring fallback tests ────────────────────────────────────────────


class SubstringPlatform(Platform):
    """Mock platform with devices that test substring matching."""

    @property
    def name(self) -> str:
        return "mock"

    @property
    def display_name(self) -> str:
        return "Mock"

    @property
    def error_message(self) -> str:
        return "⚠ Mock ist offline."

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True

    async def discover_devices(self) -> list[DeviceInfo]:
        return [
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="garden-light-1"),
                name="Gartenstrahler",
                device_type=DeviceType.LIGHT,
                room="Gartenbereich",
            ),
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="garden-scene-1"),
                name="Garten-Szenentaster",
                device_type=DeviceType.COLORED_LIGHT,
                room="Gartenbereich",
            ),
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="terrace-wall"),
                name="Boden u. Wand",
                device_type=DeviceType.LIGHT,
                room="Gartenbereich",
            ),
            DeviceInfo(
                id=DeviceId(platform="mock", native_id="garden-light"),
                name="Pollerleuchte",
                device_type=DeviceType.LIGHT,
                room="Außenbereich",
            ),
        ]

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        return DeviceState(device_id=device_id, state={}, last_updated=datetime.now(timezone.utc))

    async def send_command(self, device_id: DeviceId, command: str, **params) -> None:
        pass

    async def unsubscribe_events(self) -> None:
        pass


async def _make_hal_substring(tmp_path: Path) -> HAL:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    hal = HAL(store)
    await hal.add_platform(SubstringPlatform())
    return hal


async def test_find_device_exact_match_preferred(tmp_path: Path) -> None:
    """Exact match still works and is preferred over substring."""
    hal = await _make_hal_substring(tmp_path)
    result = hal.find_device("Gartenstrahler")
    assert not isinstance(result, str)
    assert result.info.name == "Gartenstrahler"


async def test_find_device_substring_single_match(tmp_path: Path) -> None:
    """Substring 'Wand' matches only 'Boden u. Wand'."""
    hal = await _make_hal_substring(tmp_path)
    result = hal.find_device("Wand")
    assert not isinstance(result, str)
    assert result.info.name == "Boden u. Wand"


async def test_find_device_substring_multiple_matches_returns_suggestions(tmp_path: Path) -> None:
    """Substring 'Garten' matches two devices → returns suggestion string."""
    hal = await _make_hal_substring(tmp_path)
    result = hal.find_device("Garten")
    assert isinstance(result, str)
    assert "Gartenstrahler" in result
    assert "Garten-Szenentaster" in result


async def test_find_device_substring_with_room_filter(tmp_path: Path) -> None:
    """Substring match respects room filter."""
    hal = await _make_hal_substring(tmp_path)
    result = hal.find_device("Strahler", room="Gartenbereich")
    assert not isinstance(result, str)
    assert result.info.name == "Gartenstrahler"


async def test_find_device_substring_room_filter_also_fuzzy(tmp_path: Path) -> None:
    """Room filter uses substring fallback: 'Garten' matches 'Gartenbereich'."""
    hal = await _make_hal_substring(tmp_path)
    result = hal.find_device("Wand", room="Garten")
    assert not isinstance(result, str)
    assert result.info.name == "Boden u. Wand"


async def test_find_device_no_match_at_all(tmp_path: Path) -> None:
    """Completely unknown name still returns error."""
    hal = await _make_hal_substring(tmp_path)
    result = hal.find_device("Küchenlicht")
    assert isinstance(result, str)
    assert "nicht gefunden" in result


async def test_find_room_substring_fallback(tmp_path: Path) -> None:
    """find_room('Garten') matches 'Gartenbereich' via substring."""
    hal = await _make_hal_substring(tmp_path)
    adapters = hal.find_room("Garten")
    assert len(adapters) == 3  # all 3 devices in "Gartenbereich"


async def test_find_room_exact_preferred_over_substring(tmp_path: Path) -> None:
    """Exact room match is preferred over substring."""
    hal = await _make_hal_substring(tmp_path)
    adapters = hal.find_room("Außenbereich")
    assert len(adapters) == 1
    assert adapters[0].info.name == "Pollerleuchte"


# ---------- Topology room filtering ----------


class _MockTopologyGraph:
    """Minimal mock of networkx graph with node attributes."""

    def __init__(self, rooms: dict[str, str]) -> None:
        # rooms: {node_key: display_name}
        self._nodes = {k: {"display_name": v} for k, v in rooms.items()}

    @property
    def nodes(self):
        return self._nodes

    def __iter__(self):
        return iter(self._nodes)


class _MockTopology:
    def __init__(self, rooms: dict[str, str]) -> None:
        self.graph = _MockTopologyGraph(rooms)


async def test_topology_filters_get_all_rooms(tmp_path: Path) -> None:
    """get_all_rooms() excludes rooms not in topology."""
    hal, _ = await _make_hal_with_sensors(tmp_path)
    # Without topology: all 3 rooms visible
    assert len(hal.get_all_rooms()) == 3

    # Set topology with only Wohnzimmer and Schlafzimmer
    topo = _MockTopology({"wohnzimmer": "Wohnzimmer", "schlafzimmer": "Schlafzimmer"})
    hal.set_topology(topo)
    rooms = hal.get_all_rooms()
    assert "Wohnzimmer" in rooms
    assert "Schlafzimmer" in rooms
    assert "Keller" not in rooms


async def test_topology_filters_has_room(tmp_path: Path) -> None:
    """has_room() returns False for rooms not in topology."""
    hal, _ = await _make_hal_with_sensors(tmp_path)
    topo = _MockTopology({"wohnzimmer": "Wohnzimmer"})
    hal.set_topology(topo)

    assert hal.has_room("Wohnzimmer")
    assert hal.has_room("wohnzimmer")
    assert not hal.has_room("Keller")
    assert not hal.has_room("keller")


async def test_topology_filters_find_room(tmp_path: Path) -> None:
    """find_room() returns empty for rooms not in topology."""
    hal, _ = await _make_hal_with_sensors(tmp_path)
    topo = _MockTopology({"wohnzimmer": "Wohnzimmer"})
    hal.set_topology(topo)

    assert len(hal.find_room("Wohnzimmer")) == 1
    assert hal.find_room("Keller") == []


async def test_no_topology_returns_all_rooms(tmp_path: Path) -> None:
    """Without topology set, all rooms are returned (graceful fallback)."""
    hal, _ = await _make_hal_with_sensors(tmp_path)
    assert hal._topology_rooms is None
    assert len(hal.get_all_rooms()) == 3
    assert hal.has_room("Keller")
    assert len(hal.find_room("Keller")) == 1
