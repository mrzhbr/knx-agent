"""Unit tests for topology tools — get_room_info, get_nearby_rooms, get_rooms_by_floor, get_solar_exposure."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import src.tools.topology as topology_module
from src.tools.topology import (
    get_room_info,
    get_nearby_rooms,
    get_rooms_by_floor,
    get_solar_exposure,
    set_context,
)
from src.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helper: build a minimal HouseTopology for testing
# ---------------------------------------------------------------------------


def _make_topology() -> object:
    """Create a real HouseTopology instance for tool testing."""
    from src.core.topology import HouseTopology

    data = {
        "floors": [
            {"key": "eg", "display_name": "Erdgeschoss", "level": 0},
            {"key": "og", "display_name": "Obergeschoss", "level": 1},
        ],
        "rooms": {
            "wohnzimmer": {
                "display_name": "Wohnzimmer",
                "floor": "eg",
                "zone": "living",
                "orientation": "south",
                "window_directions": ["south", "west"],
                "area_m2": 35,
                "adjacent_to": ["kueche"],
                "open_to": ["esszimmer"],
            },
            "esszimmer": {
                "display_name": "Esszimmer",
                "floor": "eg",
                "zone": "living",
                "orientation": "south",
                "window_directions": ["south"],
                "area_m2": 18,
                "adjacent_to": ["kueche"],
                "open_to": ["wohnzimmer"],
            },
            "kueche": {
                "display_name": "Küche",
                "floor": "eg",
                "zone": "utility",
                "orientation": "north",
                "window_directions": ["north"],
                "area_m2": 14,
                "adjacent_to": ["wohnzimmer", "esszimmer"],
            },
            "schlafzimmer": {
                "display_name": "Schlafzimmer",
                "floor": "og",
                "zone": "sleeping",
                "orientation": "north",
                "window_directions": ["north"],
                "area_m2": 20,
                "adjacent_to": [],
            },
        },
        "staircases": [],
    }
    return HouseTopology.from_dict(data)


def _make_ctx(
    topology=None, hal=None, weather=None, latitude=0.0, longitude=0.0, timezone="Europe/Berlin"
) -> ToolContext:
    if hal is None:
        mock_hal = MagicMock()
        mock_hal.get_platform = lambda name: weather if name == "weather" else None
    else:
        # hal provided — add get_platform mock for weather
        if not hasattr(hal, "get_platform") or not callable(getattr(hal, "get_platform", None)):
            hal.get_platform = lambda name: weather if name == "weather" else None
        mock_hal = hal
    return ToolContext(
        hal=mock_hal,
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
        topology=topology,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone,
    )


# ---------------------------------------------------------------------------
# set_context tests
# ---------------------------------------------------------------------------


def test_set_context_stores_topology():
    """set_context() stores topology in _ctx."""
    topology = _make_topology()
    ctx = _make_ctx(topology=topology)
    set_context(ctx)
    assert topology_module._ctx is ctx
    assert topology_module._ctx.topology is topology


def test_set_context_with_none_topology():
    """set_context() accepts ToolContext with None topology without error."""
    ctx = _make_ctx(topology=None)
    set_context(ctx)
    assert topology_module._ctx.topology is None


# ---------------------------------------------------------------------------
# Graceful degradation: all tools return _NOT_CONFIGURED when topology is None
# ---------------------------------------------------------------------------


async def test_get_room_info_no_topology():
    """get_room_info() returns degradation message when topology is None."""
    set_context(_make_ctx(topology=None))
    result = await get_room_info("wohnzimmer")
    assert "nicht konfiguriert" in result.lower() or "Raumtopologie" in result


async def test_get_nearby_rooms_no_topology():
    """get_nearby_rooms() returns degradation message when topology is None."""
    set_context(_make_ctx(topology=None))
    result = await get_nearby_rooms("wohnzimmer")
    assert "nicht konfiguriert" in result.lower() or "Raumtopologie" in result


async def test_get_rooms_by_floor_no_topology():
    """get_rooms_by_floor() returns degradation message when topology is None."""
    set_context(_make_ctx(topology=None))
    result = await get_rooms_by_floor("eg")
    assert "nicht konfiguriert" in result.lower() or "Raumtopologie" in result


async def test_get_solar_exposure_no_topology():
    """get_solar_exposure() returns degradation message when topology is None."""
    set_context(_make_ctx(topology=None))
    result = await get_solar_exposure("wohnzimmer")
    assert "nicht konfiguriert" in result.lower() or "Raumtopologie" in result


# ---------------------------------------------------------------------------
# get_room_info tests
# ---------------------------------------------------------------------------


async def test_get_room_info_valid_room():
    """get_room_info() returns German spatial properties for a valid room."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology))

    result = await get_room_info("wohnzimmer")

    assert "Wohnzimmer" in result
    assert isinstance(result, str)
    assert len(result) > 10


