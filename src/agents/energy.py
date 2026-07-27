"""Energy specialist agent — heating optimization, consumption monitoring, weather."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agents import create_specialist_agent
from src.agents.prompts import ENERGY_PROMPT
from src.config import ModelSlot

if TYPE_CHECKING:
    from strands import Agent
    from src.config import BrainConfig


def create_energy_agent(config: "BrainConfig", hooks: list | None = None) -> "Agent":
    from src.tools.comfort import get_ac_status, heating_control
    from src.tools.energy import get_energy_summary, get_weather
    from src.tools.memory import remember_correction
    from src.tools.presence import get_window_states
    from src.tools.topology import get_solar_exposure

    return create_specialist_agent(
        config,
        ENERGY_PROMPT,
        [
            get_energy_summary,
            get_weather,
            heating_control,
            get_ac_status,
            get_window_states,
            get_solar_exposure,
            remember_correction,
        ],
        model_slot=ModelSlot.ROUTER_TIER,
        domain="energy",
        hooks=hooks,
    )
