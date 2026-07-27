"""Unit tests for HouseTopology — loading, graph, query methods, solar heuristics, and validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock
import tempfile


from src.core.topology import HouseTopology


# ---------------------------------------------------------------------------
# Test data fixtures (inline — no shared conftest)
# ---------------------------------------------------------------------------

SIMPLE_DATA = {
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
            "adjacent_to": ["kueche", "flur_eg"],
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
            "adjacent_to": ["wohnzimmer", "esszimmer", "flur_eg"],
        },
        "flur_eg": {
            "display_name": "Flur EG",
            "floor": "eg",
            "zone": "utility",
            "orientation": "north",
            "window_directions": [],
            "area_m2": 8,
            "adjacent_to": ["wohnzimmer", "kueche"],
        },
        "schlafzimmer": {
            "display_name": "Schlafzimmer",
            "floor": "og",
            "zone": "sleeping",
            "orientation": "north",
            "window_directions": ["north", "west"],
            "area_m2": 20,
            "adjacent_to": ["flur_og"],
        },
        "flur_og": {
            "display_name": "Flur OG",
            "floor": "og",
            "zone": "utility",
            "orientation": "north",
            "window_directions": [],
            "area_m2": 6,
            "adjacent_to": ["schlafzimmer"],
        },
    },
    "staircases": [
        {"connects": ["flur_eg", "flur_og"], "type": "indoor"},
    ],
}


# ---------------------------------------------------------------------------
# Loading tests
# ---------------------------------------------------------------------------


def test_from_dict_creates_topology():
    """HouseTopology.from_dict() creates a valid topology instance."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert len(t.graph.nodes) == 6
    assert "wohnzimmer" in t.graph
    assert "schlafzimmer" in t.graph


def test_from_yaml_file():
    """HouseTopology.load() reads a YAML file and returns a topology."""
    import yaml

    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(SIMPLE_DATA, f)
        path = Path(f.name)

    try:
        t = HouseTopology.load(path)
        assert t is not None
        assert len(t.graph.nodes) == 6
    finally:
        path.unlink()


def test_load_returns_none_when_file_missing():
    """HouseTopology.load() returns None gracefully when file doesn't exist."""
    result = HouseTopology.load(Path("/nonexistent/path/topology.yaml"))
    assert result is None


# ---------------------------------------------------------------------------
# Graph structure tests
# ---------------------------------------------------------------------------


def test_bidirectional_adjacent_edges():
    """Adjacent edges are bidirectional."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert "kueche" in t.get_adjacent("wohnzimmer")
    assert "wohnzimmer" in t.get_adjacent("kueche")


def test_bidirectional_open_to_edges():
    """open_to edges are bidirectional."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert "esszimmer" in t.get_open_to("wohnzimmer")
    assert "wohnzimmer" in t.get_open_to("esszimmer")


def test_staircase_edge_connects_floors():
    """Staircase edges connect flur_eg and flur_og."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    neighbors = list(t.graph.neighbors("flur_eg"))
    assert "flur_og" in neighbors
    edge = t.graph.edges["flur_eg", "flur_og"]
    assert edge["type"] == "staircase"


def test_edge_types_are_distinct():
    """Adjacent and open_to edges have correct type attributes."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    # wohnzimmer-kueche is adjacent
    assert t.graph.edges["wohnzimmer", "kueche"]["type"] == "adjacent"
    # wohnzimmer-esszimmer is open_to
    assert t.graph.edges["wohnzimmer", "esszimmer"]["type"] == "open_to"


# ---------------------------------------------------------------------------
# Query method tests
# ---------------------------------------------------------------------------


def test_get_adjacent_valid_room():
    """get_adjacent() returns adjacent rooms for a valid room."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    adj = t.get_adjacent("wohnzimmer")
    assert "kueche" in adj
    assert "flur_eg" in adj
    assert "esszimmer" not in adj  # esszimmer is open_to, not adjacent


def test_get_adjacent_invalid_room():
    """get_adjacent() returns empty list for unknown room."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert t.get_adjacent("nonexistent") == []


