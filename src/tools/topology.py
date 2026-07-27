"""Topology tools for the Strands agents — spatial reasoning on demand.

Exposes 4 @tool-decorated async functions for room topology queries.
Dependencies injected via set_context() at startup.

All docstrings are in German — the LLM reads them as tool descriptions.
"""

from __future__ import annotations

import logging
from typing import Any

from strands import tool

from src.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Module-level context — set via set_context() at startup
_ctx: ToolContext | None = None


def set_context(ctx: ToolContext) -> None:
    """Set the tool context for all topology tool functions."""
    global _ctx
    _ctx = ctx


_NOT_CONFIGURED = "Raumtopologie nicht konfiguriert."


@tool
async def get_room_info(room: str) -> str:
    """Rauminfomationen abrufen: Ausrichtung, Zone, Etage, Fläche und angrenzende Räume.

    Gibt eine strukturierte Beschreibung des Raums zurück, einschließlich seiner
    räumlichen Eigenschaften und Nachbarschaft.

    Args:
        room: Raumschlüssel (z.B. "wohnzimmer", "schlafzimmer", "kueche")
    """
    if _ctx is None:
        return _NOT_CONFIGURED
    topology = _ctx.topology
    if topology is None:
        return _NOT_CONFIGURED

    props = topology.get_properties(room)
    if props is None:
        return f"Raum '{room}' nicht in der Topologie gefunden."

    return topology.format_for_prompt(room)


@tool
async def get_nearby_rooms(room: str) -> str:
    """Benachbarte und offen verbundene Räume mit aktuellem Gerätestatus abrufen.

    Gibt alle angrenzenden und offen verbundenen Räume zurück, inklusive
    Informationen über aktive Geräte in diesen Räumen (wenn verfügbar).

    Args:
        room: Raumschlüssel (z.B. "wohnzimmer", "schlafzimmer")
    """
    if _ctx is None:
        return _NOT_CONFIGURED
    topology = _ctx.topology
    if topology is None:
        return _NOT_CONFIGURED

    adjacent = topology.get_adjacent(room)
    open_to = topology.get_open_to(room)
    hal = _ctx.hal

    if not adjacent and not open_to:
        return f"Keine benachbarten Räume für '{room}' bekannt."

    parts: list[str] = []

    if adjacent:
        adj_info: list[str] = []
        for adj_room in adjacent:
            adj_props = topology.get_properties(adj_room)
            display = adj_props["display_name"] if adj_props else adj_room
            if hal is not None:
                try:
                    state = await hal.get_room_state_formatted(adj_room)
                    if state:
                        devices_summary = ", ".join(
                            f"{dev}: {', '.join(f'{k}={v}' for k, v in dp.items())}"
                            for dev, dp in state.items()
                        )
                        adj_info.append(f"{display} [{devices_summary}]")
                    else:
                        adj_info.append(display)
                except Exception:
                    adj_info.append(display)
            else:
                adj_info.append(display)
        parts.append(f"Angrenzend: {', '.join(adj_info)}")

    if open_to:
        open_info: list[str] = []
        for open_room in open_to:
            open_props = topology.get_properties(open_room)
            display = open_props["display_name"] if open_props else open_room
            if hal is not None:
                try:
                    state = await hal.get_room_state_formatted(open_room)
                    if state:
                        devices_summary = ", ".join(
                            f"{dev}: {', '.join(f'{k}={v}' for k, v in dp.items())}"
                            for dev, dp in state.items()
                        )
                        open_info.append(f"{display} [{devices_summary}]")
                    else:
                        open_info.append(display)
                except Exception:
                    open_info.append(display)
            else:
                open_info.append(display)
        parts.append(f"Offen verbunden: {', '.join(open_info)}")

    return "; ".join(parts)


@tool
async def get_rooms_by_floor(floor: str) -> str:
    """Alle Räume auf einer bestimmten Etage mit ihren Zonen auflisten.

    Gibt eine Übersicht aller Räume auf der angegebenen Etage zurück,
    gruppiert nach Zone.

    Args:
        floor: Etagenschlüssel (z.B. "eg" für Erdgeschoss, "og" für Obergeschoss)
    """
    if _ctx is None:
        return _NOT_CONFIGURED
    topology = _ctx.topology
    if topology is None:
        return _NOT_CONFIGURED

    rooms = topology.get_floor_rooms(floor)
    if not rooms:
        return f"Etage '{floor}' nicht bekannt oder keine Räume zugeordnet."

    room_lines: list[str] = []
    for room_key in rooms:
        props = topology.get_properties(room_key)
        if props:
            display = props.get("display_name", room_key)
            zone = props.get("zone", "")
            area = props.get("area_m2", 0)
            room_lines.append(f"{display} (Zone: {zone}, {area} m²)")
        else:
            room_lines.append(room_key)

    return f"Etage '{floor}': " + "; ".join(room_lines)


@tool
async def get_solar_exposure(room: str) -> str:
    """Aktuelle Sonneneinstrahlung für einen Raum ermitteln.

    Berechnet die Sonneneinstrahlung basierend auf der aktuellen Sonnenposition
    (via astral), den Fensterpositionen des Raums und dem aktuellen Wetter.
    Nützlich für Jalousie-Steuerung und Heizungsoptimierung.

    Args:
        room: Raumschlüssel (z.B. "wohnzimmer", "schlafzimmer")
    """
    if _ctx is None:
        return _NOT_CONFIGURED
    topology = _ctx.topology
    if topology is None:
        return _NOT_CONFIGURED

    weather: Any = _ctx.hal.get_platform("weather")
    if weather is None:
        return "Wetterdaten nicht verfügbar — Sonneneinstrahlung kann nicht berechnet werden."

    try:
        weather_data = await weather.get_current()
    except Exception as e:
        logger.warning("Failed to fetch weather for solar assessment: %s", e)
        return "Wetterdaten konnten nicht abgerufen werden."

    latitude = _ctx.latitude
    longitude = _ctx.longitude
    timezone = _ctx.timezone

    result = topology.assess_solar_exposure(room, weather_data, latitude, longitude, timezone)

    level = result["level"]
    description = result["description"]
    irradiance = result["irradiance_w_m2"]
    el = result.get("sun_elevation", 0)

    if level == "none":
        return f"{description} (Einstrahlung: {irradiance:.0f} W/m²)"

    az = result.get("sun_azimuth", 0)
    windows = result.get("lit_windows", [])
    windows_str = f" Beleuchtete Fenster: {', '.join(windows)}." if windows else ""
    return (
        f"{description} (Einstrahlung: {irradiance:.0f} W/m², "
        f"Sonne: {el:.0f}° Höhe, {az:.0f}° Azimut).{windows_str}"
    )
