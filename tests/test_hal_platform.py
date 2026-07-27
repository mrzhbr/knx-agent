from __future__ import annotations

import pytest

from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType
from src.hal.platform import Platform
from datetime import datetime, timezone


class MockPlatform(Platform):
    """In-memory mock platform for testing."""

    def __init__(self, devices: list[DeviceInfo] | None = None) -> None:
        self._devices = devices or []
        self._states: dict[str, dict] = {}
        self._commands: list[tuple] = []
        self._started = False
        self._stopped = False

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
        return self._started

    async def discover_devices(self) -> list[DeviceInfo]:
        return self._devices

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        state = self._states.get(device_id.key, {})
        return DeviceState(
            device_id=device_id,
            state=state,
            last_updated=datetime.now(timezone.utc),
        )

    async def send_command(self, device_id: DeviceId, command: str, **params) -> None:
        self._commands.append((device_id, command, params))


async def test_mock_platform_lifecycle() -> None:
    platform = MockPlatform()
    await platform.start()
    assert platform._started is True
    await platform.stop()
    assert platform._stopped is True


async def test_mock_platform_discover() -> None:
    device_id = DeviceId(platform="mock", native_id="1")
    devices = [DeviceInfo(id=device_id, name="Lampe", device_type=DeviceType.LIGHT, room="Flur")]
    platform = MockPlatform(devices=devices)
    found = await platform.discover_devices()
    assert len(found) == 1
    assert found[0].name == "Lampe"


async def test_mock_platform_read_state() -> None:
    device_id = DeviceId(platform="mock", native_id="1")
    platform = MockPlatform()
    platform._states[device_id.key] = {"on": True}
    state = await platform.read_state(device_id)
    assert state.state["on"] is True


async def test_mock_platform_send_command() -> None:
    device_id = DeviceId(platform="mock", native_id="1")
    platform = MockPlatform()
    await platform.send_command(device_id, "turn_on", brightness=50)
    assert len(platform._commands) == 1
    assert platform._commands[0][1] == "turn_on"
    assert platform._commands[0][2]["brightness"] == 50


def test_abstract_methods_enforced() -> None:
    with pytest.raises(TypeError):
        Platform()  # type: ignore[abstract]


# --- Tests for Platform.send_command default behavior (Task 1.1) ---


async def test_send_command_default_raises_not_implemented() -> None:
    """A minimal Platform subclass without send_command should raise NotImplementedError."""
    from src.hal.models import DeviceId

    class ReadOnlyPlatform(Platform):
        @property
        def name(self) -> str:
            return "read_only"

        @property
        def display_name(self) -> str:
            return "Read Only"

        @property
        def error_message(self) -> str:
            return "⚠ Read Only ist offline."

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

        async def health_check(self) -> bool:
            return True

        async def discover_devices(self) -> list:
            return []

        async def read_state(self, device_id: DeviceId) -> DeviceState:
            return DeviceState(
                device_id=device_id,
                state={},
                last_updated=datetime.now(timezone.utc),
            )

    platform = ReadOnlyPlatform()
    import pytest

    with pytest.raises(NotImplementedError) as exc_info:
        await platform.send_command(DeviceId(platform="read_only", native_id="1"), "turn_on")
    assert "read_only" in str(exc_info.value)