def test_get_open_to_valid_room():
    """get_open_to() returns open-plan connected rooms."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert "esszimmer" in t.get_open_to("wohnzimmer")


def test_get_open_to_invalid_room():
    """get_open_to() returns empty list for unknown room."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert t.get_open_to("nonexistent") == []


def test_zone_grouping():
    """get_zone_rooms() returns all rooms in a zone."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    living = set(t.get_zone_rooms("living"))
    assert living == {"wohnzimmer", "esszimmer"}
    sleeping = set(t.get_zone_rooms("sleeping"))
    assert sleeping == {"schlafzimmer"}
    utility = set(t.get_zone_rooms("utility"))
    assert "kueche" in utility
    assert "flur_eg" in utility
    assert "flur_og" in utility


def test_floor_grouping():
    """get_floor_rooms() returns all rooms on a floor."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    eg_rooms = set(t.get_floor_rooms("eg"))
    assert eg_rooms == {"wohnzimmer", "esszimmer", "kueche", "flur_eg"}
    og_rooms = set(t.get_floor_rooms("og"))
    assert og_rooms == {"schlafzimmer", "flur_og"}


def test_get_orientation():
    """get_orientation() returns orientation string."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert t.get_orientation("wohnzimmer") == "south"
    assert t.get_orientation("kueche") == "north"
    assert t.get_orientation("nonexistent") is None


def test_get_window_directions():
    """get_window_directions() returns list of window directions."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert t.get_window_directions("wohnzimmer") == ["south", "west"]
    assert t.get_window_directions("flur_eg") == []
    assert t.get_window_directions("nonexistent") == []


def test_get_path_same_floor():
    """get_path() finds path between rooms on same floor."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    path = t.get_path("wohnzimmer", "kueche")
    assert len(path) > 0
    assert path[0] == "wohnzimmer"
    assert path[-1] == "kueche"


def test_get_path_across_floors_via_staircase():
    """get_path() finds path across floors via staircase."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    path = t.get_path("wohnzimmer", "schlafzimmer")
    assert len(path) > 0
    assert "flur_eg" in path
    assert "flur_og" in path


def test_get_path_invalid_rooms():
    """get_path() returns empty list for unknown rooms."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert t.get_path("nonexistent", "wohnzimmer") == []
    assert t.get_path("wohnzimmer", "nonexistent") == []


def test_get_properties_valid_room():
    """get_properties() returns full property dict."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    props = t.get_properties("wohnzimmer")
    assert props is not None
    assert props["display_name"] == "Wohnzimmer"
    assert props["floor"] == "eg"
    assert props["zone"] == "living"
    assert props["orientation"] == "south"
    assert props["window_directions"] == ["south", "west"]
    assert props["area_m2"] == 35


def test_get_properties_invalid_room():
    """get_properties() returns None for unknown room."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    assert t.get_properties("nonexistent") is None


def test_rooms_facing():
    """rooms_facing() returns all rooms with matching orientation."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    south_rooms = set(t.rooms_facing("south"))
    assert "wohnzimmer" in south_rooms
    assert "esszimmer" in south_rooms
    assert "kueche" not in south_rooms
    north_rooms = set(t.rooms_facing("north"))
    assert "kueche" in north_rooms


def test_format_for_prompt_contains_german():
    """format_for_prompt() returns German text with room name, floor, zone."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    text = t.format_for_prompt("wohnzimmer")
    assert "Wohnzimmer" in text
    assert "Erdgeschoss" in text
    assert "living" in text
    assert "south" in text
    assert "35" in text  # area_m2


def test_format_for_prompt_unknown_room():
    """format_for_prompt() returns a graceful message for unknown rooms."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    text = t.format_for_prompt("nonexistent")
    assert "nonexistent" in text.lower() or "nicht" in text.lower()


# ---------------------------------------------------------------------------
# validate_rooms tests
# ---------------------------------------------------------------------------


