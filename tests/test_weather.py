from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx as httpx_module

from src.integrations.weather import WeatherAdapter


def make_brightsky_response(
    temperature=12.5,
    humidity=65,
    wind_speed=8.3,
    cloud_cover=50,
    condition="rain",
    precipitation_60=1.2,
    station_name="Mannheim",
    station_distance=12000,
):
    """Create a mock httpx response for BrightSky."""
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(
        return_value={
            "weather": {
                "temperature": temperature,
                "relative_humidity": humidity,
                "wind_speed": wind_speed,
                "wind_gust_speed": 15.0,
                "cloud_cover": cloud_cover,
                "condition": condition,
                "precipitation_60": precipitation_60,
            },
            "sources": [
                {
                    "station_name": station_name,
                    "distance": station_distance,
                }
            ],
        }
    )
    return response


def make_openmeteo_response(
    temperature=13.0,
    humidity=60,
    weather_code=61,
    wind_speed=10.0,
    wind_gusts=18.0,
    cloud_cover=70,
    is_day=1,
    precipitation=0.5,
    hourly_times=None,
    hourly_radiation=None,
    minutely_times=None,
    minutely_precip=None,
):
    """Create a mock httpx response for Open-Meteo DWD-ICON."""
    if hourly_times is None:
        now = datetime.now()
        hourly_times = []
        hourly_radiation = []
        for day_offset in range(2):
            day = now.date() + timedelta(days=day_offset)
            for hour in range(24):
                hourly_times.append(f"{day}T{hour:02d}:00")
                if day == now.date() and hour == now.hour:
                    hourly_radiation.append(650.0)
                else:
                    hourly_radiation.append(100.0)

    if minutely_times is None:
        now = datetime.now()
        minutely_times = []
        minutely_precip = []
        for i in range(96):  # 24 hours of 15-min slots
            t = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0) + timedelta(
                minutes=15 * i
            )
            minutely_times.append(t.strftime("%Y-%m-%dT%H:%M"))
            minutely_precip.append(0.3 if i < 4 else 0.0)

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(
        return_value={
            "current": {
                "temperature_2m": temperature,
                "relative_humidity_2m": humidity,
                "weather_code": weather_code,
                "wind_speed_10m": wind_speed,
                "wind_gusts_10m": wind_gusts,
                "cloud_cover": cloud_cover,
                "is_day": is_day,
                "precipitation": precipitation,
            },
            "hourly": {
                "time": hourly_times,
                "shortwave_radiation": hourly_radiation,
            },
            "minutely_15": {
                "time": minutely_times,
                "precipitation": minutely_precip,
            },
        }
    )
    return response


