"""Weather adapter — dual source: BrightSky (DWD observations) + Open-Meteo ICON-D2 (forecast).

BrightSky provides actual measured data from the nearest DWD station.
Open-Meteo ICON-D2 provides 2km-resolution forecast, solar irradiance, and 15-min precipitation.
Results are cached for 10 minutes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# WMO weather interpretation codes → German description
WMO_CODES: dict[int, str] = {
    0: "klar",
    1: "überwiegend klar",
    2: "teilweise bewölkt",
    3: "bewölkt",
    45: "neblig",
    48: "Raureifnebel",
    51: "leichter Nieselregen",
    53: "Nieselregen",
    55: "starker Nieselregen",
    61: "leichter Regen",
    63: "Regen",
    65: "starker Regen",
    71: "leichter Schneefall",
    73: "Schneefall",
    75: "starker Schneefall",
    77: "Schneekörner",
    80: "leichte Regenschauer",
    81: "Regenschauer",
    82: "starke Regenschauer",
    85: "Schneeschauer",
    86: "starke Schneeschauer",
    95: "Gewitter",
    96: "Gewitter mit Hagel",
    99: "Gewitter mit starkem Hagel",
}

# BrightSky condition → German description
BRIGHTSKY_CONDITIONS: dict[str, str] = {
    "dry": "trocken",
    "fog": "neblig",
    "rain": "Regen",
    "sleet": "Schneeregen",
    "snow": "Schneefall",
    "hail": "Hagel",
    "thunderstorm": "Gewitter",
}

# BrightSky conditions that count as precipitation
BRIGHTSKY_PRECIPITATION_CONDITIONS: frozenset[str] = frozenset(
    {"rain", "sleet", "snow", "hail", "thunderstorm"}
)

# All WMO codes indicating precipitation (rain, drizzle, snow, showers, thunderstorms)
PRECIPITATION_WMO_CODES: frozenset[int] = frozenset(
    {
        51,
        53,
        55,  # Nieselregen (drizzle)
        61,
        63,
        65,  # Regen (rain)
        71,
        73,
        75,
        77,  # Schnee (snow)
        80,
        81,
        82,  # Regenschauer (rain showers)
        85,
        86,  # Schneeschauer (snow showers)
        95,
        96,
        99,  # Gewitter (thunderstorms)
    }
)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/dwd-icon"
BRIGHTSKY_URL = "https://api.brightsky.dev/current_weather"


class WeatherAdapter:
    """Dual-source weather: BrightSky observations + Open-Meteo ICON-D2 forecast.

    BrightSky provides actual DWD station measurements (temperature, humidity,
    wind, precipitation). Open-Meteo ICON-D2 provides high-resolution forecast
    data (solar irradiance, weather code, 15-min precipitation forecast).

    Falls back to Open-Meteo only if BrightSky is unavailable.

    Usage::

        weather = WeatherAdapter(latitude=49.53, longitude=8.35)
        data = await weather.get_current()
        summary = await weather.get_summary()
    """

    name: str = "weather"

    def __init__(
        self,
        latitude: float = 49.53,
        longitude: float = 8.35,
        cache_minutes: int = 10,
    ) -> None:
        self._latitude = latitude
        self._longitude = longitude
        self._cache_ttl = timedelta(minutes=cache_minutes)
        self._cache: dict[str, Any] | None = None
        self._cache_time: datetime | None = None

    async def start(self) -> None:
        """No-op — no persistent connection needed."""

    async def stop(self) -> None:
        """No-op."""

    async def health_check(self) -> dict[str, Any]:
        return {"name": self.name, "healthy": True, "status": "ok"}

    async def get_current(self) -> dict[str, Any]:
        """Fetch or return cached current weather data from both sources.

        Returns dict with:
          temperature: float (°C) — from DWD station (BrightSky) or ICON-D2
          humidity: int (%) — from DWD station or ICON-D2
          weather_code: int (WMO code) — from ICON-D2
          weather_description: str (German) — best available description
          wind_speed: float (km/h) — from DWD station or ICON-D2
          wind_gust_speed: float (km/h) — from DWD station if available
          cloud_cover: int (%) — from DWD station or ICON-D2
          is_day: bool — from ICON-D2
          solar_irradiance_w_m2: float (W/m²) — from ICON-D2 hourly
          precipitation_mm: float (mm/h) — from DWD station observation
          rain_next_hours: list[float] — 15-min precipitation forecast (mm)
          condition: str — BrightSky condition (dry/rain/snow/...) or derived
          source: str — "dwd_station" if BrightSky, "icon_d2" if Open-Meteo only
          station_name: str — nearest DWD station name (if BrightSky)
          station_distance_km: float — distance to DWD station (if BrightSky)
          timestamp: str (ISO)
        """
        if self._cache and self._cache_time:
            age = datetime.now() - self._cache_time
            if age < self._cache_ttl:
                return self._cache

        try:
            result = await self._fetch_merged()
            self._cache = result
            self._cache_time = datetime.now()
            logger.debug(
                "Weather [%s]: %.1f°C, %s, %.0f km/h, solar: %.0f W/m²",
                result.get("source", "?"),
                result["temperature"],
                result["weather_description"],
                result["wind_speed"],
                result["solar_irradiance_w_m2"],
            )
            return result

        except Exception as e:
            logger.warning("Weather fetch failed: %s", e)
            if self._cache:
                return self._cache
            return self._error_fallback()

    async def _fetch_merged(self) -> dict[str, Any]:
        """Fetch from both sources and merge. BrightSky for observations, ICON-D2 for forecast."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Fetch both in parallel-ish (sequential but fast)
            brightsky_data = await self._fetch_brightsky(client)
            openmeteo_data = await self._fetch_open_meteo(client)

        return self._merge_sources(brightsky_data, openmeteo_data)

    async def _fetch_brightsky(self, client: httpx.AsyncClient) -> dict[str, Any] | None:
        """Fetch current observations from BrightSky (DWD stations)."""
        try:
            response = await client.get(
                BRIGHTSKY_URL,
                params={
                    "lat": self._latitude,
                    "lon": self._longitude,
                    "tz": "Europe/Berlin",
                },
            )
            response.raise_for_status()
            data = response.json()
            weather = data.get("weather", {})
            sources = data.get("sources", [])
            source_info = sources[0] if sources else {}

            return {
                "temperature": weather.get("temperature"),
                "humidity": weather.get("relative_humidity"),
                "wind_speed": weather.get("wind_speed"),
                "wind_gust_speed": weather.get("wind_gust_speed"),
                "cloud_cover": weather.get("cloud_cover"),
                "precipitation_mm": weather.get("precipitation_10", 0.0)
                or weather.get("precipitation_30", 0.0)
                or weather.get("precipitation_60", 0.0)
                or 0.0,
                "condition": weather.get("condition", "dry"),
                "station_name": source_info.get("station_name", ""),
                "station_distance_km": round(source_info.get("distance", 0) / 1000, 1),
            }
        except Exception as e:
            logger.debug("BrightSky unavailable: %s", e)
            return None

    async def _fetch_open_meteo(self, client: httpx.AsyncClient) -> dict[str, Any]:
        """Fetch forecast from Open-Meteo DWD-ICON (ICON-D2 model)."""
        response = await client.get(
            OPEN_METEO_URL,
            params={
                "latitude": self._latitude,
                "longitude": self._longitude,
                "models": "icon_d2",
                "current": (
                    "temperature_2m,relative_humidity_2m,weather_code,"
                    "wind_speed_10m,wind_gusts_10m,cloud_cover,is_day,"
                    "precipitation"
                ),
                "hourly": "shortwave_radiation",
                "minutely_15": "precipitation",
                "forecast_days": 1,
                "wind_speed_unit": "kmh",
                "timezone": "Europe/Berlin",
            },
        )
        response.raise_for_status()
        data = response.json()

        current = data.get("current", {})
        weather_code = int(current.get("weather_code", 0))
        solar_irradiance = self._extract_current_solar(data)
        rain_next_hours = self._extract_rain_forecast(data)

        return {
            "temperature": current.get("temperature_2m", 0.0),
            "humidity": current.get("relative_humidity_2m", 0),
            "weather_code": weather_code,
            "weather_description": WMO_CODES.get(weather_code, f"Code {weather_code}"),
            "wind_speed": current.get("wind_speed_10m", 0.0),
            "wind_gust_speed": current.get("wind_gusts_10m", 0.0),
            "cloud_cover": current.get("cloud_cover", 0),
            "is_day": bool(current.get("is_day", 0)),
            "solar_irradiance_w_m2": solar_irradiance,
            "precipitation_mm": current.get("precipitation", 0.0),
            "rain_next_hours": rain_next_hours,
        }

    def _merge_sources(
        self,
        brightsky: dict[str, Any] | None,
        openmeteo: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge BrightSky observations with Open-Meteo forecast.

        BrightSky wins for: temperature, humidity, wind, cloud_cover, precipitation
        Open-Meteo wins for: weather_code, solar, is_day, rain forecast
        """
        result: dict[str, Any] = {
            # Always from Open-Meteo ICON-D2
            "weather_code": openmeteo["weather_code"],
            "weather_description": openmeteo["weather_description"],
            "is_day": openmeteo["is_day"],
            "solar_irradiance_w_m2": openmeteo["solar_irradiance_w_m2"],
            "rain_next_hours": openmeteo.get("rain_next_hours", []),
            "timestamp": datetime.now().isoformat(),
        }

        if brightsky and brightsky.get("temperature") is not None:
            # Use DWD station observations (actual measurements)
            result["temperature"] = brightsky["temperature"]
            result["humidity"] = brightsky.get("humidity") or openmeteo["humidity"]
            result["wind_speed"] = brightsky.get("wind_speed") or openmeteo["wind_speed"]
            result["wind_gust_speed"] = brightsky.get("wind_gust_speed") or 0.0
            result["cloud_cover"] = (
                brightsky["cloud_cover"]
                if brightsky.get("cloud_cover") is not None
                else openmeteo["cloud_cover"]
            )
            result["precipitation_mm"] = brightsky.get("precipitation_mm", 0.0)
            result["condition"] = brightsky.get("condition", "dry")
            result["source"] = "dwd_station"
            result["station_name"] = brightsky.get("station_name", "")
            result["station_distance_km"] = brightsky.get("station_distance_km", 0.0)

            # Override weather_description with BrightSky condition if it's precipitation
            condition = brightsky.get("condition", "dry")
            if condition in BRIGHTSKY_CONDITIONS and condition != "dry":
                result["weather_description"] = BRIGHTSKY_CONDITIONS[condition]
        else:
            # BrightSky unavailable — use Open-Meteo for everything
            result["temperature"] = openmeteo["temperature"]
            result["humidity"] = openmeteo["humidity"]
            result["wind_speed"] = openmeteo["wind_speed"]
            result["wind_gust_speed"] = openmeteo.get("wind_gust_speed", 0.0)
            result["cloud_cover"] = openmeteo["cloud_cover"]
            result["precipitation_mm"] = openmeteo.get("precipitation_mm", 0.0)
            result["condition"] = self._wmo_to_condition(openmeteo["weather_code"])
            result["source"] = "icon_d2"
            result["station_name"] = ""
            result["station_distance_km"] = 0.0

        return result

    def _extract_current_solar(self, data: dict[str, Any]) -> float:
        """Extract shortwave_radiation for the current hour from hourly data."""
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        radiation = hourly.get("shortwave_radiation", [])

        if not times or not radiation:
            return 0.0

        now = datetime.now()
        current_hour_prefix = now.strftime("%Y-%m-%dT%H")

        for i, t in enumerate(times):
            if isinstance(t, str) and t.startswith(current_hour_prefix):
                if i < len(radiation):
                    val = radiation[i]
                    return float(val) if val is not None else 0.0
                break

        return 0.0

    def _extract_rain_forecast(self, data: dict[str, Any]) -> list[float]:
        """Extract next 4 hours of 15-min precipitation from minutely_15 data.

        Returns up to 16 values (4 hours × 4 per hour) starting from the current
        15-min slot.
        """
        minutely = data.get("minutely_15", {})
        times = minutely.get("time", [])
        precip = minutely.get("precipitation", [])

        if not times or not precip:
            return []

        now = datetime.now()
        # Find the current or next 15-min slot
        current_minute = now.strftime("%Y-%m-%dT%H:%M")

        start_idx = 0
        for i, t in enumerate(times):
            if isinstance(t, str) and t >= current_minute:
                start_idx = i
                break

        # Return up to 16 entries (4 hours)
        values = precip[start_idx : start_idx + 16]
        return [float(v) if v is not None else 0.0 for v in values]

    @staticmethod
    def _wmo_to_condition(weather_code: int) -> str:
        """Map WMO code to BrightSky-style condition string."""
        if weather_code in {95, 96, 99}:
            return "thunderstorm"
        if weather_code in {71, 73, 75, 77, 85, 86}:
            return "snow"
        if weather_code in {51, 53, 55, 61, 63, 65, 80, 81, 82}:
            return "rain"
        if weather_code in {45, 48}:
            return "fog"
        return "dry"

    async def get_summary(self) -> str:
        """Return a German one-liner describing current weather.

        Example: '12°C, leichter Regen, leichter Wind (8 km/h), Sonne: 650 W/m²'
        Includes station info and rain forecast when relevant.
        """
        data = await self.get_current()
        temp = data["temperature"]
        desc = data["weather_description"]
        wind = data["wind_speed"]
        solar = data.get("solar_irradiance_w_m2", 0.0)
        source = data.get("source", "icon_d2")
        precip = data.get("precipitation_mm", 0.0)

        if wind < 5:
            wind_str = "windstill"
        elif wind < 20:
            wind_str = f"leichter Wind ({wind:.0f} km/h)"
        elif wind < 40:
            wind_str = f"mäßiger Wind ({wind:.0f} km/h)"
        else:
            wind_str = f"starker Wind ({wind:.0f} km/h)"

        base = f"{temp:.0f}°C, {desc}, {wind_str}"
        if solar > 0:
            base += f", Sonne: {solar:.0f} W/m²"
        if precip > 0:
            base += f", Niederschlag: {precip:.1f} mm/h"

        # Add rain forecast if rain expected in next hours
        rain_forecast = data.get("rain_next_hours", [])
        if rain_forecast and any(v > 0 for v in rain_forecast):
            rain_slots = sum(1 for v in rain_forecast if v > 0)
            hours = rain_slots * 0.25  # each slot is 15 min
            base += f", Regen in den nächsten {hours:.0f}h erwartet"

        if source == "dwd_station":
            station = data.get("station_name", "")
            if station:
                base += f" (DWD: {station})"

        return base

    @staticmethod
    def _error_fallback() -> dict[str, Any]:
        """Return safe defaults when all sources fail."""
        return {
            "temperature": 0.0,
            "humidity": 0,
            "weather_code": -1,
            "weather_description": "unbekannt",
            "wind_speed": 0.0,
            "wind_gust_speed": 0.0,
            "cloud_cover": 100,
            "is_day": False,
            "solar_irradiance_w_m2": 0.0,
            "precipitation_mm": 0.0,
            "rain_next_hours": [],
            "condition": "dry",
            "source": "error",
            "station_name": "",
            "station_distance_km": 0.0,
            "timestamp": datetime.now().isoformat(),
        }
