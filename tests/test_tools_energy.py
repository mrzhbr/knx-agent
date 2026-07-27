"""Tests for energy tools — get_energy_summary and get_weather."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.energy import get_energy_summary, get_weather, set_context
import src.tools.energy as energy_module
from src.tools.context import ToolContext


def _make_ctx(energy_awareness=None, weather=None) -> ToolContext:
    hal = MagicMock()
    hal.get_platform = lambda name: weather if name == "weather" else None
    return ToolContext(
        hal=hal,
        presence_manager=MagicMock(),
        energy_awareness=energy_awareness or MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=MagicMock(),
        schedule_engine=MagicMock(),
    )


@pytest.fixture(autouse=True)
def _reset_ctx():
    """Reset _ctx before each test."""
    energy_module._ctx = None
    yield
    energy_module._ctx = None


async def test_get_energy_summary_returns_formatted():
    ea = MagicMock()
    ea.format_for_prompt.return_value = "Gesamtverbrauch: ~1500 W"
    set_context(_make_ctx(energy_awareness=ea))

    result = await get_energy_summary()
    assert "1500" in result
    ea.format_for_prompt.assert_called_once()


async def test_get_energy_summary_missing_dep():
    result = await get_energy_summary()
    assert "nicht verfügbar" in result


async def test_get_weather_returns_summary():
    weather = AsyncMock()
    weather.get_summary.return_value = "12°C, leichter Regen"
    set_context(_make_ctx(weather=weather))

    result = await get_weather()
    assert "12°C" in result


async def test_get_weather_missing_dep():
    result = await get_weather()
    assert "nicht verfügbar" in result


async def test_get_weather_handles_error():
    weather = AsyncMock()
    weather.get_summary.side_effect = ConnectionError("timeout")
    set_context(_make_ctx(weather=weather))

    result = await get_weather()
    assert "nicht verfügbar" in result