def _mock_client_for(*responses):
    """Create a mock httpx.AsyncClient that returns responses in order."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=list(responses))
    return client


# ---------------------------------------------------------------------------
# Dual-source merge tests
# ---------------------------------------------------------------------------


async def test_get_current_prefers_brightsky_for_temperature():
    """BrightSky station temperature should be preferred over ICON-D2."""
    adapter = WeatherAdapter(latitude=49.53, longitude=8.35)
    client = _mock_client_for(
        make_brightsky_response(temperature=11.2),
        make_openmeteo_response(temperature=13.0),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        result = await adapter.get_current()

    assert result["temperature"] == 11.2
    assert result["source"] == "dwd_station"


async def test_get_current_falls_back_to_openmeteo():
    """When BrightSky fails, all data comes from Open-Meteo."""
    adapter = WeatherAdapter()
    brightsky_fail = MagicMock()
    brightsky_fail.raise_for_status = MagicMock(
        side_effect=httpx_module.HTTPStatusError("503", request=MagicMock(), response=MagicMock())
    )

    client = _mock_client_for(
        brightsky_fail,
        make_openmeteo_response(temperature=13.0, weather_code=0),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        result = await adapter.get_current()

    assert result["temperature"] == 13.0
    assert result["source"] == "icon_d2"


async def test_get_current_returns_all_expected_fields():
    """get_current() returns dict with all expected keys."""
    adapter = WeatherAdapter()
    client = _mock_client_for(
        make_brightsky_response(),
        make_openmeteo_response(),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        result = await adapter.get_current()

    expected_keys = {
        "temperature",
        "humidity",
        "weather_code",
        "weather_description",
        "wind_speed",
        "wind_gust_speed",
        "cloud_cover",
        "is_day",
        "solar_irradiance_w_m2",
        "precipitation_mm",
        "rain_next_hours",
        "condition",
        "source",
        "station_name",
        "station_distance_km",
        "timestamp",
    }
    assert expected_keys.issubset(result.keys())


async def test_get_current_maps_wmo_code_to_german():
    """WMO weather code is translated to German description."""
    adapter = WeatherAdapter()
    client = _mock_client_for(
        make_brightsky_response(condition="dry"),
        make_openmeteo_response(weather_code=0),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        result = await adapter.get_current()

    assert result["weather_description"] == "klar"


async def test_brightsky_rain_overrides_description():
    """BrightSky condition 'rain' overrides weather_description."""
    adapter = WeatherAdapter()
    client = _mock_client_for(
        make_brightsky_response(condition="thunderstorm"),
        make_openmeteo_response(weather_code=0),  # ICON says clear
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        result = await adapter.get_current()

    assert result["weather_description"] == "Gewitter"
    assert result["condition"] == "thunderstorm"


# ---------------------------------------------------------------------------
# Summary tests
# ---------------------------------------------------------------------------


async def test_get_summary_returns_german_string():
    """get_summary() returns a German one-liner with temperature."""
    adapter = WeatherAdapter()
    client = _mock_client_for(
        make_brightsky_response(temperature=12.5, wind_speed=8.3),
        make_openmeteo_response(),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        summary = await adapter.get_summary()

    assert "°C" in summary
    assert isinstance(summary, str)
    assert len(summary) > 5


async def test_get_summary_wind_categories():
    """get_summary categorizes wind speed correctly."""
    adapter = WeatherAdapter()
    client = _mock_client_for(
        make_brightsky_response(wind_speed=2.0),
        make_openmeteo_response(),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        summary = await adapter.get_summary()
    assert "windstill" in summary


async def test_get_summary_includes_station_name():
    """get_summary() includes DWD station name when from BrightSky."""
    adapter = WeatherAdapter()
    client = _mock_client_for(
        make_brightsky_response(station_name="Mannheim"),
        make_openmeteo_response(),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        summary = await adapter.get_summary()

    assert "Mannheim" in summary


async def test_get_summary_includes_rain_forecast():
    """get_summary() mentions rain forecast when precipitation expected."""
    adapter = WeatherAdapter()

    now = datetime.now()
    minutely_times = []
    minutely_precip = []
    for i in range(16):
        t = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0) + timedelta(
            minutes=15 * i
        )
        minutely_times.append(t.strftime("%Y-%m-%dT%H:%M"))
        minutely_precip.append(0.5 if i < 8 else 0.0)

    client = _mock_client_for(
        make_brightsky_response(condition="dry"),
        make_openmeteo_response(minutely_times=minutely_times, minutely_precip=minutely_precip),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        summary = await adapter.get_summary()

    assert "Regen" in summary


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------


async def test_get_current_caches_result():
    """Second call within cache TTL does not make another HTTP request."""
    adapter = WeatherAdapter(cache_minutes=15)
    client = _mock_client_for(
        make_brightsky_response(),
        make_openmeteo_response(),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        await adapter.get_current()
        await adapter.get_current()  # Should use cache

    # httpx.AsyncClient should only be instantiated once (one call to __aenter__)
    assert client.get.call_count == 2  # brightsky + openmeteo, not 4


async def test_get_current_returns_stale_cache_on_error():
    """On API failure, returns stale cache if available."""
    adapter = WeatherAdapter()

    # First successful call
    client_ok = _mock_client_for(
        make_brightsky_response(temperature=20.0),
        make_openmeteo_response(temperature=21.0),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client_ok):
        await adapter.get_current()

    # Force cache expiry
    adapter._cache_time = datetime.now() - timedelta(hours=1)

    # Second call — both sources fail
    client_fail = AsyncMock()
    client_fail.__aenter__ = AsyncMock(return_value=client_fail)
    client_fail.__aexit__ = AsyncMock(return_value=False)
    client_fail.get = AsyncMock(side_effect=httpx_module.ConnectError("unreachable"))

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client_fail):
        result = await adapter.get_current()

    assert result["temperature"] == 20.0


# ---------------------------------------------------------------------------
# Solar irradiance tests
# ---------------------------------------------------------------------------


async def test_get_current_returns_solar_fields():
    """get_current() returns solar_irradiance_w_m2, cloud_cover, is_day."""
    adapter = WeatherAdapter()
    client = _mock_client_for(
        make_brightsky_response(cloud_cover=20),
        make_openmeteo_response(cloud_cover=20, is_day=1),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        result = await adapter.get_current()

    assert "solar_irradiance_w_m2" in result
    assert "is_day" in result
    assert result["is_day"] is True
    assert result["solar_irradiance_w_m2"] == 650.0


async def test_solar_irradiance_uses_timestamp_matching():
    """Solar extraction matches current hour by timestamp prefix."""
    adapter = WeatherAdapter()

    now = datetime.now()
    times = []
    radiation = []
    for day_offset in range(3):
        day = now.date() + timedelta(days=day_offset - 1)
        for hour in range(24):
            times.append(f"{day}T{hour:02d}:00")
            if day == now.date() and hour == now.hour:
                radiation.append(750.0)
            else:
                radiation.append(50.0)

    client = _mock_client_for(
        make_brightsky_response(),
        make_openmeteo_response(hourly_times=times, hourly_radiation=radiation),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        result = await adapter.get_current()

    assert result["solar_irradiance_w_m2"] == 750.0


# ---------------------------------------------------------------------------
# Rain forecast tests
# ---------------------------------------------------------------------------


async def test_rain_next_hours_populated():
    """rain_next_hours is populated from minutely_15 data."""
    adapter = WeatherAdapter()

    now = datetime.now()
    minutely_times = []
    minutely_precip = []
    for i in range(16):
        t = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0) + timedelta(
            minutes=15 * i
        )
        minutely_times.append(t.strftime("%Y-%m-%dT%H:%M"))
        minutely_precip.append(0.2 if i < 4 else 0.0)

    client = _mock_client_for(
        make_brightsky_response(),
        make_openmeteo_response(minutely_times=minutely_times, minutely_precip=minutely_precip),
    )

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        result = await adapter.get_current()

    assert len(result["rain_next_hours"]) > 0
    assert result["rain_next_hours"][0] == 0.2


# ---------------------------------------------------------------------------
# Error fallback tests
# ---------------------------------------------------------------------------


async def test_error_fallback_includes_all_fields():
    """Error fallback includes all expected fields with safe defaults."""
    adapter = WeatherAdapter()

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(side_effect=httpx_module.ConnectError("unreachable"))

    with patch("src.integrations.weather.httpx.AsyncClient", return_value=client):
        result = await adapter.get_current()

    assert result["temperature"] == 0.0
    assert result["is_day"] is False
    assert result["cloud_cover"] == 100
    assert result["solar_irradiance_w_m2"] == 0.0
    assert result["condition"] == "dry"
    assert result["source"] == "error"
    assert result["rain_next_hours"] == []


# ---------------------------------------------------------------------------
# WMO to condition mapping
# ---------------------------------------------------------------------------


def test_wmo_to_condition_rain():
    assert WeatherAdapter._wmo_to_condition(61) == "rain"


def test_wmo_to_condition_snow():
    assert WeatherAdapter._wmo_to_condition(73) == "snow"


def test_wmo_to_condition_thunderstorm():
    assert WeatherAdapter._wmo_to_condition(95) == "thunderstorm"


def test_wmo_to_condition_fog():
    assert WeatherAdapter._wmo_to_condition(45) == "fog"


def test_wmo_to_condition_dry():
    assert WeatherAdapter._wmo_to_condition(0) == "dry"
