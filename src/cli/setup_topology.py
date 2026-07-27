"""CLI command: generate house_topology.yaml from Gira X1 API.

Pulls the uiconfig from the live X1 API, extracts the location hierarchy
(Building → Floor → Room → function UIDs), and generates a topology YAML
scaffold with rooms, floors, and device counts. Spatial metadata (adjacency,
zones, orientations) must be filled in manually after generation.

Usage:
    python -m src setup-topology [--output PATH] [--config PATH]
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

import httpx

from src.config import load_config

logger = logging.getLogger(__name__)

# Default zone guesses based on room name keywords
_ZONE_HINTS: dict[str, str] = {
    "schlafzimmer": "sleeping",
    "bad": "utility",
    "wc": "utility",
    "küche": "living",
    "flur": "utility",
    "treppe": "utility",
    "waschraum": "utility",
    "keller": "utility",
    "ankleide": "utility",
    "büro": "utility",
    "schaltschrank": "utility",
    "terrasse": "outdoor",
    "pool": "outdoor",
    "garten": "outdoor",
    "außen": "outdoor",
    "eingang": "utility",
    "kino": "living",
    "wohnzimmer": "living",
    "esszimmer": "living",
    "fitness": "utility",
    "sauna": "living",
    "poker": "living",
    "wein": "utility",
    "gäste": "sleeping",
}

# Location types that should be excluded from topology (not physical rooms)
_EXCLUDED_NAMES: set[str] = {
    "alarmanlage",
    "kameras",
    "zentralfunktionen",
    "schaltschrank",
}


def _make_key(name: str) -> str:
    """Generate room key matching HAL's location parsing logic."""
    return name.lower().replace(" ", "_").replace("/", "_")


def _guess_zone(room_name: str) -> str:
    """Guess zone from room name keywords."""
    name_lower = room_name.lower()
    for keyword, zone in _ZONE_HINTS.items():
        if keyword in name_lower:
            return zone
    return "living"