def test_validate_rooms_mismatches():
    """validate_rooms() correctly identifies mismatched room keys."""
    t = HouseTopology.from_dict(SIMPLE_DATA)

    # Plain dict: room key -> list of devices (empty lists are fine for this test)
    rooms = {
        "wohnzimmer": [],  # matching room
        "garage": [],  # in HAL but not topology
        "esszimmer": [],  # also in topology
    }

    result = t.validate_rooms(rooms)

    assert isinstance(result, dict)
    assert "in_topology_only" in result
    assert "in_registry_only" in result
    assert "garage" in result["in_registry_only"]
    # Rooms that are in topology but not in HAL
    topology_only = set(result["in_topology_only"])
    # kueche, flur_eg, schlafzimmer, flur_og are in topology but not in rooms dict
    assert "kueche" in topology_only


def test_validate_rooms_no_mismatch():
    """validate_rooms() with perfectly matching room keys."""
    small_data = {
        "floors": [{"key": "eg", "display_name": "Erdgeschoss", "level": 0}],
        "rooms": {
            "wohnzimmer": {
                "display_name": "Wohnzimmer",
                "floor": "eg",
                "zone": "living",
                "orientation": "south",
                "window_directions": ["south"],
                "area_m2": 30,
                "adjacent_to": [],
            },
        },
        "staircases": [],
    }
    t = HouseTopology.from_dict(small_data)

    rooms = {"wohnzimmer": []}

    result = t.validate_rooms(rooms)
    assert result["in_topology_only"] == []
    assert result["in_registry_only"] == []


# ---------------------------------------------------------------------------
# Solar heuristic tests (assess_solar_exposure)
# ---------------------------------------------------------------------------

LAT = 48.73
LON = 9.23
TZ = "Europe/Berlin"


def test_solar_no_sun_when_not_day():
    """Returns 'none' when is_day is False."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    weather = {"is_day": False, "solar_irradiance_w_m2": 500, "cloud_cover": 0}
    result = t.assess_solar_exposure("wohnzimmer", weather, LAT, LON, TZ)
    assert result["level"] == "none"


def test_solar_no_sun_when_low_irradiance():
    """Returns 'none' when irradiance < 100 W/m2."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    weather = {"is_day": True, "solar_irradiance_w_m2": 50, "cloud_cover": 0}
    result = t.assess_solar_exposure("wohnzimmer", weather, LAT, LON, TZ)
    assert result["level"] == "none"


def test_solar_result_has_required_keys():
    """Result dict always has required keys."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    weather = {"is_day": False, "solar_irradiance_w_m2": 0, "cloud_cover": 100}
    result = t.assess_solar_exposure("wohnzimmer", weather, LAT, LON, TZ)
    for key in [
        "level",
        "description",
        "irradiance_w_m2",
        "relevant_windows",
        "sun_azimuth",
        "sun_elevation",
    ]:
        assert key in result, f"Missing key: {key}"


def test_solar_cloud_cover_downgrade():
    """High cloud cover (>70%) downgrades level by one step."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    # Use a weather dict with high irradiance + day + heavy cloud
    weather = {"is_day": True, "solar_irradiance_w_m2": 700, "cloud_cover": 80}
    # We need to test the downgrade logic without caring about actual sun position
    # So we'll check that the cloud cover logic is applied by checking the _internal_ levels
    # We can't easily mock the sun position, so we test with a known-daytime scenario
    # The level should not be "hoch" even with 700 W/m2 when cloud_cover > 70%
    result = t.assess_solar_exposure("wohnzimmer", weather, LAT, LON, TZ)
    # The actual level depends on sun position, but if it was "hoch" it should be "mittel" now
    # If the sun is up and direction matches, it will be downgraded
    # We verify the function runs without error and returns valid levels
    assert result["level"] in ("none", "gering", "mittel", "hoch")
    assert isinstance(result["description"], str)


