"""Schedule specialist agent — timers, routines, scheduled tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.agents import create_specialist_agent
from src.agents.prompts import SCHEDULE_PROMPT
from src.config import ModelSlot

if TYPE_CHECKING:
    from strands import Agent
    from src.config import BrainConfig


def create_schedule_agent(config: "BrainConfig", hooks: list | None = None) -> "Agent":
    from src.tools.comfort import get_room_state, list_rooms
    from src.tools.memory import remember_correction
    from src.tools.schedule import (
        cancel_schedule,
        create_triggered_routine,
        disable_routine,
        execute_routine,
        list_schedules,
        schedule_task,
    )

    return create_specialist_agent(
        config,
        SCHEDULE_PROMPT,
        [
            schedule_task,
            execute_routine,
            list_schedules,
            cancel_schedule,
            create_triggered_routine,
            disable_routine,
            remember_correction,
            list_rooms,
            get_room_state,
        ],
        model_slot=ModelSlot.SPECIALISTS_TIER,
        domain="schedule",
        hooks=hooks,
    )
