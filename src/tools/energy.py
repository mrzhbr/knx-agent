"""Energy tools for the energy specialist agent.

Wraps EnergyAwareness and WeatherPlatform for LLM consumption.
Dependencies injected via set_context() at startup.
"""

from __future__ import annotations

import logging
from typing import Any

from strands import tool

from src.tools.context import ToolContext
from src.core.types import HealthState

logger = logging.getLogger(__name__)

# Module-level context. Set via set_context() at startup.
_ctx: ToolContext | None = None


def set_context(ctx: ToolContext) -> None:
    """Set the tool context for all energy tool functions."""
    global _ctx
    _ctx = ctx


@tool
async def get_energy_summary() -> str:
    """Aktuellen Energieverbrauch des Hauses abfragen.

    Zeigt geschätzten und gemessenen Verbrauch aller aktiven Geräte.
    """
    if _ctx is None:
        return "Energieüberwachung nicht verfügbar."
    ea = _ctx.energy_awareness
    if ea is None:
        return "Energieüberwachung nicht verfügbar."
    return ea.format_for_prompt()


@tool
async def get_weather() -> str:
    """Aktuelles Wetter abfragen (Temperatur, Niederschlag, Wind).

    Nützlich für Heizungsoptimierung und Energiesparen.
    """
    if _ctx is None:
        return "Wetterdienst nicht verfügbar."

    weather: Any = _ctx.hal.get_platform("weather")
    if weather is None:
        return "Wetterdienst nicht verfügbar."

    if _ctx.platform_health is not None:
        health = _ctx.platform_health.get_status("weather")
        if health is not None and health.state != HealthState.HEALTHY:
            return weather.error_message

    try:
        return await weather.get_summary()
    except Exception as e:
        logger.warning("Weather query failed: %s", e)
        return f"Wetterdaten nicht verfügbar: {e}"