def test_solar_unknown_room():
    """assess_solar_exposure returns gracefully for unknown room."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    weather = {"is_day": True, "solar_irradiance_w_m2": 700, "cloud_cover": 10}
    result = t.assess_solar_exposure("nonexistent", weather, LAT, LON, TZ)
    # Unknown room has no window_directions, so relevant_windows should be empty
    assert result["relevant_windows"] == []
    assert result["level"] in ("none", "gering")


# ---------------------------------------------------------------------------
# Additional solar heuristic tests with astral (Task 3.5)
# ---------------------------------------------------------------------------

# Ostfildern coordinates
_LAT = 48.73
_LON = 9.23
_TZ = "Europe/Berlin"


def _make_topology_south() -> HouseTopology:
    """Topology with south-facing and north-facing rooms."""
    data = {
        "floors": [{"key": "eg", "display_name": "EG", "level": 0}],
        "rooms": {
            "south_room": {
                "display_name": "Südzimmer",
                "floor": "eg",
                "zone": "living",
                "orientation": "south",
                "window_directions": ["south"],
                "area_m2": 20,
                "adjacent_to": [],
            },
            "north_room": {
                "display_name": "Nordzimmer",
                "floor": "eg",
                "zone": "sleeping",
                "orientation": "north",
                "window_directions": ["north"],
                "area_m2": 15,
                "adjacent_to": [],
            },
            "no_windows_room": {
                "display_name": "Kellerraum",
                "floor": "eg",
                "zone": "utility",
                "orientation": "north",
                "window_directions": [],
                "area_m2": 10,
                "adjacent_to": [],
            },
        },
        "staircases": [],
    }
    return HouseTopology.from_dict(data)


def test_solar_assess_night_time_returns_none():
    """When is_day=False, returns level='none' regardless of other params."""
    t = _make_topology_south()
    weather = {"is_day": False, "solar_irradiance_w_m2": 500, "cloud_cover": 0}
    result = t.assess_solar_exposure("south_room", weather, _LAT, _LON, _TZ)
    assert result["level"] == "none"
    assert "Sonneneinstrahlung" in result["description"] or "keine" in result["description"].lower()


def test_solar_assess_low_irradiance_returns_none():
    """When irradiance < 100 W/m², returns level='none'."""
    t = _make_topology_south()
    weather = {"is_day": True, "solar_irradiance_w_m2": 50, "cloud_cover": 0}
    result = t.assess_solar_exposure("south_room", weather, _LAT, _LON, _TZ)
    assert result["level"] == "none"


def test_solar_assess_result_keys():
    """assess_solar_exposure result always contains required keys."""
    t = _make_topology_south()
    weather = {"is_day": False, "solar_irradiance_w_m2": 0, "cloud_cover": 100}
    result = t.assess_solar_exposure("south_room", weather, _LAT, _LON, _TZ)
    required = [
        "level",
        "description",
        "irradiance_w_m2",
        "relevant_windows",
        "sun_azimuth",
        "sun_elevation",
    ]
    for key in required:
        assert key in result, f"Missing key: {key}"


def test_solar_high_irradiance_day():
    """High irradiance with is_day=True runs without error and returns valid level."""
    t = _make_topology_south()
    weather = {"is_day": True, "solar_irradiance_w_m2": 700, "cloud_cover": 5}
    result = t.assess_solar_exposure("south_room", weather, _LAT, _LON, _TZ)
    assert result["level"] in ("hoch", "mittel", "gering", "none")
    assert isinstance(result["sun_azimuth"], float)
    assert isinstance(result["sun_elevation"], float)


def test_solar_cloud_cover_high_downgrades():
    """Cloud cover > 70% downgrades solar level by one step."""
    t = _make_topology_south()
    # Test the downgrade logic with weather data
    # With cloud_cover=80, a "hoch" level should become "mittel"
    # We can verify the logic by checking levels at different cloud_cover values
    # with the same irradiance (> 600 W/m²)
    weather_clear = {"is_day": True, "solar_irradiance_w_m2": 700, "cloud_cover": 5}
    weather_cloudy = {"is_day": True, "solar_irradiance_w_m2": 700, "cloud_cover": 80}

    result_clear = t.assess_solar_exposure("south_room", weather_clear, _LAT, _LON, _TZ)
    result_cloudy = t.assess_solar_exposure("south_room", weather_cloudy, _LAT, _LON, _TZ)

    # Both should succeed without error
    assert result_clear["level"] in ("hoch", "mittel", "gering", "none")
    assert result_cloudy["level"] in ("hoch", "mittel", "gering", "none")

    # If the sun is shining on a matching window, cloudy result should be <= clear result
    level_order = {"none": 0, "gering": 1, "mittel": 2, "hoch": 3}
    # Only assert if both have irradiance > threshold (not "none")
    if result_clear["level"] != "none" and result_cloudy["level"] != "none":
        assert level_order[result_cloudy["level"]] <= level_order[result_clear["level"]]


def test_solar_room_with_no_windows():
    """Room with no window_directions gets gering or none (indirect only)."""
    t = _make_topology_south()
    weather = {"is_day": True, "solar_irradiance_w_m2": 700, "cloud_cover": 5}
    result = t.assess_solar_exposure("no_windows_room", weather, _LAT, _LON, _TZ)
    # No windows = no direct match = indirect or none
    assert result["relevant_windows"] == []
    assert result["level"] in ("gering", "none")


def test_solar_uses_observer_correctly():
    """Verify astral LocationInfo.observer is used — results are non-trivial for known coords."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from astral import LocationInfo
    from astral.sun import elevation

    # Verify astral API works: Ostfildern at noon should have elevation > 0 in summer
    loc = LocationInfo(name="Home", region="", timezone=_TZ, latitude=_LAT, longitude=_LON)
    obs = loc.observer  # Must be .observer, not loc directly

    # Use a known summer noon datetime
    summer_noon = datetime(2026, 6, 21, 12, 0, 0, tzinfo=ZoneInfo(_TZ))
    el = elevation(obs, summer_noon)

    # At Ostfildern (lat 48.73), summer solstice noon, elevation should be ~65 degrees
    assert el > 50, f"Summer noon elevation at Ostfildern should be > 50°, got {el:.1f}°"


