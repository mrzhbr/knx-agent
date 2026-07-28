"""Vendor-neutral KNX/IP platform backed by an authoritative device map."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
import logging
from typing import Any, Protocol

from src.config import BrainConfig, KNXIPConfig
from src.hal.models import DeviceId, DeviceInfo, DeviceState
from src.hal.platform import Platform
from src.hal.platforms.knx_ip_commands import plan_command  # type: ignore[import-not-found]
from src.hal.platforms.knx_ip_ets import KNXETSValidationReport, validate_registry_against_project
from src.hal.platforms.knx_ip_registry import (
    KNXDeviceRegistry,
    KNXMapError,
    KNXMappedDevice,
    group_address_raw,
)
from src.hal.platforms.knx_ip_values import (  # type: ignore[import-not-found]
    normalize_state_value,
    state_key,
)
from src.integrations.knx_ip import KNXConnectionError, KNXIPAdapter
from src.integrations.knx_project import load_knx_project  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

EventCallback = Callable[[DeviceState], Awaitable[None]]
RegistryLoader = Callable[[str], KNXDeviceRegistry]
ProjectLoader = Callable[[str, str], Mapping[str, Any]]


class KNXAdapterProtocol(Protocol):
    @property
    def connected(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def health_check(self) -> bool: ...

    async def write(self, address: str, value: object, dpt: str) -> None: ...

    async def read(self, address: str, dpt: str, *, timeout: float) -> object | None: ...

    def set_update_callback(
        self,
        callback: Callable[[str, object], Awaitable[None]] | None,
    ) -> None: ...


AdapterFactory = Callable[..., Any]


async def _cancel_task(task: asyncio.Task[None] | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError as cancelled:
        del cancelled


class KNXIPPlatform(Platform):
    """Translate explicit KNX group-address roles into HAL devices and state."""

    def __init__(
        self,
        config: KNXIPConfig,
        *,
        registry_loader: RegistryLoader = KNXDeviceRegistry.load,
        project_loader: ProjectLoader = load_knx_project,
        adapter_factory: AdapterFactory = KNXIPAdapter,
    ) -> None:
        self._config = config
        self._registry_loader = registry_loader
        self._project_loader = project_loader
        self._adapter_factory = adapter_factory
        self._registry: KNXDeviceRegistry | None = None
        self._ets_report: KNXETSValidationReport | None = None
        self._adapter: KNXAdapterProtocol | None = None
        self._event_callback: EventCallback | None = None
        self._states: dict[str, dict[str, str | float | bool | None]] = {}
        self._initial_sync_task: asyncio.Task[None] | None = None
        self._reconnect_task: asyncio.Task[None] | None = None
        self._stopping = False

    @classmethod
    def create_from_config(cls, config: BrainConfig) -> KNXIPPlatform:
        return cls(config.knx_ip)

    @property
    def name(self) -> str:
        return "knx_ip"

    @property
    def display_name(self) -> str:
        return "KNX/IP"

    @property
    def error_message(self) -> str:
        return "⚠ KNX/IP ist offline — keine KNX-Gerätesteuerung möglich."

    async def start(self) -> None:
        if self._registry is not None:
            return
        self._stopping = False
        self._registry = self._registry_loader(self._config.device_map)
        self._initialize_unknown_states()
        if self._config.project_file:
            project = await asyncio.to_thread(
                self._project_loader,
                self._config.project_file,
                self._config.project_password,
            )
            self._ets_report = validate_registry_against_project(self._registry, project)
            self._ets_report.require_valid()
            if self._ets_report.data_secure_roles and not self._config.secure_keyfile:
                raise KNXMapError(
                    "ETS marks mapped group addresses as Data Secure, but no secure_keyfile is configured"
                )
            if self._ets_report.warnings:
                logger.warning(
                    "KNX ETS validation completed with %d warning(s)",
                    len(self._ets_report.warnings),
                )

        address_dpts = self._build_address_dpts()
        adapter: KNXAdapterProtocol = self._adapter_factory(
            self._config,
            address_dpts=address_dpts,
            update_callback=self._handle_update,
        )
        self._adapter = adapter
        try:
            await adapter.start()
        except KNXConnectionError:
            logger.warning("KNX/IP initial connection unavailable; retrying in background")
            self._reconnect_task = asyncio.create_task(
                self._reconnect_loop(),
                name="knx_ip_reconnect",
            )
        else:
            self._start_initial_sync()

    async def stop(self) -> None:
        self._stopping = True
        initial_sync_task = self._initial_sync_task
        reconnect_task = self._reconnect_task
        self._initial_sync_task = None
        self._reconnect_task = None
        await _cancel_task(initial_sync_task)
        await _cancel_task(reconnect_task)
        if self._adapter is not None:
            self._adapter.set_update_callback(None)
            await self._adapter.stop()
        self._adapter = None
        self._event_callback = None
        self._registry = None
        self._ets_report = None
        self._states.clear()

    async def health_check(self) -> bool:
        if self._adapter is None:
            return False
        return await self._adapter.health_check()

    async def discover_devices(self) -> list[DeviceInfo]:
        registry = self._require_registry()
        return [self._to_device_info(device) for device in registry.devices]

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        device = self._require_device(device_id)
        return DeviceState(
            device_id=device_id,
            state=dict(self._states[device.device_id]),
            last_updated=datetime.now(timezone.utc),
            source="external",
        )

    async def send_command(self, device_id: DeviceId, command: str, **params: Any) -> None:
        device = self._require_device(device_id)
        writes = plan_command(device, command, params, self._dpt_for)
        for write in writes:
            await self._write_role(device, write.role, write.value)

    async def subscribe_events(self, callback: EventCallback) -> None:
        self._event_callback = callback

    async def unsubscribe_events(self) -> None:
        self._event_callback = None

    def _build_address_dpts(self) -> dict[str, str]:
        registry = self._require_registry()
        by_raw_address: dict[int, tuple[str, str]] = {}
        for device in registry.devices:
            for role, address in device.addresses.items():
                dpt = self._dpt_for(device, role)
                if dpt is None:
                    raise KNXMapError(
                        f"Device {device.device_id!r} role {role!r} has no resolvable DPT"
                    )
                raw_address = group_address_raw(address)
                existing = by_raw_address.get(raw_address)
                if existing is not None and existing[1] != dpt:
                    raise KNXMapError(
                        f"Group address {address} has conflicting DPTs {existing[1]} and {dpt}"
                    )
                by_raw_address[raw_address] = (address, dpt)
        return {address: dpt for address, dpt in by_raw_address.values()}

    def _dpt_for(self, device: KNXMappedDevice, role: str) -> str | None:
        configured = device.dpt_for(role)
        if configured is not None:
            return configured
        if self._ets_report is not None:
            return self._ets_report.dpt_for(device.device_id, role)
        return None

    async def _write_role(self, device: KNXMappedDevice, role: str, value: object) -> None:
        adapter = self._require_adapter()
        address = device.addresses.get(role)
        dpt = self._dpt_for(device, role)
        if address is None or dpt is None:
            raise ValueError(f"KNX device {device.name!r} has no writable role {role!r}")
        await adapter.write(address, value, dpt)

    async def _handle_update(self, address: str, value: object) -> None:
        registry = self._require_registry()
        callback = self._event_callback
        for binding in registry.bindings_for_address(address):
            if not binding.role.endswith("_state"):
                continue
            device = registry.get(binding.device_id)
            if device is None:
                continue
            key = state_key(device, binding.role)
            normalized = normalize_state_value(key, value)
            device_state = self._states.setdefault(device.device_id, {})
            device_state[key] = normalized
            if callback is not None:
                await callback(
                    DeviceState(
                        device_id=DeviceId(platform=self.name, native_id=device.device_id),
                        state=dict(device_state),
                        last_updated=datetime.now(timezone.utc),
                        source="external",
                    )
                )

    def _initialize_unknown_states(self) -> None:
        for device in self._require_registry().devices:
            state = self._states.setdefault(device.device_id, {})
            for role in device.addresses:
                if role.endswith("_state"):
                    state.setdefault(state_key(device, role), None)

    def _start_initial_sync(self) -> None:
        if self._initial_sync_task is not None and not self._initial_sync_task.done():
            return
        self._initial_sync_task = asyncio.create_task(
            self._sync_initial_state(),
            name="knx_ip_initial_state",
        )

    async def _sync_initial_state(self) -> None:
        adapter = self._require_adapter()
        state_addresses: dict[int, tuple[str, str]] = {}
        for device in self._require_registry().devices:
            for role, address in device.addresses.items():
                if role.endswith("_state"):
                    dpt = self._dpt_for(device, role)
                    if dpt is not None:
                        state_addresses[group_address_raw(address)] = (address, dpt)
        semaphore = asyncio.Semaphore(16)

        async def read_address(address: str, dpt: str) -> None:
            async with semaphore:
                try:
                    value = await adapter.read(
                        address,
                        dpt,
                        timeout=self._config.initial_read_timeout,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("KNX initial read failed for %s: %s", address, exc)
                    return
                if value is not None:
                    await self._handle_update(address, value)

        await asyncio.gather(
            *(read_address(address, dpt) for address, dpt in state_addresses.values())
        )

    async def _reconnect_loop(self) -> None:
        delay = 3
        while not self._stopping and self._adapter is not None:
            await asyncio.sleep(delay)
            try:
                await self._adapter.start()
            except KNXConnectionError:
                delay = min(delay * 2, 60)
                continue
            logger.info("KNX/IP connection restored")
            self._start_initial_sync()
            return

    def _to_device_info(self, device: KNXMappedDevice) -> DeviceInfo:
        capabilities = {state_key(device, role) for role in device.addresses}
        return DeviceInfo(
            id=DeviceId(platform=self.name, native_id=device.device_id),
            name=device.name,
            device_type=device.device_type,
            room=device.room,
            floor=device.floor,
            sensor_class=device.sensor_class,
            capabilities=capabilities,
            platform_data={
                "mapping_id": device.device_id,
                "address_roles": sorted(device.addresses),
            },
        )

    def _require_registry(self) -> KNXDeviceRegistry:
        if self._registry is None:
            raise RuntimeError("KNXIPPlatform has not been started")
        return self._registry

    def _require_device(self, device_id: DeviceId) -> KNXMappedDevice:
        if device_id.platform != self.name:
            raise ValueError(f"Device {device_id.key!r} does not belong to KNX/IP")
        device = self._require_registry().get(device_id.native_id)
        if device is None:
            raise ValueError(f"Unknown KNX device {device_id.native_id!r}")
        return device

    def _require_adapter(self) -> KNXAdapterProtocol:
        if self._adapter is None:
            raise RuntimeError("KNXIPPlatform has not been started")
        return self._adapter
