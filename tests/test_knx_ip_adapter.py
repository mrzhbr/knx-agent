"""Tests for the low-level XKNX protocol adapter."""

from __future__ import annotations

import asyncio
from enum import Enum
from types import SimpleNamespace
from typing import Any

import pytest  # type: ignore[import-not-found]
from xknx.core import XknxConnectionState  # type: ignore[import-not-found]
from xknx.io import ConnectionType  # type: ignore[import-not-found]

from src.config import KNXIPConfig
from src.integrations.knx_ip import (  # type: ignore[import-not-found]
    KNXConnectionError,
    KNXIPAdapter,
    build_connection_config,
)


class _DPTRegistry:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, values: dict[str, str]) -> None:
        self.values = values


class _TelegramQueue:
    def __init__(self) -> None:
        self.callback: Any = None
        self.handle = object()
        self.unregistered: Any = None

    def register_telegram_received_cb(self, callback: Any, **_: Any) -> object:
        self.callback = callback
        return self.handle

    def unregister_telegram_received_cb(self, handle: object) -> None:
        self.unregistered = handle


class _FakeXKNX:
    def __init__(self, **_: Any) -> None:
        self.group_address_dpt = _DPTRegistry()
        self.telegram_queue = _TelegramQueue()
        self.connection_manager = SimpleNamespace(state=XknxConnectionState.DISCONNECTED)
        self.started = False
        self.stopped = False
        self.joined = False

    async def start(self) -> None:
        self.started = True
        self.connection_manager.state = XknxConnectionState.CONNECTED

    async def stop(self) -> None:
        self.stopped = True
        self.connection_manager.state = XknxConnectionState.DISCONNECTED

    async def join(self) -> None:
        self.joined = True


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("automatic", ConnectionType.AUTOMATIC),
        ("tunneling", ConnectionType.TUNNELING),
        ("tunneling_tcp", ConnectionType.TUNNELING_TCP),
        ("routing", ConnectionType.ROUTING),
    ],
)
def test_build_connection_config_maps_public_modes(configured: str, expected: ConnectionType):
    values: dict[str, object] = {"connection_type": configured}
    if configured in {"tunneling", "tunneling_tcp"}:
        values["gateway_ip"] = "192.0.2.10"
    config = KNXIPConfig.model_validate(values)

    connection = build_connection_config(config)

    assert connection.connection_type is expected
    assert connection.gateway_port == 3671


def test_secure_tunneling_uses_tcp_secure_and_runtime_password():
    config = KNXIPConfig(
        connection_type="tunneling",
        gateway_ip="192.0.2.10",
        secure=True,
        secure_keyfile="secrets/installation.knxkeys",
        keyfile_password="runtime-only",
    )

    connection = build_connection_config(config)

    assert connection.connection_type is ConnectionType.TUNNELING_TCP_SECURE
    assert connection.secure_config is not None
    assert connection.secure_config.knxkeys_file_path == "secrets/installation.knxkeys"
    assert connection.secure_config.knxkeys_password == "runtime-only"


def test_data_secure_keyfile_does_not_force_secure_ip_transport():
    config = KNXIPConfig(
        connection_type="tunneling",
        gateway_ip="192.0.2.10",
        secure_keyfile="secrets/installation.knxkeys",
        keyfile_password="runtime-only",
    )

    connection = build_connection_config(config)

    assert connection.connection_type is ConnectionType.TUNNELING
    assert connection.secure_config is not None


def test_secure_routing_uses_routing_secure():
    config = KNXIPConfig(
        connection_type="routing",
        secure=True,
        secure_keyfile="secrets/installation.knxkeys",
        keyfile_password="runtime-only",
    )

    assert build_connection_config(config).connection_type is ConnectionType.ROUTING_SECURE


async def test_adapter_rejects_unsupported_dpt_before_connecting():
    fake = _FakeXKNX()
    adapter = KNXIPAdapter(
        KNXIPConfig(),
        address_dpts={"1/0/1": "999.999"},
        xknx_factory=lambda **_: fake,
    )

    with pytest.raises(ValueError, match="Unsupported KNX DPT"):
        await adapter.start()
    assert not fake.started


async def test_adapter_lifecycle_registers_dpts_and_callback():
    fake = _FakeXKNX()
    adapter = KNXIPAdapter(
        KNXIPConfig(),
        address_dpts={"1/0/1": "1.001"},
        xknx_factory=lambda **_: fake,
    )

    await adapter.start()

    assert fake.started
    assert adapter.connected
    assert fake.group_address_dpt.values == {"1/0/1": "1.001"}
    assert fake.telegram_queue.callback is not None

    await adapter.stop()

    assert fake.stopped
    assert fake.telegram_queue.unregistered is fake.telegram_queue.handle


