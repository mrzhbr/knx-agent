"""Supervisor agent — orchestrates specialist agents via tool-wrapped dispatchers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from strands import Agent, tool

from src.agents import create_model_for_slot
from src.agents.prompts import build_supervisor_prompt
from src.agents.retry import invoke_with_retry
from src.config import ModelSlot

if TYPE_CHECKING:
    from src.config import BrainConfig

logger = logging.getLogger(__name__)

# Module-level agent references, set by create_supervisor()
_agents: dict[str, Agent] = {}

# German descriptions shown to the LLM as tool docstrings
_AGENT_DESCRIPTIONS: dict[str, str] = {
    "comfort": "Komfort-Spezialist: Licht, Jalousien, Heizung, Klimaanlage, Raumzustand, Szenen.",
    "security": "Sicherheits-Spezialist: Alarm, Türöffner, Kamerastatus, Fensterzustand.",
    "schedule": "Zeitplan-Spezialist: Timer, Routinen, geplante Aufgaben, Kalender.",
    "energy": "Energie-Spezialist: Heizung, Energieverbrauch, Wetter, Energiesparen.",
}


def _make_agent_tool(name: str, description: str):
    """Create a @tool-wrapped async function that dispatches to a specialist agent.

    Uses invoke_async so the specialist runs in the caller's event loop instead of
    spawning an ephemeral loop via run_async().  This prevents httpx connection-pool
    entries from outliving the loop they were created on ("Event loop is closed").
    """

    async def agent_fn(request: str) -> str:
        agent = _agents.get(name)
        if agent is None:
            return f"{name.title()}-Agent nicht verfügbar."
        try:
            return await invoke_with_retry(agent, request, label=f"{name}_agent")
        except Exception:
            logger.exception("%s_agent tool error", name)
            return f"Fehler beim {name.title()}-Agenten."

    agent_fn.__name__ = f"{name}_agent"
    agent_fn.__doc__ = (
        f"{description}\n\n"
        "    Args:\n"
        "        request: Die Benutzeranfrage an den Spezialisten (deutsch)"
    )
    return tool(agent_fn)


def create_supervisor(
    config: "BrainConfig",
    agents: dict[str, Agent] | None = None,
    memory_layer: Any = None,
    hooks: list | None = None,
) -> Agent:
    """Create the supervisor agent with specialist agents as tools.

    Args:
        config: BrainConfig with model slot settings
        agents: Dict mapping agent name → Agent instance
        memory_layer: MemoryLayer for context assembly (optional)
    """
    from src.tools.memory import (
        describe_device,
        get_unknown_devices,
        recall,
        remember,
        remember_correction,
    )

    model = create_model_for_slot(config, ModelSlot.SPECIALISTS_TIER)
    tools: list[Any] = [remember, recall, remember_correction, describe_device, get_unknown_devices]

    num_specialists = 0
    for name, instance in (agents or {}).items():
        _agents[name] = instance
        description = _AGENT_DESCRIPTIONS.get(name, f"{name.title()}-Spezialist.")
        tools.append(_make_agent_tool(name, description))
        num_specialists += 1
        logger.info("Supervisor: %s agent registered as tool", name)

    if num_specialists == 0:
        logger.warning("Supervisor created with no specialist agents")

    agent = Agent(model=model, system_prompt=build_supervisor_prompt(), tools=tools, hooks=hooks)
    logger.info(
        "Supervisor agent created with model slot %s and %d specialists",
        ModelSlot.SPECIALISTS_TIER.value,
        num_specialists,
    )
    return agent
