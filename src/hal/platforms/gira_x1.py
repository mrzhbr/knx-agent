"""Gira X1 platform adapter for the Hardware Abstraction Layer (HAL).

This module bridges the HAL Core abstraction to the existing Gira X1 integration,
mapping Gira X1 functions and rooms to HAL's DeviceType/DeviceInfo model,
routing commands through the Gira X1 REST API, and forwarding push events
via the embedded HTTPS webhook server.

Architecture: GiraX1Platform owns the full lifecycle of GiraX1Adapter and
_device_registry (private implementation detail). It creates, starts, and stops them internally,
making other platforms (Control4, Loxone, raw KNX) pluggable with the same interface.
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import tempfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from aiohttp import web

from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass
from src.hal.platform import Platform

if TYPE_CHECKING:
    from src.config import BrainConfig, GiraX1Config
    from src.hal.platforms.gira_x1_registry import Device, DeviceRegistry
    from src.hal.state import StateStore
    from src.integrations.gira_x1 import GiraX1Adapter

logger = logging.getLogger(__name__)


# Verbatim copy from house_state.py — pure function, no behavioral changes.
def _generate_self_signed_cert() -> tuple[bytes, bytes]:
    """Generate a self-signed certificate and private key in PEM format."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "knx-agent"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=timezone.utc))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return cert_pem, key_pem


# Maps Gira function type strings (from FUNCTION_TYPE_MAP values) to HAL DeviceType.
# Sources: src/hal/platforms/gira_x1_registry.py FUNCTION_TYPE_MAP
# Rationale for approximate mappings:
#   tunable_light -> DIMMABLE_LIGHT  (it is dimmable, just adds color temperature)
#   sauna         -> HEATING         (temperature control semantics)
#   trigger_onoff -> TRIGGER         (distinction preserved in platform_data)
_GIRA_TYPE_TO_HAL: dict[str, DeviceType] = {
    "dimmable_light": DeviceType.DIMMABLE_LIGHT,
    "switch": DeviceType.SWITCH,
    "colored_light": DeviceType.COLORED_LIGHT,
    "tunable_light": DeviceType.DIMMABLE_LIGHT,  # closest fit; no TunableLight in HAL
    "blind": DeviceType.BLIND,
    "heating": DeviceType.HEATING,
    "sauna": DeviceType.HEATING,  # closest fit; has temperature control
    "binary_sensor": DeviceType.BINARY_SENSOR,
    "camera": DeviceType.CAMERA,
    "trigger": DeviceType.TRIGGER,
    "trigger_onoff": DeviceType.TRIGGER,  # distinction preserved in platform_data
    "scene": DeviceType.SCENE,
}

# Maps Gira device subtype strings to HAL SensorClass.
# window_door -> WINDOW: deliberate — KNX contacts cannot distinguish window vs door.
_GIRA_SUBTYPE_TO_SENSOR_CLASS: dict[str, SensorClass] = {
    "window_door": SensorClass.WINDOW,
    "alarm_readiness": SensorClass.ALARM_READINESS,
    "": SensorClass.GENERIC,
}


