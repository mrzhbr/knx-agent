"""Low-level XKNX lifecycle, typed group I/O, and telegram callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Mapping
from enum import Enum
import logging
from typing import Any

from xknx import XKNX  # type: ignore[import-not-found]
from xknx.core import XknxConnectionState  # type: ignore[import-not-found]
from xknx.dpt import DPTBase  # type: ignore[import-not-found]
from xknx.io import (  # type: ignore[import-not-found]
    ConnectionConfig,
    ConnectionType,
    SecureConfig,
)
from xknx.telegram import GroupAddress  # type: ignore[import-not-found]
from xknx.tools import (  # type: ignore[import-not-found]
    group_value_write,
    read_group_value,
)

from src.config import KNXIPConfig

logger = logging.getLogger(__name__)

UpdateCallback = Callable[[str, object], Coroutine[Any, Any, None]]
XKNXFactory = Callable[..., Any]


class KNXConnectionError(ConnectionError):
    """Raised when XKNX cannot establish its initial connection."""


def build_connection_config(config: KNXIPConfig) -> ConnectionConfig:
    """Translate stable public configuration into XKNX connection settings."""
    connection_types = {
        "automatic": ConnectionType.AUTOMATIC,
        "tunneling": ConnectionType.TUNNELING,
        "tunneling_tcp": ConnectionType.TUNNELING_TCP,
        "routing": ConnectionType.ROUTING,
    }
    connection_type = connection_types[config.connection_type]
    secure_config: SecureConfig | None = None
    if config.secure_keyfile:
        if not config.keyfile_password:
            raise ValueError("KNX_KEYFILE_PASSWORD is required for an ETS keyfile")
        secure_config = SecureConfig(
            knxkeys_file_path=config.secure_keyfile,
            knxkeys_password=config.keyfile_password,
        )
    if config.secure:
        if config.connection_type == "routing":
            connection_type = ConnectionType.ROUTING_SECURE
        elif config.connection_type != "automatic":
            connection_type = ConnectionType.TUNNELING_TCP_SECURE

    return ConnectionConfig(
        connection_type=connection_type,
        individual_address=config.individual_address or None,
        local_ip=config.local_ip or None,
        gateway_ip=config.gateway_ip or None,
        gateway_port=config.gateway_port,
        multicast_group=config.multicast_group,
        auto_reconnect=True,
        secure_config=secure_config,
    )


class KNXIPAdapter:
    """Own one XKNX instance and expose only typed group operations."""

    def __init__(
        self,
        config: KNXIPConfig,
        *,
        address_dpts: Mapping[str, str] | None = None,
        update_callback: UpdateCallback | None = None,
        xknx_factory: XKNXFactory = XKNX,
    ) -> None:
        self._config = config
        self._address_dpts = dict(address_dpts or {})
        self._update_callback = update_callback
        self._xknx_factory = xknx_factory
        self._xknx: Any | None = None
        self._telegram_handle: Any | None = None
        self._callback_tasks: set[asyncio.Task[None]] = set()

    @property
    def connected(self) -> bool:
        return bool(
            self._xknx is not None
            and self._xknx.connection_manager.state is XknxConnectionState.CONNECTED
        )

    async def start(self) -> None:
        if self._xknx is not None:
            return
        for dpt in set(self._address_dpts.values()):
            _require_supported_dpt(dpt)
        connection_config = build_connection_config(self._config)
        xknx = self._xknx_factory(
            connection_config=connection_config,
            rate_limit=self._config.rate_limit,
        )
        self._xknx = xknx
        if self._address_dpts:
            xknx.group_address_dpt.set(self._address_dpts)
        filters = [GroupAddress(address) for address in self._address_dpts]
        self._telegram_handle = xknx.telegram_queue.register_telegram_received_cb(
            self._handle_telegram,
            group_addresses=filters or None,
            match_for_outgoing=False,
        )
        try:
            await xknx.start()
        except Exception as exc:
            self._unregister_telegram_callback()
            try:
                await xknx.stop()
            except Exception as cleanup_error:  # noqa: BLE001
                logger.debug("XKNX cleanup after connection failure failed: %s", cleanup_error)
            self._xknx = None
            raise KNXConnectionError("KNX/IP initial connection failed") from exc

    async def stop(self) -> None:
        xknx = self._xknx
        if xknx is None:
            return
        self._unregister_telegram_callback()
        self._update_callback = None
        if self._callback_tasks:
            await asyncio.gather(*self._callback_tasks, return_exceptions=True)
            self._callback_tasks.clear()
        try:
            await xknx.stop()
        finally:
            self._xknx = None

    async def health_check(self) -> bool:
        return self.connected

    async def write(self, address: str, value: object, dpt: str) -> None:
        xknx = self._require_connected()
        _require_supported_dpt(dpt)
        group_value_write(xknx, address, value, dpt)
        await xknx.join()

    async def read(self, address: str, dpt: str, *, timeout: float) -> object | None:
        xknx = self._require_connected()
        _require_supported_dpt(dpt)
        return await asyncio.wait_for(
            read_group_value(xknx, address, dpt),
            timeout=timeout,
        )

    def set_update_callback(self, callback: UpdateCallback | None) -> None:
        self._update_callback = callback

    def _require_connected(self) -> Any:
        if not self.connected or self._xknx is None:
            raise RuntimeError("KNX/IP is not connected")
        return self._xknx

    def _handle_telegram(self, telegram: Any) -> None:
        callback = self._update_callback
        decoded = getattr(telegram, "decoded_data", None)
        destination = getattr(telegram, "destination_address", None)
        if callback is None or decoded is None or destination is None:
            return
        value = decoded.value
        if isinstance(value, Enum):
            value = value.value
        task: asyncio.Task[None] = asyncio.create_task(callback(str(destination), value))
        self._callback_tasks.add(task)
        task.add_done_callback(self._callback_done)

    def _callback_done(self, task: asyncio.Task[None]) -> None:
        self._callback_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error("KNX state callback failed: %s", error)

    def _unregister_telegram_callback(self) -> None:
        if self._xknx is None or self._telegram_handle is None:
            return
        self._xknx.telegram_queue.unregister_telegram_received_cb(self._telegram_handle)
        self._telegram_handle = None


def _require_supported_dpt(dpt: str) -> None:
    if DPTBase.parse_transcoder(dpt) is None:
        raise ValueError(f"Unsupported KNX DPT: {dpt}")