async def test_get_room_info_includes_adjacent():
    """get_room_info() includes adjacent rooms in the description."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology))

    result = await get_room_info("wohnzimmer")
    assert "kueche" in result.lower() or "Küche" in result or "Angrenzend" in result


async def test_get_room_info_unknown_room():
    """get_room_info() returns helpful message for unknown room."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology))

    result = await get_room_info("nonexistent_room")
    assert "nicht" in result.lower() or "nonexistent_room" in result


# ---------------------------------------------------------------------------
# get_nearby_rooms tests
# ---------------------------------------------------------------------------


async def test_get_nearby_rooms_without_state():
    """get_nearby_rooms() lists adjacent and open-to rooms without state."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology, hal=None))

    result = await get_nearby_rooms("wohnzimmer")

    assert isinstance(result, str)
    assert "kueche" in result.lower() or "Küche" in result or "Angrenzend" in result


async def test_get_nearby_rooms_with_house_state():
    """get_nearby_rooms() includes device state when hal is provided."""
    topology = _make_topology()
    mock_hal = MagicMock()
    mock_hal.get_room_state_formatted = AsyncMock(return_value={"Deckenlampe": {"OnOff": "1"}})

    set_context(_make_ctx(topology=topology, hal=mock_hal))

    result = await get_nearby_rooms("wohnzimmer")

    assert isinstance(result, str)
    assert len(result) > 0


async def test_get_nearby_rooms_unknown_room():
    """get_nearby_rooms() returns helpful message for unknown room."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology, hal=None))

    result = await get_nearby_rooms("nonexistent")
    assert "nicht" in result.lower() or "nonexistent" in result or "keine" in result.lower()


# ---------------------------------------------------------------------------
# get_rooms_by_floor tests
# ---------------------------------------------------------------------------


async def test_get_rooms_by_floor_valid_floor():
    """get_rooms_by_floor() lists rooms on the given floor."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology))

    result = await get_rooms_by_floor("eg")

    assert "Wohnzimmer" in result or "wohnzimmer" in result
    assert "eg" in result.lower() or "Etage" in result


async def test_get_rooms_by_floor_includes_zone():
    """get_rooms_by_floor() includes zone information."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology))

    result = await get_rooms_by_floor("eg")

    assert "Zone" in result or "zone" in result.lower() or "living" in result


async def test_get_rooms_by_floor_og():
    """get_rooms_by_floor() lists Obergeschoss rooms correctly."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology))

    result = await get_rooms_by_floor("og")

    assert "Schlafzimmer" in result


async def test_get_rooms_by_floor_unknown_floor():
    """get_rooms_by_floor() returns helpful message for unknown floor."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology))

    result = await get_rooms_by_floor("keller")
    assert "nicht" in result.lower() or "keller" in result or "keine" in result.lower()


# ---------------------------------------------------------------------------
# get_solar_exposure tests
# ---------------------------------------------------------------------------


async def test_get_solar_exposure_no_weather():
    """get_solar_exposure() returns helpful message when weather is None."""
    topology = _make_topology()
    set_context(_make_ctx(topology=topology, weather=None))

    result = await get_solar_exposure("wohnzimmer")
    assert "nicht verfügbar" in result.lower() or "wetter" in result.lower()


async def test_get_solar_exposure_with_weather():
    """get_solar_exposure() returns German solar assessment."""
    topology = _make_topology()
    mock_weather = AsyncMock()
    mock_weather.get_current = AsyncMock(
        return_value={
            "is_day": False,
            "solar_irradiance_w_m2": 0.0,
            "cloud_cover": 100,
        }
    )

    set_context(
        _make_ctx(
            topology=topology,
            weather=mock_weather,
            latitude=48.73,
            longitude=9.23,
            timezone="Europe/Berlin",
        )
    )

    result = await get_solar_exposure("wohnzimmer")

    assert isinstance(result, str)
    assert len(result) > 5
    assert "keine" in result.lower() or "W/m" in result


async def test_get_solar_exposure_high_irradiance():
    """get_solar_exposure() handles high irradiance scenario."""
    topology = _make_topology()
    mock_weather = AsyncMock()
    mock_weather.get_current = AsyncMock(
        return_value={
            "is_day": True,
            "solar_irradiance_w_m2": 700.0,
            "cloud_cover": 5,
        }
    )

    set_context(
        _make_ctx(
            topology=topology,
            weather=mock_weather,
            latitude=48.73,
            longitude=9.23,
            timezone="Europe/Berlin",
        )
    )

    result = await get_solar_exposure("wohnzimmer")

    assert isinstance(result, str)
    assert len(result) > 5