class GiraX1Platform(Platform):
    """HAL platform adapter for Gira X1 home automation system.

    Lifecycle ownership pattern: GiraX1Platform takes a GiraX1Config and
    creates/owns GiraX1Adapter and DeviceRegistry internally.
    External code creates only the platform; the platform manages all component
    lifecycles via start()/start_runtime()/stop().

    Lifecycle:
        start()             -- creates GiraX1Adapter (started), DeviceRegistry
        start_runtime()     -- starts embedded HTTPS webhook server + callback registration + snapshot
        discover_devices()  -- converts DeviceRegistry entries to DeviceInfo list
        subscribe_events()  -- stores callback; webhook handler calls it directly
        send_command()      -- routes commands to GiraX1Adapter via datapoint UIDs
        read_state()        -- reads from StateStore cache or GiraX1Adapter API
        health_check()      -- delegates to GiraX1Adapter.health_check()
        unsubscribe_events()-- nulls event callback
        stop()              -- cancels poll task, unregisters callbacks, stops server + adapter

    Properties (available after start()):
        _device_registry    -- DeviceRegistry parsed from uiconfig (private, use HAL interface externally)
        gira_adapter        -- GiraX1Adapter for direct access (e.g. FrigateAdapter)

    Name collision note: GiraX1Platform.name returns "gira_x1" for HAL's _platforms
    dict, but this platform is NOT registered with IntegrationBus -- that is reserved
    for GiraX1Adapter itself.
    """

    def __init__(
        self,
        config: "GiraX1Config",
    ) -> None:
        """Initialize with config.

        Components (GiraX1Adapter, DeviceRegistry) are created lazily inside start() -- not here.

        Args:
            config: Gira X1 configuration (host, token, local_ip, callback_port).
        """
        self._config = config

        # Components created by start(); None until then
        self._gira: "GiraX1Adapter | None" = None
        self._registry: "DeviceRegistry | None" = None
        # Populated by discover_devices(); maps native_id (Gira function UID) -> Device
        self._device_map: dict[str, "Device"] = {}

        # Set via set_state_store() after subscribe_events(); required before start_runtime()
        self._state_store: "StateStore | None" = None

        # Stored by subscribe_events(); nulled by unsubscribe_events()
        self._event_callback: Callable[[DeviceState], Awaitable[None]] | None = None

        # Webhook server state (created by start_runtime())
        self._runner: web.AppRunner | None = None
        self._poll_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._callbacks_registered: bool = False

    @classmethod
    def create_from_config(cls, config: "BrainConfig") -> "GiraX1Platform | None":
        """Create a GiraX1Platform from the application config.

        GiraX1 is mandatory — always returns an instance.
        """
        return cls(config=config.gira_x1)

    @property
    def name(self) -> str:
        """Unique platform identifier used as key in HAL._platforms dict."""
        return "gira_x1"

    @property
    def display_name(self) -> str:
        return "Gira X1"

    @property
    def error_message(self) -> str:
        return "⚠ Gira X1 ist offline — keine Gerätesteuerung möglich."

    # --- Read-only properties for external consumers --- #

    @property
    def _device_registry(self) -> "DeviceRegistry":
        """DeviceRegistry built from uiconfig. Available after start(). Private -- use HAL interface externally."""
        if self._registry is None:
            raise RuntimeError("_device_registry not available: call start() first")
        return self._registry

    @property
    def gira_adapter(self) -> "GiraX1Adapter":
        """GiraX1Adapter for direct access (e.g. FrigateAdapter). Available after start()."""
        if self._gira is None:
            raise RuntimeError("gira_adapter not available: call start() first")
        return self._gira

    def set_state_store(self, store: "StateStore") -> None:
        """Set the StateStore reference for snapshot writes, read_state() cache lookups,
        and building DeviceState objects in the webhook handler.

        Called by HAL.add_platform() after subscribe_events(). Must be called before
        start_runtime() -- a RuntimeError will be raised otherwise.
        """
        self._state_store = store

    async def start(self) -> None:
        """Create and start GiraX1Adapter, build DeviceRegistry.

        On uiconfig failure: logs error, creates empty DeviceRegistry (graceful fallback).
        """
        from src.hal.platforms.gira_x1_registry import DeviceRegistry
        from src.integrations.gira_x1 import GiraX1Adapter

        self._gira = GiraX1Adapter(
            host=self._config.host,
            token=self._config.token,
            verify_tls=self._config.verify_tls,
            ca_bundle=self._config.ca_bundle,
        )
        await self._gira.start()

        try:
            uiconfig = await self._gira.fetch_uiconfig()
            self._registry = DeviceRegistry.from_json(uiconfig)
        except Exception as e:
            logger.error("GiraX1Platform.start(): uiconfig fetch failed: %s", e)
            logger.warning("Starting with empty DeviceRegistry -- device control will be limited")
            self._registry = DeviceRegistry()

        logger.info(
            "GiraX1Platform.start(): adapter started, registry built (%d rooms)",
            len(self._registry.all_rooms()),
        )

    async def start_runtime(self) -> None:
        """Start HTTPS webhook listener, register callbacks with X1, take initial snapshot.

        Must be called after start() and set_state_store(), and before populate_initial_state().
        This is a GiraX1Platform-specific method, not part of the Platform ABC.

        Raises:
            RuntimeError: If set_state_store() has not been called yet.
        """
        if self._state_store is None:
            raise RuntimeError(
                "StateStore not set -- call set_state_store() before start_runtime()"
            )
        if self._gira is None:
            raise RuntimeError("start() must be called before start_runtime()")
        callback_token = self._config.callback_token
        if not callback_token:
            logger.warning(
                "Gira callbacks disabled because GIRA_X1_CALLBACK_TOKEN is not configured; "
                "using polling only"
            )
            await self._take_snapshot()
            self._poll_task = asyncio.create_task(self._poll_loop())
            return

        # 1. Generate self-signed cert and create SSL context
        cert_pem, key_pem = _generate_self_signed_cert()
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as cf:
            cf.write(cert_pem)
            cert_path = cf.name
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pem") as kf:
            kf.write(key_pem)
            key_path = kf.name
        try:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ssl_ctx.load_cert_chain(cert_path, key_path)
        finally:
            os.unlink(cert_path)
            os.unlink(key_path)

        # 2. Create aiohttp app and start HTTPS server
        app = web.Application()
        app.router.add_post(f"/{callback_token}/value", self._handle_value_callback)
        app.router.add_post(f"/{callback_token}/service", self._handle_service_callback)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(
            self._runner,
            self._config.callback_host,
            self._config.callback_port,
            ssl_context=ssl_ctx,
        )
        await site.start()
        logger.info(
            "GiraX1Platform HTTPS webhook listener started on port %d",
            self._config.callback_port,
        )

        # 3. Register callbacks with X1 (retry pattern: first with testCallbacks, then without)
        local_ip = self._config.local_ip
        port = self._config.callback_port
        service_url = f"https://{local_ip}:{port}/{callback_token}/service"
        value_url = f"https://{local_ip}:{port}/{callback_token}/value"

        ok = await self._gira.register_callbacks(
            service_url=service_url,
            value_url=value_url,
            test=True,
        )
        if not ok:
            logger.warning("X1 callback registration failed. Retrying without testCallbacks...")
            ok = await self._gira.register_callbacks(
                service_url=service_url,
                value_url=value_url,
                test=False,
            )
            if not ok:
                logger.warning(
                    "X1 callback registration failed twice. Falling back to polling only."
                )
        else:
            self._callbacks_registered = True

        # 4. Take initial snapshot (populates StateStore directly)
        await self._take_snapshot()

        # 5. Start 5-minute polling fallback
        self._poll_task = asyncio.create_task(self._poll_loop())

        logger.info("GiraX1Platform.start_runtime(): webhook server + callbacks + snapshot done")

    async def stop(self) -> None:
        """Tear down: cancel poll task, unregister callbacks, cleanup runner, stop adapter."""
        # 1. Cancel polling fallback
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError as cancellation:
                del cancellation
            self._poll_task = None

        # 2. Unregister callbacks with X1
        if self._gira is not None and self._callbacks_registered:
            await self._gira.unregister_callbacks()

        # 3. Cleanup webhook server runner
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

        # 4. Stop GiraX1Adapter
        if self._gira is not None:
            await self._gira.stop()

        logger.info("GiraX1Platform.stop(): teardown complete")

    async def health_check(self) -> bool:
        """Delegate health check to GiraX1Adapter. Returns False if not started."""
        if self._gira is None:
            return False
        return await self._gira.health_check()

    async def discover_devices(self) -> list[DeviceInfo]:
        """Convert all DeviceRegistry entries to HAL DeviceInfo objects.

        Returns empty list if start() has not been called yet.
        Skips devices with type="unknown" (logged as warning). Populates
        self._device_map for use by send_command() and read_state().

        Returns:
            List of DeviceInfo for all successfully mapped devices.
        """
        if self._registry is None:
            return []

        result: list[DeviceInfo] = []
        skipped = 0

        for room in self._registry.all_rooms():
            for device in room.devices:
                hal_type = _GIRA_TYPE_TO_HAL.get(device.type)
                if hal_type is None:
                    logger.warning(
                        "Skipping device %s (%s): unknown Gira type %r",
                        device.name,
                        device.uid,
                        device.type,
                    )
                    skipped += 1
                    continue

                # Room lookup with fallback for unassigned devices
                room_obj = self._registry.get_room(device.room_key)
                if room_obj is not None:
                    room_name = room_obj.display_name
                    floor_name = room_obj.floor
                else:
                    room_name = "Unbekannt"
                    floor_name = ""

                sensor_class = _GIRA_SUBTYPE_TO_SENSOR_CLASS.get(
                    device.subtype, SensorClass.GENERIC
                )
                capabilities = set(device.datapoints.keys())

                info = DeviceInfo(
                    id=DeviceId(platform="gira_x1", native_id=device.uid),
                    name=device.name,
                    device_type=hal_type,
                    room=room_name,
                    floor=floor_name,
                    sensor_class=sensor_class,
                    capabilities=capabilities,
                    is_group=device.is_group_controller,
                    platform_data={
                        "gira_uid": device.uid,
                        "gira_type": device.type,
                        "datapoints": device.datapoints,
                        "room_key": device.room_key,
                    },
                )
                result.append(info)
                self._device_map[device.uid] = device

        room_count = len({d.room_key for d in self._device_map.values()})
        logger.info(
            "GiraX1Platform: discovered %d devices in %d rooms (%d skipped, unknown type)",
            len(result),
            room_count,
            skipped,
        )
        if skipped > 0:
            logger.warning("Skipped %d devices with unknown Gira type", skipped)

        return result

    async def send_command(self, device_id: DeviceId, command: str, **params: Any) -> None:
        """Dispatch a HAL command to the Gira X1 datapoint API.

        Uses a table-driven dispatcher. Brain-action marking is NOT done here --
        that is HAL.execute()'s responsibility (it marks before calling send_command).

        Args:
            device_id: HAL device identifier (native_id is the Gira function UID).
            command: HAL command name (e.g. "turn_on", "set_brightness", "open").
            **params: Command-specific parameters.

        Raises:
            ValueError: If device not found or required datapoint missing.
        """
        gira = self._gira
        if gira is None:
            raise RuntimeError("GiraX1Platform not started")

        device = self._device_map.get(device_id.native_id)
        if device is None:
            raise ValueError(
                f"Gerät '{device_id.native_id}' nicht in der Plattform registriert. "
                f"Wurde discover_devices() aufgerufen?"
            )

        logger.debug(
            "GiraX1Platform.send_command: device=%s command=%s params=%s",
            device.name,
            command,
            params,
        )

        def _require(name: str) -> str:
            """Return the UID for a named datapoint, or raise ValueError."""
            uid = device.datapoints.get(name)
            if uid is None:
                raise ValueError(
                    f"Datenpunkt '{name}' fehlt bei Gerät '{device.name}' "
                    f"(Typ: {device.type}). Befehl nicht ausführbar."
                )
            return uid

        if command == "turn_on":
            onoff_uid = _require("OnOff")
            if "brightness" in params:
                brightness_uid = _require("Brightness")
                await gira.set_values({onoff_uid: "1", brightness_uid: str(params["brightness"])})
            else:
                await gira.set_value(onoff_uid, "1")

        elif command == "turn_off":
            await gira.set_value(_require("OnOff"), "0")

        elif command == "set_brightness":
            onoff_uid = _require("OnOff")
            brightness_uid = _require("Brightness")
            await gira.set_values({onoff_uid: "1", brightness_uid: str(params["value"])})

        elif command == "open":
            await gira.set_value(_require("Up-Down"), "0")  # 0 = up/open per Gira KNX

        elif command == "close":
            await gira.set_value(_require("Up-Down"), "1")  # 1 = down/close per Gira KNX

        elif command == "stop":
            # Gira KNX convention: writing 1 to Step-Up-Down sends a momentary stop pulse
            await gira.set_value(_require("Step-Up-Down"), "1")

        elif command == "set_temperature":
            await gira.set_value(_require("Set-Point"), str(params["value"]))

        elif command == "set_mode":
            # No-op: climate mode control not supported on Gira X1
            logger.warning(
                "Climate mode control not supported for device %s -- Befehl set_mode ignoriert",
                device.name,
            )

        elif command == "set_color":
            red_uid = _require("Red")
            green_uid = _require("Green")
            blue_uid = _require("Blue")
            values: dict[str, str] = {
                red_uid: str(params["red"]),
                green_uid: str(params["green"]),
                blue_uid: str(params["blue"]),
            }
            if "white" in params and "White" in device.datapoints:
                values[device.datapoints["White"]] = str(params["white"])
            await gira.set_values(values)

        elif command == "trigger":
            await gira.set_value(_require("Trigger"), "1")

        elif command == "set_binary":
            # Tries OnOff first; falls back to Binary; silently returns if neither exists
            value = str(params.get("value", "0"))
            if "OnOff" in device.datapoints:
                await gira.set_value(device.datapoints["OnOff"], value)
            elif "Binary" in device.datapoints:
                await gira.set_value(device.datapoints["Binary"], value)
            else:
                logger.warning(
                    "set_binary: neither OnOff nor Binary datapoint found for %s -- ignoriert",
                    device.name,
                )
                return

        else:
            logger.warning(
                "Unbekannter Befehl %r fuer Geraet %s -- wird ignoriert", command, device.name
            )

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        """Read current state for a device from StateStore cache or API fallback.

        First checks StateStore (SQLite). Falls back to GiraX1Adapter.get_value()
        if no cached values are available. All returned state dicts are keyed by
        datapoint name (not UID).

        Args:
            device_id: HAL device identifier.

        Returns:
            DeviceState with name-keyed state dict and UTC timestamp.

        Raises:
            RuntimeError: If set_state_store() has not been called.
            ValueError: If device is not registered.
        """
        if self._state_store is None:
            raise RuntimeError(
                "StateStore not set -- call set_state_store() before using read_state()"
            )
        gira = self._gira
        if gira is None:
            raise RuntimeError("GiraX1Platform not started")

        device = self._device_map.get(device_id.native_id)
        if device is None:
            raise ValueError(f"Gerät '{device_id.native_id}' nicht in der Plattform registriert.")

        # Try StateStore first (async SQLite read)
        cached_state = await self._state_store.get_state(device_id)
        if cached_state is not None:
            return cached_state

        # Fallback: query API and translate UIDs to names
        uid_to_name = {uid: name for name, uid in device.datapoints.items()}
        state_dict: dict[str, str | float | bool | None] = {}
        try:
            raw = await gira.get_value(device.uid)
            for uid, value in raw.items():
                dp_name = uid_to_name.get(uid)
                if dp_name is None:
                    logger.warning(
                        "read_state: unbekannte UID %r in API-Antwort für %s",
                        uid,
                        device.name,
                    )
                    continue
                state_dict[dp_name] = value
        except Exception as e:
            logger.error("read_state: API-Fehler für %s: %s", device.name, e)

        return DeviceState(
            device_id=device_id,
            state=state_dict,
            last_updated=datetime.now(timezone.utc),
            source="external",
        )

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        """Subscribe to Gira X1 state change events.

        Stores the callback; the webhook handler (_handle_value_callback) calls it
        directly when X1 push events arrive.

        Args:
            callback: Async callable that receives DeviceState on each change.
        """
        self._event_callback = callback

    async def unsubscribe_events(self) -> None:
        """Unsubscribe from push events by nulling the event callback.

        The webhook handler checks _event_callback before firing, so nulling it
        cleanly stops event delivery.
        """
        logger.info("GiraX1Platform.unsubscribe_events(): nulling event callback")
        self._event_callback = None

    # --- Webhook handlers --- #

    async def _handle_value_callback(self, request: web.Request) -> web.Response:
        """Handle POST /value from X1: translate UID events to DeviceState and fire callback."""
        if self._state_store is None:
            raise RuntimeError(
                "StateStore not set -- call set_state_store() before start_runtime()"
            )
        try:
            body = await request.json()
        except Exception:
            return web.Response(status=400, text="Invalid JSON")

        events = body.get("events", [])
        for event in events:
            uid = event.get("uid")
            value = event.get("value")
            if uid and value is not None:
                if self._registry is None or self._event_callback is None:
                    continue

                # Look up device that owns this datapoint UID
                device = self._registry.lookup_datapoint(uid)
                if device is None:
                    logger.debug("_handle_value_callback: unknown UID %r -- skipped", uid)
                    continue

                # Build UID-to-name reverse lookup
                uid_to_name = {dp_uid: dp_name for dp_name, dp_uid in device.datapoints.items()}

                # Get current state from in-memory cache (no SQLite I/O on hot path)
                device_id = DeviceId(platform="gira_x1", native_id=device.uid)
                current = self._state_store.get_state_sync(device_id) or {}

                # Merge the incoming value (name-keyed) into the current state
                dp_name = uid_to_name.get(uid)
                if dp_name is not None:
                    state_dict = dict(current)
                    state_dict[dp_name] = str(value)
                else:
                    logger.debug(
                        "_handle_value_callback: UID %r not in device %s datapoints",
                        uid,
                        device.name,
                    )
                    continue

                device_state = DeviceState(
                    device_id=device_id,
                    state=state_dict,
                    last_updated=datetime.now(timezone.utc),
                )
                # Fire callback ONLY -- HAL._event_callback calls state_store.update()
                # Do NOT call self._state_store.update() here (no double-write)
                asyncio.create_task(self._event_callback(device_state))  # type: ignore[arg-type]

        return web.Response(status=200)

    async def _handle_service_callback(self, request: web.Request) -> web.Response:
        """Handle POST /service from X1: log service events and warn on config changes."""
        try:
            body = await request.json()
            events = body.get("events", [])
            for event in events:
                event_type = event.get("event", "unknown")
                logger.info("X1 service event: %s", event_type)
                if event_type in ("uiConfigChanged", "projectConfigChanged"):
                    logger.warning(
                        "X1 config changed -- DeviceRegistry may be stale. Restart recommended."
                    )
        except Exception as e:
            logger.warning("Service callback error: %s", e)
        return web.Response(status=200)

    async def _take_snapshot(self) -> None:
        """Fetch all device values concurrently and write to StateStore."""
        if self._state_store is None:
            raise RuntimeError(
                "StateStore not set -- call set_state_store() before _take_snapshot()"
            )
        gira = self._gira
        state_store = self._state_store
        if self._registry is None or gira is None:
            return

        all_uids = self._registry.all_function_uids()
        sem = asyncio.Semaphore(10)
        count = 0

        async def fetch_one(function_uid: str) -> None:
            nonlocal count
            async with sem:
                try:
                    # get_value() returns {datapoint_uid: value}
                    values = await gira.get_value(function_uid)

                    # Look up device to get its datapoint name mapping
                    device = self._device_map.get(function_uid)
                    if device is None:
                        return  # not yet discovered or unmapped
                    uid_to_name = {dp_uid: dp_name for dp_name, dp_uid in device.datapoints.items()}

                    # Build name-keyed state dict
                    state_dict: dict[str, Any] = {}
                    for dp_uid, value in values.items():
                        dp_name = uid_to_name.get(dp_uid)
                        if dp_name is not None:
                            state_dict[dp_name] = value
                        else:
                            logger.debug(
                                "_take_snapshot: unknown UID %r for device %s",
                                dp_uid,
                                device.name,
                            )

                    if state_dict:
                        device_id = DeviceId(platform="gira_x1", native_id=function_uid)
                        device_state = DeviceState(
                            device_id=device_id,
                            state=state_dict,
                            last_updated=datetime.now(timezone.utc),
                            source="external",
                        )
                        await state_store.update(device_state)

                    count += 1
                    if count % 50 == 0:
                        logger.info("Snapshot progress: %d/%d devices", count, len(all_uids))
                except Exception as e:
                    logger.debug("_take_snapshot: failed to fetch %s: %s", function_uid, e)

        await asyncio.gather(*[fetch_one(uid) for uid in all_uids])
        logger.info(
            "_take_snapshot complete: %d/%d devices fetched",
            count,
            len(all_uids),
        )

    async def _poll_loop(self) -> None:
        """Poll X1 every 5 minutes as fallback for missed callbacks."""
        while True:
            try:
                await asyncio.sleep(5 * 60)
                logger.info("GiraX1Platform: polling X1 state (fallback)...")
                await self._take_snapshot()
            except asyncio.CancelledError as cancellation:
                del cancellation
                break
            except Exception as e:
                logger.error("GiraX1Platform: poll loop error: %s", e)
