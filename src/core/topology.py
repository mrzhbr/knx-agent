"""House topology — static spatial knowledge layer.

Loads house_topology.yaml and provides a networkx graph with query methods
for room adjacency, zones, floors, paths, and solar exposure assessment.

This is core infrastructure (no start/stop lifecycle) — consumers receive
HouseTopology | None; all methods degrade gracefully.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.hal.models import DeviceInfo
from zoneinfo import ZoneInfo

import networkx as nx
import yaml

logger = logging.getLogger(__name__)


class HouseTopology:
    """Spatial knowledge graph built from house_topology.yaml.

    Graph model: undirected networkx Graph.
    - Nodes: room keys, with attributes (display_name, floor, zone, orientation,
      window_directions, area_m2)
    - Edges: with attribute ``type`` = "adjacent" | "open_to" | "staircase"

    Usage::

        topology = HouseTopology.load(Path("house_topology.yaml"))
        if topology:
            adj = topology.get_adjacent("wohnzimmer")
    """

    def __init__(self, rooms: dict[str, Any], floors: list[dict], staircases: list[dict]) -> None:
        self.graph: nx.Graph = nx.Graph()
        self._floors: dict[str, dict] = {f["key"]: f for f in floors}
        self._build_graph(rooms, staircases)

    def _build_graph(self, rooms: dict[str, Any], staircases: list[dict]) -> None:
        """Build the networkx graph from parsed YAML data."""
        # Add nodes
        for room_key, props in rooms.items():
            self.graph.add_node(
                room_key,
                display_name=props.get("display_name", room_key),
                floor=props.get("floor", ""),
                zone=props.get("zone", ""),
                orientation=props.get("orientation", ""),
                window_directions=props.get("window_directions", []),
                area_m2=props.get("area_m2", 0),
            )

        # Add edges from room relationships
        for room_key, props in rooms.items():
            for adj in props.get("adjacent_to", []):
                if adj in rooms:
                    self.graph.add_edge(room_key, adj, type="adjacent")
            for open_room in props.get("open_to", []):
                if open_room in rooms:
                    self.graph.add_edge(room_key, open_room, type="open_to")

        # Add staircase edges
        for staircase in staircases:
            connects = staircase.get("connects", [])
            if len(connects) == 2 and connects[0] in self.graph and connects[1] in self.graph:
                self.graph.add_edge(connects[0], connects[1], type="staircase")

    @classmethod
    def load(cls, path: Path) -> HouseTopology | None:
        """Load topology from an explicit YAML file path.

        Returns None if file not found (graceful degradation). Does NOT
        validate against HAL rooms — call validate_rooms()
        separately after loading.

        Args:
            path: Absolute path to house_topology.yaml
        """
        if not path.exists():
            logger.info("house_topology.yaml not found at %s — spatial reasoning disabled", path)
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            topology = cls(
                rooms=data.get("rooms", {}),
                floors=data.get("floors", []),
                staircases=data.get("staircases", []),
            )
            logger.info(
                "HouseTopology loaded: %d rooms, %d edges",
                len(topology.graph.nodes),
                len(topology.graph.edges),
            )
            return topology
        except Exception as e:
            logger.warning("Failed to load house_topology.yaml: %s", e)
            return None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HouseTopology:
        """Build from a dict (for testing).

        Args:
            data: Dict with keys 'rooms', 'floors', 'staircases'
        """
        return cls(
            rooms=data.get("rooms", {}),
            floors=data.get("floors", []),
            staircases=data.get("staircases", []),
        )

    # -------------------------------------------------------------------------
    # Query methods — all return empty list / None for unknown rooms, never raise
    # -------------------------------------------------------------------------

    def get_adjacent(self, room: str) -> list[str]:
        """Return rooms connected by 'adjacent' edges."""
        if room not in self.graph:
            return []
        return [
            neighbor
            for neighbor in self.graph.neighbors(room)
            if self.graph.edges[room, neighbor].get("type") == "adjacent"
        ]

    def get_open_to(self, room: str) -> list[str]:
        """Return rooms connected by 'open_to' edges (open-plan connections)."""
        if room not in self.graph:
            return []
        return [
            neighbor
            for neighbor in self.graph.neighbors(room)
            if self.graph.edges[room, neighbor].get("type") == "open_to"
        ]

    def get_zone_rooms(self, zone: str) -> list[str]:
        """Return all rooms sharing the given zone type."""
        return [node for node, attrs in self.graph.nodes(data=True) if attrs.get("zone") == zone]

    def get_floor_rooms(self, floor: str) -> list[str]:
        """Return all rooms on a given floor key."""
        return [node for node, attrs in self.graph.nodes(data=True) if attrs.get("floor") == floor]

    def get_orientation(self, room: str) -> str | None:
        """Return the room's orientation string, or None if unknown."""
        if room not in self.graph:
            return None
        return self.graph.nodes[room].get("orientation") or None

    def get_window_directions(self, room: str) -> list[str]:
        """Return the list of window directions for a room."""
        if room not in self.graph:
            return []
        return list(self.graph.nodes[room].get("window_directions", []))

    def get_path(self, from_room: str, to_room: str) -> list[str]:
        """Return shortest path from from_room to to_room via networkx.

        Returns empty list if no path exists or rooms are unknown.
        """
        if from_room not in self.graph or to_room not in self.graph:
            return []
        try:
            return list(nx.shortest_path(self.graph, from_room, to_room))
        except nx.NetworkXNoPath:
            return []

    def get_properties(self, room: str) -> dict | None:
        """Return full property dict for a room, or None if unknown."""
        if room not in self.graph:
            return None
        return dict(self.graph.nodes[room])

    def rooms_facing(self, direction: str) -> list[str]:
        """Return all rooms whose orientation matches direction."""
        return [
            node
            for node, attrs in self.graph.nodes(data=True)
            if attrs.get("orientation") == direction
        ]

    def format_for_prompt(self, room: str) -> str:
        """Format spatial context for a room as a German string for LLM consumption."""
        props = self.get_properties(room)
        if props is None:
            return f"Kein Raumwissen für '{room}' verfügbar."

        display = props.get("display_name", room)
        floor_key = props.get("floor", "")
        floor_info = self._floors.get(floor_key, {})
        floor_name = floor_info.get("display_name", floor_key)
        zone = props.get("zone", "")
        orientation = props.get("orientation", "")
        window_dirs = props.get("window_directions", [])
        area = props.get("area_m2", 0)

        adjacent = self.get_adjacent(room)
        open_to = self.get_open_to(room)

        parts = [f"{display} ({floor_name}, Zone: {zone})"]
        if orientation:
            parts.append(f"Ausrichtung: {orientation}")
        if window_dirs:
            parts.append(f"Fenster: {', '.join(window_dirs)}")
        if area:
            parts.append(f"Fläche: {area} m²")
        if adjacent:
            parts.append(f"Angrenzend: {', '.join(adjacent)}")
        if open_to:
            parts.append(f"Offen verbunden mit: {', '.join(open_to)}")

        return "; ".join(parts)

    def format_topology_for_prompt(self, hal: Any = None) -> str:
        """Format full house topology as compact German overview for LLM system prompt.

        Groups rooms by floor. Per room: zone, window directions, adjacency,
        open-to connections, device count (if HAL provided).

        Args:
            hal: Optional HAL instance for device counts per room.
        """
        # Get device counts per room from HAL
        room_device_counts: dict[str, int] = {}
        if hal is not None:
            try:
                all_rooms = hal.get_all_rooms()
                room_device_counts = {name: len(devs) for name, devs in all_rooms.items()}
            except Exception:
                pass

        # Group rooms by floor
        floor_order = sorted(self._floors.values(), key=lambda f: f.get("level", 0))
        lines: list[str] = ["Raumübersicht:"]

        for floor in floor_order:
            floor_key = floor["key"]
            floor_name = floor.get("display_name", floor_key)
            floor_rooms = self.get_floor_rooms(floor_key)
            if not floor_rooms:
                continue

            lines.append(f"\n{floor_name}:")
            for room_key in sorted(floor_rooms):
                props = self.graph.nodes[room_key]
                display = props.get("display_name", room_key)
                zone = props.get("zone", "")
                win_dirs = props.get("window_directions", [])
                adjacent = self.get_adjacent(room_key)
                open_to = self.get_open_to(room_key)

                # Device count — match by display name
                count = room_device_counts.get(display, 0)

                parts = [f"  {display} ({zone})"]
                if win_dirs:
                    parts.append(f"Fenster: {'/'.join(win_dirs)}")
                if count:
                    parts.append(f"{count} Geräte")
                if adjacent:
                    adj_names = [self.graph.nodes[r].get("display_name", r) for r in adjacent]
                    parts.append(f"Adj: {', '.join(adj_names)}")
                if open_to:
                    open_names = [self.graph.nodes[r].get("display_name", r) for r in open_to]
                    parts.append(f"Open: {', '.join(open_names)}")

                lines.append("; ".join(parts))

        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Solar heuristic
    # -------------------------------------------------------------------------

    def assess_solar_exposure(
        self,
        room: str,
        weather: dict,
        latitude: float,
        longitude: float,
        timezone: str,
    ) -> dict:
        """Assess solar exposure for a room given current weather and home coordinates.

        Uses astral library for accurate sun position (azimuth + elevation).
        Maps sun azimuth to compass direction, checks against room window_directions.

        Args:
            room: Room key (e.g. "wohnzimmer")
            weather: Dict with weather data (must contain
                     is_day, solar_irradiance_w_m2, cloud_cover
            latitude: Home latitude (e.g. 48.73)
            longitude: Home longitude (e.g. 9.23)
            timezone: IANA timezone string (e.g. "Europe/Berlin")

        Returns:
            Dict with keys: level, description, irradiance_w_m2, relevant_windows,
            sun_azimuth, sun_elevation
        """
        from astral import LocationInfo
        from astral.sun import azimuth, elevation

        is_day = weather.get("is_day", False)
        irradiance = weather.get("solar_irradiance_w_m2", 0.0)
        cloud_cover = weather.get("cloud_cover", 100)

        no_sun = {
            "level": "none",
            "description": "Keine Sonneneinstrahlung",
            "irradiance_w_m2": irradiance,
            "relevant_windows": [],
            "sun_azimuth": 0,
            "sun_elevation": 0,
        }

        if not is_day or irradiance < 100:
            return no_sun

        # Compute actual sun position using astral
        try:
            loc = LocationInfo(
                name="Home",
                region="",
                timezone=timezone,
                latitude=latitude,
                longitude=longitude,
            )
            obs = loc.observer  # Observer object — pass this to azimuth/elevation
            dt = datetime.now(ZoneInfo(timezone))  # timezone-aware datetime
            az = azimuth(obs, dt)
            el = elevation(obs, dt)
        except Exception as e:
            logger.warning("Solar position calculation failed: %s", e)
            return no_sun

        if el <= 0:
            return {**no_sun, "sun_azimuth": round(az, 1), "sun_elevation": round(el, 1)}

        # Map azimuth to compass direction (8 sectors of 45 degrees each)
        sectors = [
            (22.5, "north"),
            (67.5, "north-east"),
            (112.5, "east"),
            (157.5, "south-east"),
            (202.5, "south"),
            (247.5, "south-west"),
            (292.5, "west"),
            (337.5, "north-west"),
        ]
        sun_direction = "north"  # default for az near 360/0
        for threshold, direction in sectors:
            if az < threshold:
                sun_direction = direction
                break
        else:
            sun_direction = "north"  # wraps past 337.5

        window_dirs = self.get_window_directions(room)

        # Determine base irradiance level
        if irradiance > 600:
            level = "hoch"
        elif irradiance > 300:
            level = "mittel"
        else:
            level = "gering"

        # Downgrade one level if cloud cover > 70%
        if cloud_cover > 70:
            if level == "hoch":
                level = "mittel"
            elif level == "mittel":
                level = "gering"
            # gering stays gering

        # Check if sun direction matches any window direction
        relevant_windows = [d for d in window_dirs if d == sun_direction]

        if relevant_windows:
            level_text = {"hoch": "Starke", "mittel": "Mäßige", "gering": "Geringe"}[level]
            description = f"{level_text} Sonneneinstrahlung durch {sun_direction}-Fenster"
        else:
            # No direct window match — indirect/ambient only
            if level in ("hoch", "mittel"):
                level = "gering"
            description = "Indirekte Sonneneinstrahlung (kein passendes Fenster)"

        return {
            "level": level,
            "description": description,
            "irradiance_w_m2": irradiance,
            "relevant_windows": relevant_windows,
            "sun_azimuth": round(az, 1),
            "sun_elevation": round(el, 1),
        }

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def validate_rooms(self, rooms: dict[str, list[DeviceInfo]]) -> dict[str, list[str]]:
        """Cross-reference topology room keys against HAL room keys.

        Args:
            rooms: dict[str, list[DeviceInfo]] as returned by hal.get_all_rooms()

        Returns:
            Dict {"in_topology_only": [...], "in_registry_only": [...]}
        """
        topology_keys = set(self.graph.nodes)
        registry_keys = set(rooms.keys())

        in_topology_only = sorted(topology_keys - registry_keys)
        in_registry_only = sorted(registry_keys - topology_keys)

        if in_topology_only:
            logger.warning("Topology rooms not in HAL (typo or stale): %s", in_topology_only)
        if in_registry_only:
            logger.info("HAL rooms without topology data (acceptable): %s", in_registry_only)

        return {
            "in_topology_only": in_topology_only,
            "in_registry_only": in_registry_only,
        }