def _parse_x1_locations(
    locations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Parse X1 uiconfig locations into floors and rooms.

    Returns:
        Tuple of (floors_list, rooms_dict) ready for YAML output.
    """
    floors: list[dict[str, Any]] = []
    rooms: dict[str, dict[str, Any]] = {}
    floor_keys_seen: set[str] = set()

    floor_levels: dict[str, int] = {}

    def walk(locs: list[dict], floor_name: str = "", depth: int = 0) -> None:
        for loc in locs:
            display = loc.get("displayName", "")
            loc_type = loc.get("locationType", "")
            children = loc.get("locations", [])
            functions = loc.get("functions", [])
            key = _make_key(display)

            # Skip excluded locations
            if key in _EXCLUDED_NAMES:
                continue

            if loc_type == "Building" and display == "Gebäude":
                walk(children, floor_name="", depth=depth)
                continue

            if loc_type == "Floor":
                floor_key = _make_key(display)
                if floor_key not in floor_keys_seen:
                    floor_keys_seen.add(floor_key)
                    floors.append({"key": floor_key, "display_name": display})
                    floor_levels[floor_key] = len(floors) - 1

                # Floor with direct functions = also a room
                if functions:
                    rooms[key] = {
                        "display_name": display,
                        "floor": floor_key,
                        "zone": _guess_zone(display),
                        "device_count": len(functions),
                    }

                walk(children, floor_name=display, depth=depth + 1)

            elif loc_type in ("Room", "ControlCabinet"):
                if key in _EXCLUDED_NAMES:
                    continue
                floor_key = _make_key(floor_name) if floor_name else "unknown"
                rooms[key] = {
                    "display_name": display,
                    "floor": floor_key,
                    "zone": _guess_zone(display),
                    "device_count": len(functions),
                }
                if children:
                    walk(children, floor_name=floor_name, depth=depth + 1)

            elif loc_type == "Building":
                # Area grouping (Außenbereich, Außenbereich)
                if "aussen" not in floor_keys_seen:
                    floor_keys_seen.add("aussen")
                    floors.append({"key": "aussen", "display_name": "Außenbereich"})

                if functions:
                    rooms[key] = {
                        "display_name": display,
                        "floor": "aussen",
                        "zone": "outdoor",
                        "device_count": len(functions),
                    }
                if children:
                    walk(children, floor_name=display, depth=depth + 1)

    walk(locations)
    return floors, rooms


def _generate_yaml(floors: list[dict[str, Any]], rooms: dict[str, dict[str, Any]]) -> str:
    """Generate house_topology.yaml content from parsed data."""
    lines: list[str] = []
    lines.append("# House topology — auto-generated from Gira X1 API.")
    lines.append("# Room keys match HAL device room keys (from uiconfig locations).")
    lines.append("#")
    lines.append("# TODO after generation:")
    lines.append("#   1. Fill in window_directions for each room")
    lines.append("#   2. Set adjacent_to (rooms connected by a door)")
    lines.append("#   3. Set open_to (rooms with open connections, no door)")
    lines.append("#   4. Add staircases connecting floors")
    lines.append("#   5. Adjust zone guesses if needed")
    lines.append("")

    # Floors
    lines.append("floors:")
    level_guesses = {
        "keller": -1,
        "erdgeschoss": 0,
        "obergeschoss": 1,
        "dachgeschoss": 2,
        "aussen": 0,
    }
    for i, floor in enumerate(floors):
        key = floor["key"]
        level = level_guesses.get(key, i)
        lines.append(f"  - key: {key}")
        lines.append(f"    display_name: {floor['display_name']}")
        lines.append(f"    level: {level}")
    lines.append("")

    # Rooms grouped by floor
    lines.append("rooms:")
    current_floor = ""
    for room_key, room in rooms.items():
        floor = room["floor"]
        if floor != current_floor:
            current_floor = floor
            floor_display = next(
                (f["display_name"] for f in floors if f["key"] == floor),
                floor,
            )
            lines.append(f"  # --- {floor_display} ---")

        # Quote keys with special characters
        key_str = f'"{room_key}"' if any(c in room_key for c in ",/") else room_key
        lines.append(f"  {key_str}:")
        lines.append(f"    display_name: {room['display_name']}")
        lines.append(f"    floor: {floor}")
        lines.append(f"    zone: {room['zone']}  # {room['device_count']} devices")
        lines.append("    window_directions: []  # TODO")
        lines.append("    adjacent_to: []  # TODO")
        lines.append("    open_to: []  # TODO")
        lines.append("")

    # Staircases placeholder
    lines.append("staircases:")
    lines.append("  # TODO: Add staircase connections between floors")
    lines.append("  # Example:")
    lines.append("  # - connects: [flur_eg, flur_og]")
    lines.append("  #   type: indoor")
    lines.append("")

    return "\n".join(lines)


async def run(output: Path, config_path: Path | None = None) -> None:
    """Fetch X1 uiconfig and generate topology YAML."""
    config = load_config(config_path)

    host = config.gira_x1.host
    token = config.gira_x1.token
    if not host or not token:
        print("Error: gira_x1.host and GIRA_X1_TOKEN must be configured.")
        print("  Set gira_x1.host in config.yaml")
        print("  Set GIRA_X1_TOKEN in .env")
        sys.exit(1)

    url = f"https://{host}/api/v2/uiconfig?expand=locations&token={token}"
    print(f"Fetching uiconfig from {host}...")

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    locations = data.get("locations", [])
    if not locations:
        print("Error: No locations found in uiconfig response.")
        sys.exit(1)

    functions = data.get("functions", [])
    floors, rooms = _parse_x1_locations(locations)

    print(f"Found {len(floors)} floors, {len(rooms)} rooms, {len(functions)} devices")
    print()

    for floor in floors:
        floor_rooms = [r for r in rooms.values() if r["floor"] == floor["key"]]
        device_total = sum(r["device_count"] for r in floor_rooms)
        print(f"  {floor['display_name']}: {len(floor_rooms)} rooms, {device_total} devices")
        for room in floor_rooms:
            print(f"    - {room['display_name']} ({room['device_count']} devices)")
    print()

    content = _generate_yaml(floors, rooms)

    if output.exists():
        print(f"Warning: {output} already exists.")
        answer = input("Overwrite? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            return

    output.write_text(content, encoding="utf-8")
    print(f"Wrote {output}")
    print()
    print("Next steps:")
    print("  1. Edit the file and fill in TODO fields:")
    print("     - window_directions (compass directions windows face)")
    print("     - adjacent_to (rooms connected by doors)")
    print("     - open_to (rooms with open connections)")
    print("     - staircases (stairway connections between floors)")
    print("  2. Adjust zone guesses if needed (living/sleeping/utility/outdoor)")
    print("  3. Run the app — topology validates against HAL room data at startup")


def main(args: list[str] | None = None) -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate house_topology.yaml from Gira X1 API")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("house_topology.yaml"),
        help="Output file path (default: house_topology.yaml)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to config.yaml (default: auto-detect)",
    )
    parsed = parser.parse_args(args)
    asyncio.run(run(parsed.output, parsed.config))