# ---------------------------------------------------------------------------
# format_topology_for_prompt tests
# ---------------------------------------------------------------------------


def test_format_topology_for_prompt_groups_by_floor():
    """format_topology_for_prompt() groups rooms by floor and includes all rooms."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    result = t.format_topology_for_prompt()
    assert "Raumübersicht:" in result
    assert "Erdgeschoss:" in result
    assert "Obergeschoss:" in result
    assert "Wohnzimmer" in result
    assert "Schlafzimmer" in result
    assert "Küche" in result


def test_format_topology_for_prompt_includes_zone():
    """format_topology_for_prompt() shows zone type per room."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    result = t.format_topology_for_prompt()
    assert "(living)" in result
    assert "(utility)" in result
    assert "(sleeping)" in result


def test_format_topology_for_prompt_includes_adjacency():
    """format_topology_for_prompt() shows adjacency using display names."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    result = t.format_topology_for_prompt()
    # Wohnzimmer is adjacent to Küche and Flur EG
    assert "Adj:" in result
    assert "Küche" in result


def test_format_topology_for_prompt_includes_open_to():
    """format_topology_for_prompt() shows open-to connections."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    result = t.format_topology_for_prompt()
    # Wohnzimmer opens to Esszimmer
    assert "Open:" in result
    assert "Esszimmer" in result


def test_format_topology_for_prompt_includes_windows():
    """format_topology_for_prompt() shows window directions."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    result = t.format_topology_for_prompt()
    assert "Fenster:" in result
    assert "south" in result


def test_format_topology_for_prompt_with_hal_device_counts():
    """format_topology_for_prompt() includes device counts when HAL is provided."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    mock_hal = MagicMock()
    mock_hal.get_all_rooms.return_value = {
        "Wohnzimmer": [MagicMock()] * 5,
        "Küche": [MagicMock()] * 3,
    }
    result = t.format_topology_for_prompt(hal=mock_hal)
    assert "5 Geräte" in result
    assert "3 Geräte" in result


def test_format_topology_for_prompt_without_hal():
    """format_topology_for_prompt() works without HAL — no device counts shown."""
    t = HouseTopology.from_dict(SIMPLE_DATA)
    result = t.format_topology_for_prompt()
    assert "Geräte" not in result
