"""Protocol conformance tests for HAL protocol definitions.

These tests verify that the real implementations in src.core and src.integrations
satisfy the HAL protocols defined in src.hal.protocols. Uses @runtime_checkable
isinstance checks which verify structural compatibility.

Conformance map:
- PresenceQuery <- src.core.presence.PresenceManager
- TopologyQuery <- src.core.topology.HouseTopology
- WeatherSource <- src.integrations.weather.WeatherAdapter
- EnergySource  <- src.core.awareness.EnergyAwareness
- ConfigSource  <- src.config.BrainConfig (checked via attribute presence)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from src.hal.protocols import (
    EnergySource,
    PresenceQuery,
    TopologyQuery,
    WeatherSource,
)


# ---------------------------------------------------------------------------
# PresenceQuery conformance: PresenceManager.nobody_home() -> bool
# ---------------------------------------------------------------------------


def test_presence_manager_satisfies_presence_query() -> None:
    """PresenceManager satisfies PresenceQuery (has nobody_home() -> bool)."""
    from src.core.presence import PresenceManager

    from unittest.mock import MagicMock

    presence = PresenceManager(arp_presence=MagicMock())
    assert isinstance(presence, PresenceQuery), (
        "PresenceManager does not satisfy PresenceQuery — missing nobody_home()"
    )


def test_presence_query_nobody_home_callable() -> None:
    """PresenceManager.nobody_home() is callable without error."""
    from src.core.presence import PresenceManager

    from unittest.mock import MagicMock

    mock_arp = MagicMock()
    mock_arp.nobody_home.return_value = (
        True  # PresenceManager.nobody_home() delegates to _arp.nobody_home()
    )
    presence = PresenceManager(arp_presence=mock_arp)
    result = presence.nobody_home()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# TopologyQuery conformance: HouseTopology.get_open_to, assess_solar_exposure
# ---------------------------------------------------------------------------


def test_house_topology_satisfies_topology_query() -> None:
    """HouseTopology satisfies TopologyQuery (has get_open_to and assess_solar_exposure)."""
    from src.core.topology import HouseTopology

    topology = HouseTopology(rooms={}, floors=[], staircases=[])
    assert isinstance(topology, TopologyQuery), (
        "HouseTopology does not satisfy TopologyQuery — missing required methods"
    )


def test_topology_query_get_open_to_callable() -> None:
    """HouseTopology.get_open_to() is callable and returns list or None."""
    from src.core.topology import HouseTopology

    topology = HouseTopology(rooms={}, floors=[], staircases=[])
    result = topology.get_open_to("nonexistent_room")
    # Should return None or a list (not raise)
    assert result is None or isinstance(result, list)


# ---------------------------------------------------------------------------
# WeatherSource conformance: WeatherAdapter.get_current() -> dict
# ---------------------------------------------------------------------------


def test_weather_adapter_satisfies_weather_source() -> None:
    """WeatherAdapter satisfies WeatherSource (has async get_current() -> dict)."""
    from src.integrations.weather import WeatherAdapter

    # @runtime_checkable only checks for method existence (not async), so isinstance works
    adapter = WeatherAdapter()
    assert isinstance(adapter, WeatherSource), (
        "WeatherAdapter does not satisfy WeatherSource — missing get_current()"
    )


async def test_weather_source_get_current_is_async() -> None:
    """WeatherAdapter.get_current() is async and returns a dict."""
    from src.integrations.weather import WeatherAdapter

    adapter = WeatherAdapter()
    # Patch httpx to avoid real network calls
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "current": {
            "temperature_2m": 15.5,
            "weather_code": 1,
            "wind_speed_10m": 5.0,
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await adapter.get_current()

    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# EnergySource conformance: EnergyAwareness.estimated_watts -> float
# ---------------------------------------------------------------------------


def test_energy_awareness_satisfies_energy_source() -> None:
    """EnergyAwareness satisfies EnergySource (has estimated_watts property)."""
    from unittest.mock import MagicMock
    from src.core.awareness import EnergyAwareness

    mock_store = MagicMock()
    mock_store.on_change = MagicMock()
    mock_hal = MagicMock()
    energy = EnergyAwareness(state_store=mock_store, hal=mock_hal)
    assert isinstance(energy, EnergySource), (
        "EnergyAwareness does not satisfy EnergySource — missing estimated_watts property"
    )


def test_energy_source_estimated_watts_is_float() -> None:
    """EnergyAwareness.estimated_watts returns a float."""
    from unittest.mock import MagicMock
    from src.core.awareness import EnergyAwareness

    mock_store = MagicMock()
    mock_store.on_change = MagicMock()
    mock_hal = MagicMock()
    energy = EnergyAwareness(state_store=mock_store, hal=mock_hal)
    result = energy.estimated_watts
    assert isinstance(result, int) or isinstance(result, float), (
        f"expected numeric, got {type(result)}"
    )


# ---------------------------------------------------------------------------
# ConfigSource conformance: BrainConfig.home -> has latitude/longitude/timezone
# ---------------------------------------------------------------------------


def test_brain_config_satisfies_config_source() -> None:
    """BrainConfig satisfies ConfigSource (has .home property)."""
    from src.config import BrainConfig

    # isinstance check works for @runtime_checkable Protocol with property
    # We just verify BrainConfig has the 'home' attribute
    assert hasattr(BrainConfig, "model_fields"), "BrainConfig is not a pydantic model"
    # Check 'home' is in BrainConfig fields
    fields = BrainConfig.model_fields
    assert "home" in fields, "BrainConfig does not have 'home' field"


def test_home_config_has_required_fields() -> None:
    """HomeConfig has latitude, longitude, timezone fields."""
    from src.config import HomeConfig

    fields = getattr(HomeConfig, "model_fields", None) or getattr(HomeConfig, "__fields__", {})
    assert "latitude" in fields, "HomeConfig missing 'latitude'"
    assert "longitude" in fields, "HomeConfig missing 'longitude'"
    assert "timezone" in fields, "HomeConfig missing 'timezone'"
