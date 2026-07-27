"""Presence tools for the Strands security/comfort agent.

These @tool-decorated async functions query who is home and window states.
get_window_states is placed here (not in security.py) for cleaner separation.
"""

from __future__ import annotations

import logging

from strands import tool

from src.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Module-level context. Set via set_context() at startup.
_ctx: ToolContext | None = None


def set_context(ctx: ToolContext) -> None:
    """Set the tool context for all presence tool functions."""
    global _ctx
    _ctx = ctx


@tool
async def who_is_home() -> str:
    """Abfragen wer aktuell zuhause ist (ARP + Kamera-Zonen).

    Gibt eine Deutsche Zusammenfassung der aktuellen Anwesenheit zurück,
    inklusive Kamera-Eingangsaktivität der letzten 5 Minuten.
    """
    if _ctx is None:
        return "Anwesenheitserkennung nicht verfügbar."
    presence = _ctx.presence_manager
    if presence is None:
        return "Anwesenheitserkennung nicht verfügbar."

    try:
        return presence.format_for_prompt()
    except Exception as e:
        logger.error("who_is_home error: %s", e)
        return f"Fehler bei Anwesenheitsabfrage: {e}"


@tool
async def get_window_states(zone: str = "") -> str:
    """Zustand aller Fenster und Türen abfragen.

    Args:
        zone: Optionaler Raumname zum Filtern (leer = alle Räume)
    """
    from src.hal.models import SensorClass

    if _ctx is None:
        return "Fensterstatus nicht verfügbar."
    hal = _ctx.hal
    presence = _ctx.presence_manager

    if hal is None:
        # Fall back to presence manager if available
        if presence is not None:
            open_windows = presence.open_windows
            if open_windows:
                return f"Offene Fenster/Türen: {', '.join(open_windows)}"
            else:
                return "Alle Fenster/Türen geschlossen."
        return "Fensterstatus nicht verfügbar."

    sensors = hal.get_sensors_by_class(SensorClass.WINDOW)
    if not sensors:
        return "Keine Fenster-/Türsensoren konfiguriert."

    open_items: list[str] = []
    closed_items: list[str] = []

    for adapter in sensors:
        room_name = adapter.info.room

        # Filter by zone if specified
        if zone and zone.lower() not in room_name.lower():
            continue

        try:
            state = await adapter.read_state()
            val = state.state.get("Binary") or state.state.get("is_on")
            if str(val) == "1":
                open_items.append(f"{adapter.info.name} ({room_name})")
            else:
                closed_items.append(f"{adapter.info.name} ({room_name})")
        except Exception:
            pass

    if not open_items and not closed_items:
        return f"Keine Sensoren{' in ' + zone if zone else ''} gefunden."

    lines: list[str] = []
    if open_items:
        lines.append(f"OFFEN ({len(open_items)}):")
        for item in open_items:
            lines.append(f"  - {item}")
    if closed_items:
        lines.append(f"Geschlossen ({len(closed_items)}):")
        for item in closed_items[:5]:  # Show max 5 closed items
            lines.append(f"  - {item}")
        if len(closed_items) > 5:
            lines.append(f"  ... und {len(closed_items) - 5} weitere")

    return "\n".join(lines) if lines else "Alle Fenster/Türen geschlossen."