async def test_initial_connection_failure_has_typed_error():
    class FailingXKNX(_FakeXKNX):
        async def start(self) -> None:
            raise OSError("gateway unavailable")

    fake = FailingXKNX()
    adapter = KNXIPAdapter(KNXIPConfig(), xknx_factory=lambda **_: fake)

    with pytest.raises(KNXConnectionError, match="initial connection"):
        await adapter.start()
    assert not adapter.connected
    assert fake.stopped


async def test_write_is_typed_and_waits_for_xknx_queue(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeXKNX()
    writes: list[tuple[str, object, str]] = []

    def capture_write(_: Any, address: str, value: object, value_type: str) -> None:
        writes.append((address, value, value_type))

    monkeypatch.setattr("src.integrations.knx_ip.group_value_write", capture_write)
    adapter = KNXIPAdapter(KNXIPConfig(), xknx_factory=lambda **_: fake)
    await adapter.start()

    await adapter.write("1/0/1", True, "1.001")

    assert len(writes) == 1
    address, value, value_type = writes[0]
    assert address == "1/0/1"
    assert bool(value)
    assert value_type == "1.001"
    assert fake.joined


async def test_write_fails_fast_while_disconnected():
    fake = _FakeXKNX()
    adapter = KNXIPAdapter(KNXIPConfig(), xknx_factory=lambda **_: fake)

    with pytest.raises(RuntimeError, match="not connected"):
        await adapter.write("1/0/1", True, "1.001")


async def test_read_uses_typed_group_read(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeXKNX()
    reads: list[tuple[str, str]] = []

    async def capture_read(_: Any, address: str, value_type: str) -> float:
        reads.append((address, value_type))
        return 21.5

    monkeypatch.setattr("src.integrations.knx_ip.read_group_value", capture_read)
    adapter = KNXIPAdapter(KNXIPConfig(), xknx_factory=lambda **_: fake)
    await adapter.start()

    value = await adapter.read("2/0/1", "9.001", timeout=1.0)

    assert value == 21.5
    assert len(reads) == 1
    address, value_type = reads[0]
    assert address == "2/0/1"
    assert value_type == "9.001"


async def test_xknx_enum_values_are_unwrapped_before_state_callback():
    class Switch(Enum):
        OFF = False

    fake = _FakeXKNX()
    updates: list[object] = []

    async def on_update(_address: str, value: object) -> None:
        updates.append(value)

    adapter = KNXIPAdapter(
        KNXIPConfig(),
        update_callback=on_update,
        xknx_factory=lambda **_: fake,
    )
    await adapter.start()
    telegram = SimpleNamespace(
        destination_address="1/1/1",
        decoded_data=SimpleNamespace(value=Switch.OFF),
    )

    fake.telegram_queue.callback(telegram)
    await asyncio.sleep(0)

    assert len(updates) == 1
    assert not bool(updates[0])


async def test_callback_failure_is_consumed_and_logged(caplog: pytest.LogCaptureFixture):
    fake = _FakeXKNX()

    async def failing_callback(_address: str, _value: object) -> None:
        raise RuntimeError("state sink failed")

    adapter = KNXIPAdapter(
        KNXIPConfig(),
        update_callback=failing_callback,
        xknx_factory=lambda **_: fake,
    )
    await adapter.start()
    telegram = SimpleNamespace(
        destination_address="2/1/1",
        decoded_data=SimpleNamespace(value=True),
    )

    fake.telegram_queue.callback(telegram)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "state callback failed" in caplog.text


async def test_incoming_decoded_telegram_is_forwarded_asynchronously():
    fake = _FakeXKNX()
    updates: list[tuple[str, object]] = []

    async def on_update(address: str, value: object) -> None:
        updates.append((address, value))

    adapter = KNXIPAdapter(
        KNXIPConfig(),
        update_callback=on_update,
        xknx_factory=lambda **_: fake,
    )
    await adapter.start()
    telegram = SimpleNamespace(
        destination_address="2/1/1",
        decoded_data=SimpleNamespace(value=True),
    )

    fake.telegram_queue.callback(telegram)
    await asyncio.sleep(0)

    assert len(updates) == 1
    address, value = updates[0]
    assert address == "2/1/1"
    assert bool(value)
