"""Integration tests for platform health propagation pipeline.

Tests the complete end-to-end health flow:
    PlatformHealthMonitor → build_embodied_context → ToolContext → Tools
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.platform_health import PlatformHealthMonitor
from src.core.types import HealthState
from src.hal.hal import HAL
from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType
from src.hal.platform import Platform
from src.hal.state import StateStore
from src.tools.context import ToolContext


# ─── Mock Platform Fixtures ─────────────────────────────────────────────────


class MockPlatform(Platform):
    """Mock platform with controllable health check for testing."""

    def __init__(self, name: str, devices: list[DeviceInfo], healthy: bool = True) -> None:
        self._name = name
        self._devices = devices
        self._event_callback: Callable[[DeviceState], Awaitable[None]] | None = None
        self._healthy = healthy
        self.started = False
        self.stopped = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        # Map platform names to German display names for tests
        display_names = {
            "frigate": "Frigate",
            "gira_x1": "Gira X1",
            "weather": "Wetterdienst",
            "calendar": "Kalender",
            "presence": "Anwesenheitserkennung",
            "mitsubishi": "Mitsubishi AC",
            "door_station": "Türstation",
        }
        return display_names.get(self._name, self._name)

    @property
    def error_message(self) -> str:
        # Map platform names to German error messages for tests
        error_messages = {
            "frigate": "⚠ Frigate ist offline — keine Kameradaten verfügbar.",
            "gira_x1": "⚠ Gira X1 ist offline — keine Gerätesteuerung möglich.",
            "weather": "⚠ Wetterdienst ist offline — keine aktuellen Wetterdaten.",
            "calendar": "⚠ Kalender ist offline — keine Kalenderdaten verfügbar.",
            "presence": "⚠ Anwesenheitserkennung ist offline — Anwesenheit nicht erkannt.",
            "mitsubishi": "⚠ Mitsubishi AC ist offline.",
            "door_station": "⚠ Türstation ist offline.",
        }
        return error_messages.get(self._name, f"⚠ {self._name} ist offline.")

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def health_check(self) -> bool:
        return self._healthy

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy

    async def discover_devices(self) -> list[DeviceInfo]:
        return list(self._devices)

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        return DeviceState(
            device_id=device_id,
            state={"value": "test"},
            last_updated=datetime.now(),
            source=self._name,
        )

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        self._event_callback = callback

    async def emit_event(self, device_id: DeviceId, state_dict: dict) -> None:
        """Helper: emit a state change from this platform."""
        if self._event_callback:
            state = DeviceState(
                device_id=device_id,
                state=state_dict,
                last_updated=datetime.now(),
                source=self._name,
            )
            await self._event_callback(state)

    async def get_events(self, hours: int = 24) -> list[dict]:
        """Mock camera events retrieval."""
        return []


def _make_device(platform_name: str, native_id: str, name: str, room: str) -> DeviceInfo:
    device_id = DeviceId(platform=platform_name, native_id=native_id)
    return DeviceInfo(
        id=device_id,
        name=name,
        device_type=DeviceType.SWITCH,
        room=room,
        platform_data={},
    )


@pytest.fixture
async def state_store(tmp_path: Path):
    """Create a temporary StateStore for testing."""
    store = StateStore(db_path=tmp_path / "test_health.db")
    await store.start()
    yield store
    await store.stop()


@pytest.fixture
async def hal(state_store: StateStore):
    """Create a HAL instance with the state store."""
    return HAL(state_store)


# ─── Bootstrapper Integration Tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_monitor_created_with_hal(hal: HAL):
    """PlatformHealthMonitor is created with HAL reference."""
    monitor = PlatformHealthMonitor(hal)

    assert monitor._hal is hal
    assert monitor._task is None
    assert monitor._statuses == {}


@pytest.mark.asyncio
async def test_health_monitor_starts_and_stops(hal: HAL):
    """Monitor starts when start() is called and stops when stop() is called."""
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)

    # Start the monitor
    monitor.start()
    assert monitor._task is not None
    assert not monitor._task.done()

    # Let it run one iteration
    await asyncio.sleep(0.1)

    # Stop the monitor
    monitor.stop()
    await asyncio.sleep(0.1)

    assert monitor._task.done() or monitor._task.cancelled()


@pytest.mark.asyncio
async def test_health_monitor_checks_platforms_on_start(hal: HAL):
    """Monitor performs initial health check when started."""
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    monitor.start()

    # Wait for initial check
    await asyncio.sleep(0.1)

    # Should have status for the platform
    status = monitor.get_status("frigate")
    assert status is not None
    assert status.state == HealthState.HEALTHY
    assert status.platform_name == "frigate"

    monitor.stop()


@pytest.mark.asyncio
async def test_health_monitor_detects_unhealthy_platform(hal: HAL):
    """Monitor correctly identifies unhealthy platforms."""
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=False)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    monitor.start()

    # Wait for initial check
    await asyncio.sleep(0.1)

    # Should detect unhealthy status
    status = monitor.get_status("frigate")
    assert status is not None
    assert status.state == HealthState.UNHEALTHY
    assert status.last_error == "Health check returned False"

    monitor.stop()


@pytest.mark.asyncio
async def test_health_monitor_detects_state_change(hal: HAL):
    """Monitor detects when platform health changes over time."""
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    monitor.start()

    # Wait for initial check
    await asyncio.sleep(0.1)

    # Initially healthy
    status = monitor.get_status("frigate")
    assert status.state == HealthState.HEALTHY

    # Change to unhealthy
    platform.set_healthy(False)

    # Wait for next check cycle (simulate poll interval with manual check)
    await monitor._check_all_platforms()

    # Should now be unhealthy
    status = monitor.get_status("frigate")
    assert status.state == HealthState.UNHEALTHY

    monitor.stop()


@pytest.mark.asyncio
async def test_health_monitor_handles_platform_exception(hal: HAL):
    """Monitor handles exceptions during health_check gracefully."""
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    # Override health_check to raise
    platform.health_check = AsyncMock(side_effect=RuntimeError("Connection failed"))
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    monitor.start()

    # Wait for initial check
    await asyncio.sleep(0.1)

    # Should have unhealthy status with error message
    status = monitor.get_status("frigate")
    assert status is not None
    assert status.state == HealthState.UNHEALTHY
    assert "Connection failed" in status.last_error

    monitor.stop()


@pytest.mark.asyncio
async def test_health_monitor_get_all_statuses(hal: HAL):
    """Monitor can return status for all platforms."""
    devices_a = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    devices_b = [_make_device("gira_x1", "light1", "Licht", "Wohnzimmer")]

    platform_a = MockPlatform("frigate", devices_a, healthy=True)
    platform_b = MockPlatform("gira_x1", devices_b, healthy=False)

    await hal.add_platform(platform_a)
    await hal.add_platform(platform_b)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    all_statuses = monitor.get_all_statuses()
    assert len(all_statuses) == 2
    assert all_statuses["frigate"].state == HealthState.HEALTHY
    assert all_statuses["gira_x1"].state == HealthState.UNHEALTHY


@pytest.mark.asyncio
async def test_health_monitor_get_unhealthy_platforms(hal: HAL):
    """Monitor can return only unhealthy platforms."""
    devices_a = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    devices_b = [_make_device("gira_x1", "light1", "Licht", "Wohnzimmer")]
    devices_c = [_make_device("weather", "sensor1", "Wetter", "Dach")]

    platform_a = MockPlatform("frigate", devices_a, healthy=True)
    platform_b = MockPlatform("gira_x1", devices_b, healthy=False)
    platform_c = MockPlatform("weather", devices_c, healthy=False)

    await hal.add_platform(platform_a)
    await hal.add_platform(platform_b)
    await hal.add_platform(platform_c)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    unhealthy = monitor.get_unhealthy_platforms()
    assert len(unhealthy) == 2
    platform_names = {s.platform_name for s in unhealthy}
    assert platform_names == {"gira_x1", "weather"}


# ─── Agent Context Integration Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_embodied_context_receives_platform_health(hal: HAL):
    """build_embodied_context receives platform_health parameter and includes status."""
    from src.agents.prompts import build_embodied_context

    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=False)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    # Build context with platform_health
    context = await build_embodied_context(
        hal=hal,
        platform_health=monitor,
        user="@test:server",
        intent="Testanfrage",
    )

    # Should contain German error message for unhealthy platform
    assert "Frigate ist offline" in context
    assert "keine Kameradaten verfügbar" in context


@pytest.mark.asyncio
async def test_build_embodied_context_healthy_platforms_no_status_section(hal: HAL):
    """When all platforms healthy, platform_status section is minimal."""
    from src.agents.prompts import build_embodied_context

    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    # Build context with platform_health
    context = await build_embodied_context(
        hal=hal,
        platform_health=monitor,
        user="@test:server",
        intent="Testanfrage",
    )

    # Should NOT contain error messages for healthy platforms
    assert "ist offline" not in context


@pytest.mark.asyncio
async def test_build_embodied_context_multiple_unhealthy_platforms(hal: HAL):
    """Context includes status for all unhealthy platforms in German."""
    from src.agents.prompts import build_embodied_context

    devices_a = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    devices_b = [_make_device("gira_x1", "light1", "Licht", "Wohnzimmer")]
    devices_c = [_make_device("weather", "sensor1", "Wetter", "Dach")]

    platform_a = MockPlatform("frigate", devices_a, healthy=False)
    platform_b = MockPlatform("gira_x1", devices_b, healthy=False)
    platform_c = MockPlatform("weather", devices_c, healthy=True)

    await hal.add_platform(platform_a)
    await hal.add_platform(platform_b)
    await hal.add_platform(platform_c)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    context = await build_embodied_context(
        hal=hal,
        platform_health=monitor,
        user="@test:server",
        intent="Testanfrage",
    )

    # Should contain German error messages for both unhealthy platforms
    assert "Frigate ist offline" in context
    assert "keine Kameradaten verfügbar" in context
    assert "Gira X1 ist offline" in context
    assert "keine Gerätesteuerung möglich" in context

    # Should NOT contain weather error (it's healthy)
    assert "Wetterdienst ist offline" not in context


@pytest.mark.asyncio
async def test_build_embodied_context_no_platform_health(hal: HAL):
    """When platform_health is None, no status section is included."""
    from src.agents.prompts import build_embodied_context

    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=False)
    await hal.add_platform(platform)

    # Build context WITHOUT platform_health
    context = await build_embodied_context(
        hal=hal,
        platform_health=None,
        user="@test:server",
        intent="Testanfrage",
    )

    # Should NOT contain any platform status
    assert "System-Status" not in context
    assert "ist offline" not in context


@pytest.mark.asyncio
async def test_format_unhealthy_for_prompt_returns_german_messages(hal: HAL):
    """format_unhealthy_for_prompt returns properly formatted German messages."""
    devices_a = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    devices_b = [_make_device("gira_x1", "light1", "Licht", "Wohnzimmer")]
    devices_c = [_make_device("weather", "sensor1", "Wetter", "Dach")]
    devices_d = [_make_device("calendar", "cal1", "Kalender", "Büro")]
    devices_e = [_make_device("presence", "pres1", "Anwesenheit", "Flur")]

    platform_a = MockPlatform("frigate", devices_a, healthy=False)
    platform_b = MockPlatform("gira_x1", devices_b, healthy=False)
    platform_c = MockPlatform("weather", devices_c, healthy=False)
    platform_d = MockPlatform("calendar", devices_d, healthy=False)
    platform_e = MockPlatform("presence", devices_e, healthy=False)

    await hal.add_platform(platform_a)
    await hal.add_platform(platform_b)
    await hal.add_platform(platform_c)
    await hal.add_platform(platform_d)
    await hal.add_platform(platform_e)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    formatted = monitor.format_unhealthy_for_prompt()

    # Should contain specific German messages for each platform type
    assert "⚠ Frigate ist offline — keine Kameradaten verfügbar." in formatted
    assert "⚠ Gira X1 ist offline — keine Gerätesteuerung möglich." in formatted
    assert "⚠ Wetterdienst ist offline — keine aktuellen Wetterdaten." in formatted
    assert "⚠ Kalender ist offline — keine Kalenderdaten verfügbar." in formatted
    assert "⚠ Anwesenheitserkennung ist offline — Anwesenheit nicht erkannt." in formatted


@pytest.mark.asyncio
async def test_format_unhealthy_for_prompt_returns_none_when_healthy(hal: HAL):
    """format_unhealthy_for_prompt returns None when all platforms healthy."""
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    formatted = monitor.format_unhealthy_for_prompt()
    assert formatted is None


@pytest.mark.asyncio
async def test_format_unhealthy_for_prompt_handles_unknown_platform(hal: HAL):
    """format_unhealthy_for_prompt handles unknown platform names gracefully."""
    devices = [_make_device("unknown_platform", "dev1", "Gerät", "Raum")]
    platform = MockPlatform("unknown_platform", devices, healthy=False)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    formatted = monitor.format_unhealthy_for_prompt()
    assert "⚠ unknown_platform ist offline." in formatted


# ─── Tool Integration Tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_context_receives_platform_health(hal: HAL, tmp_path: Path):
    """ToolContext is created with platform_health reference."""
    from src.core.presence import PresenceManager
    from src.core.awareness import EnergyAwareness

    presence_manager = PresenceManager(hal=hal)
    energy_awareness = EnergyAwareness(state_store=hal.state_store, hal=hal)

    # Create a mock platform health monitor
    mock_monitor = MagicMock(spec=PlatformHealthMonitor)

    tool_context = ToolContext(
        hal=hal,
        presence_manager=presence_manager,
        energy_awareness=energy_awareness,
        rule_engine=hal.rules,
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
        topology=None,
        awareness=None,
        platform_health=mock_monitor,
        latitude=52.52,
        longitude=11.0,
        timezone="Europe/Berlin",
    )

    assert tool_context.platform_health is mock_monitor


@pytest.mark.asyncio
async def test_security_tool_returns_german_error_when_frigate_unhealthy(hal: HAL, tmp_path: Path):
    """get_camera_events returns German error when Frigate is unhealthy."""
    from src.core.presence import PresenceManager
    from src.core.awareness import EnergyAwareness
    from src.tools.security import set_context, get_camera_events

    presence_manager = PresenceManager(hal=hal)
    energy_awareness = EnergyAwareness(state_store=hal.state_store, hal=hal)

    # Create platform health monitor with unhealthy frigate
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=False)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    # Create ToolContext with the monitor
    tool_context = ToolContext(
        hal=hal,
        presence_manager=presence_manager,
        energy_awareness=energy_awareness,
        rule_engine=hal.rules,
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
        topology=None,
        awareness=None,
        platform_health=monitor,
        latitude=52.52,
        longitude=11.0,
        timezone="Europe/Berlin",
    )

    # Set context for security tools
    set_context(tool_context)

    # Call get_camera_events
    result = await get_camera_events(hours=24)

    # Should return German error message immediately
    assert "Frigate ist offline" in result
    assert "keine Kameradaten verfügbar" in result


@pytest.mark.asyncio
async def test_security_tool_proceeds_when_frigate_healthy(hal: HAL, tmp_path: Path):
    """get_camera_events proceeds normally when Frigate is healthy."""
    from src.core.presence import PresenceManager
    from src.core.awareness import EnergyAwareness
    from src.tools.security import set_context, get_camera_events

    presence_manager = PresenceManager(hal=hal)
    energy_awareness = EnergyAwareness(state_store=hal.state_store, hal=hal)

    # Create platform health monitor with healthy frigate
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    await hal.add_platform(platform)

    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    # Create ToolContext with the monitor
    tool_context = ToolContext(
        hal=hal,
        presence_manager=presence_manager,
        energy_awareness=energy_awareness,
        rule_engine=hal.rules,
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
        topology=None,
        awareness=None,
        platform_health=monitor,
        latitude=52.52,
        longitude=11.0,
        timezone="Europe/Berlin",
    )

    # Set context for security tools
    set_context(tool_context)

    # Call get_camera_events
    result = await get_camera_events(hours=24)

    # Should NOT return offline error
    assert "Frigate ist offline" not in result
    # Should return normal response (no events found or configured)
    assert "nicht konfiguriert" in result or "Keine Kamera-Ereignisse" in result


@pytest.mark.asyncio
async def test_security_tool_no_platform_health_configured(hal: HAL, tmp_path: Path):
    """get_camera_events proceeds when no platform_health is configured."""
    from src.core.presence import PresenceManager
    from src.core.awareness import EnergyAwareness
    from src.tools.security import set_context, get_camera_events

    presence_manager = PresenceManager(hal=hal)
    energy_awareness = EnergyAwareness(state_store=hal.state_store, hal=hal)

    # Create ToolContext WITHOUT platform_health
    tool_context = ToolContext(
        hal=hal,
        presence_manager=presence_manager,
        energy_awareness=energy_awareness,
        rule_engine=hal.rules,
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
        topology=None,
        awareness=None,
        platform_health=None,  # No health monitor
        latitude=52.52,
        longitude=11.0,
        timezone="Europe/Berlin",
    )

    # Set context for security tools
    set_context(tool_context)

    # Call get_camera_events - should not raise
    result = await get_camera_events(hours=24)

    # Should return normal response (not configured since no platform)
    assert "nicht konfiguriert" in result


@pytest.mark.asyncio
async def test_security_tool_platform_not_in_health_monitor(hal: HAL, tmp_path: Path):
    """get_camera_events proceeds when platform not in health monitor yet."""
    from src.core.presence import PresenceManager
    from src.core.awareness import EnergyAwareness
    from src.tools.security import set_context, get_camera_events

    presence_manager = PresenceManager(hal=hal)
    energy_awareness = EnergyAwareness(state_store=hal.state_store, hal=hal)

    # Add frigate platform to HAL
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    await hal.add_platform(platform)

    # Create monitor but DON'T run check (so frigate not in statuses)
    monitor = PlatformHealthMonitor(hal)

    # Create ToolContext with the monitor
    tool_context = ToolContext(
        hal=hal,
        presence_manager=presence_manager,
        energy_awareness=energy_awareness,
        rule_engine=hal.rules,
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
        topology=None,
        awareness=None,
        platform_health=monitor,
        latitude=52.52,
        longitude=11.0,
        timezone="Europe/Berlin",
    )

    # Set context for security tools
    set_context(tool_context)

    # Call get_camera_events - should proceed since no status means unknown
    result = await get_camera_events(hours=24)

    # Should NOT return offline error (unknown status allows attempt)
    assert "Frigate ist offline" not in result


# ─── End-to-End Pipeline Tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_pipeline_monitor_to_context_to_tool(hal: HAL, tmp_path: Path):
    """Complete pipeline: monitor detects unhealthy → context shows error → tool returns error."""
    from src.core.presence import PresenceManager
    from src.core.awareness import EnergyAwareness
    from src.agents.prompts import build_embodied_context
    from src.tools.security import set_context, get_camera_events

    # Setup
    presence_manager = PresenceManager(hal=hal)
    energy_awareness = EnergyAwareness(state_store=hal.state_store, hal=hal)

    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=False)
    await hal.add_platform(platform)

    # Step 1: Monitor detects unhealthy platform
    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    status = monitor.get_status("frigate")
    assert status.state == HealthState.UNHEALTHY

    # Step 2: Context includes German error message
    context = await build_embodied_context(
        hal=hal,
        platform_health=monitor,
        user="@test:server",
        intent="Zeige Kamera-Ereignisse",
        agent="security",
    )
    assert "Frigate ist offline" in context
    assert "keine Kameradaten verfügbar" in context

    # Step 3: Tool returns German error immediately
    tool_context = ToolContext(
        hal=hal,
        presence_manager=presence_manager,
        energy_awareness=energy_awareness,
        rule_engine=hal.rules,
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
        topology=None,
        awareness=None,
        platform_health=monitor,
        latitude=52.52,
        longitude=11.0,
        timezone="Europe/Berlin",
    )
    set_context(tool_context)

    result = await get_camera_events(hours=24)
    assert "Frigate ist offline" in result
    assert "keine Kameradaten verfügbar" in result


@pytest.mark.asyncio
async def test_full_pipeline_all_healthy(hal: HAL, tmp_path: Path):
    """Complete pipeline when all platforms healthy: no errors in context or tools."""
    from src.core.presence import PresenceManager
    from src.core.awareness import EnergyAwareness
    from src.agents.prompts import build_embodied_context
    from src.tools.security import set_context, get_camera_events

    # Setup
    presence_manager = PresenceManager(hal=hal)
    energy_awareness = EnergyAwareness(state_store=hal.state_store, hal=hal)

    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    await hal.add_platform(platform)

    # Step 1: Monitor detects healthy platform
    monitor = PlatformHealthMonitor(hal)
    await monitor._check_all_platforms()

    status = monitor.get_status("frigate")
    assert status.state == HealthState.HEALTHY

    # Step 2: Context does NOT include error messages
    context = await build_embodied_context(
        hal=hal,
        platform_health=monitor,
        user="@test:server",
        intent="Zeige Kamera-Ereignisse",
        agent="security",
    )
    assert "ist offline" not in context

    # Step 3: Tool does NOT return offline error
    tool_context = ToolContext(
        hal=hal,
        presence_manager=presence_manager,
        energy_awareness=energy_awareness,
        rule_engine=hal.rules,
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
        topology=None,
        awareness=None,
        platform_health=monitor,
        latitude=52.52,
        longitude=11.0,
        timezone="Europe/Berlin",
    )
    set_context(tool_context)

    result = await get_camera_events(hours=24)
    assert "Frigate ist offline" not in result


@pytest.mark.asyncio
async def test_health_monitor_lifecycle_with_bootstrapper_pattern(hal: HAL):
    """Test the full lifecycle pattern used by Bootstrapper."""
    devices = [_make_device("frigate", "cam1", "Kamera", "Eingang")]
    platform = MockPlatform("frigate", devices, healthy=True)
    await hal.add_platform(platform)

    # Pattern from Bootstrapper.wire_hal()
    monitor = PlatformHealthMonitor(hal)
    assert monitor._hal is hal

    # Pattern from Bootstrapper.start()
    monitor.start()
    assert monitor._task is not None

    # Let it check
    await asyncio.sleep(0.1)
    assert monitor.get_status("frigate") is not None

    # Pattern from Bootstrapper.stop()
    monitor.stop()
    await asyncio.sleep(0.1)
    assert monitor._task.done() or monitor._task.cancelled()
