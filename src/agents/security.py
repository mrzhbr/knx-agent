"""Security specialist agent — alarm, door, camera events, window states."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agents import create_specialist_agent
from src.agents.prompts import SECURITY_PROMPT
from src.config import ModelSlot

if TYPE_CHECKING:
    from strands import Agent
    from src.config import BrainConfig


def create_security_agent(config: "BrainConfig", hooks: list | None = None) -> "Agent":
    from src.tools.memory import remember_correction
    from src.tools.security import alarm_control, door_open, get_camera_events, get_security_status
    from src.tools.presence import get_window_states, who_is_home

    return create_specialist_agent(
        config,
        SECURITY_PROMPT,
        [
            alarm_control,
            door_open,
            get_security_status,
            get_camera_events,
            who_is_home,
            get_window_states,
            remember_correction,
        ],
        model_slot=ModelSlot.SPECIALISTS_TIER,
        domain="security",
        hooks=hooks,
    )
