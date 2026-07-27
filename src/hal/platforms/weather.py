"""WeatherPlatform — HAL Platform adapter for Open-Meteo weather data.

Wraps WeatherAdapter (no persistent connection, 15-min cache) and adds
an asyncio poll loop that emits DeviceState changes when weather data
changes.

One virtual WEATHER_SENSOR device is exposed: native_id="weather_home".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType, SensorClass
from src.hal.platform import Platform
from src.integrations.weather import BRIGHTSKY_PRECIPITATION_CONDITIONS, PRECIPITATION_WMO_CODES

if TYPE_CHECKING:
    from src.config import BrainConfig

    pass

logger = logging.getLogger(__name__)

_WEATHER_DEVICE_ID = "weather_home"


class WeatherPlatform(Platform):
    """HAL Platform for weather data from Open-Meteo API.

    WeatherAdapter.start() is a no-op. The Platform creates its own
    asyncio poll loop that periodically calls adapter.get_current() and
    emits DeviceState changes when the weather data changes.
    """

    _name: str = "weather"

    def __init__(
        self,
        latitude: float,
        longitude: float,
        poll_interval_minutes: int = 15,
    ) -> None:
        self._latitude = latitude
        self._longitude = longitude
        self._poll_interval_seconds = poll_interval_minutes * 60
        self._adapter: Any = None
        self._poll_task: asyncio.Task | None = None
        self._last_state: dict | None = None
        self._event_callback: Callable[[DeviceState], Awaitable[None]] | None = None

    @classmethod
    def create_from_config(cls, config: "BrainConfig") -> "WeatherPlatform | None":
        """Create a WeatherPlatform from the application config.

        Does NOT pass poll_interval_minutes — lets __init__ use its default (15).
        """
        return cls(
            latitude=config.home.latitude,
            longitude=config.home.longitude,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def display_name(self) -> str:
        return "Wetterdienst"

    @property
    def error_message(self) -> str:
        return "⚠ Wetterdienst ist offline — keine aktuellen Wetterdaten."

    async def start(self) -> None:
        """Create WeatherAdapter and start the poll loop."""
        from src.integrations.weather import WeatherAdapter

        self._adapter = WeatherAdapter(
            latitude=self._latitude,
            longitude=self._longitude,
        )
        await self._adapter.start()  # no-op but keeps lifecycle consistent
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "WeatherPlatform started (lat=%.2f, lon=%.2f, poll=%ds)",
            self._latitude,
            self._longitude,
            self._poll_interval_seconds,
        )

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if self._adapter:
            await self._adapter.stop()
        logger.info("WeatherPlatform stopped")

    async def health_check(self) -> bool:
        """Open-Meteo has no auth — always healthy."""
        return True

    async def discover_devices(self) -> list[DeviceInfo]:
        """Return a single WEATHER_SENSOR device."""
        device_id = DeviceId(platform=self._name, native_id=_WEATHER_DEVICE_ID)
        info = DeviceInfo(
            id=device_id,
            name="Wetter",
            device_type=DeviceType.WEATHER_SENSOR,
            room="Aussen",
            sensor_class=SensorClass.TEMPERATURE,
            platform_data={},
        )
        return [info]

    async def read_state(self, device_id: DeviceId) -> DeviceState:
        """Return weather state from internal cache (populated by poll loop)."""
        data = self._last_state or {}
        return self._build_device_state(device_id, data)

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        """Store the HAL event callback. Poll loop will call it on data change."""
        self._event_callback = callback

    async def send_command(self, device_id: DeviceId, command: str, **params: Any) -> None:
        raise NotImplementedError("Platform 'weather' is read-only")

    # ── Public API for tools ──────────────────────────────────────────────

    async def get_summary(self) -> str:
        """Return a German one-liner describing current weather."""
        if self._adapter is None:
            return "Wetterdaten nicht verfügbar."
        return await self._adapter.get_summary()

    async def get_current(self) -> dict:
        """Return current weather data dict."""
        if self._adapter is None:
            return {}
        return await self._adapter.get_current()

    # ── Internal ──────────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Periodically fetch weather and emit state changes."""
        while True:
            try:
                new_data = await self._adapter.get_current()
                comparable = {k: v for k, v in new_data.items() if k != "timestamp"}
                last_comparable = {
                    k: v for k, v in (self._last_state or {}).items() if k != "timestamp"
                }
                if comparable != last_comparable:
                    self._last_state = new_data
                    if self._event_callback:
                        device_id = DeviceId(platform=self._name, native_id=_WEATHER_DEVICE_ID)
                        device_state = self._build_device_state(device_id, new_data)
                        await self._event_callback(device_state)
                await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Weather poll error: %s", e)
                await asyncio.sleep(self._poll_interval_seconds)

    def _build_device_state(self, device_id: DeviceId, data: dict) -> DeviceState:
        """Convert weather data dict to DeviceState."""
        # Determine is_raining from best available source
        condition = data.get("condition", "")
        weather_code = data.get("weather_code", 0)
        is_raining = (
            condition in BRIGHTSKY_PRECIPITATION_CONDITIONS
            if condition
            else weather_code in PRECIPITATION_WMO_CODES
        )

        return DeviceState(
            device_id=device_id,
            state={
                "temperature": data.get("temperature", 0.0),
                "humidity": data.get("humidity", 0),
                "weather_description": data.get("weather_description", "unbekannt"),
                "wind_speed": data.get("wind_speed", 0.0),
                "wind_gust_speed": data.get("wind_gust_speed", 0.0),
                "cloud_cover": data.get("cloud_cover", 0),
                "solar_irradiance_w_m2": data.get("solar_irradiance_w_m2", 0.0),
                "is_day": data.get("is_day", False),
                "is_raining": is_raining,
                "precipitation_mm": data.get("precipitation_mm", 0.0),
                "condition": data.get("condition", "dry"),
                "source": data.get("source", "icon_d2"),
            },
            last_updated=datetime.now(),
            source="weather",
        )
