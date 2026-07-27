"""Comfort specialist agent — lights, blinds, heating, AC, scenes.

Topology and rules tools are lazy-loaded as nested Haiku agents to reduce
token overhead on every comfort call (17 → 12 tools).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from strands import Agent, tool

from src.agents import create_model_for_slot, create_specialist_agent
from src.agents.prompts import COMFORT_PROMPT
from src.agents.retry import invoke_with_retry
from src.config import ModelSlot

if TYPE_CHECKING:
    from src.config import BrainConfig

logger = logging.getLogger(__name__)

# Module-level nested agents, populated by create_comfort_agent()
_nested_agents: dict[str, Agent] = {}


def _make_nested_agent_tool(name: str, description: str):
    """Create a @tool-wrapped function that dispatches to a nested Haiku agent.

    Same pattern as supervisor._make_agent_tool() but for lightweight sub-agents
    that handle rare queries (topology, rules) without loading their tool schemas
    into every comfort call.
    """

    async def nested_fn(request: str) -> str:
        agent = _nested_agents.get(name)
        if agent is None:
            return f"{name.title()}-Agent nicht verfügbar."
        try:
            return await invoke_with_retry(agent, request, label=f"{name}_nested")
        except Exception:
            logger.exception("%s nested agent error", name)
            return f"Fehler beim {name.title()}-Agenten."

    nested_fn.__name__ = f"{name}_agent"
    nested_fn.__doc__ = f"{description}\n\n    Args:\n        request: Die Anfrage auf Deutsch"
    return tool(nested_fn)


def create_comfort_agent(config: "BrainConfig", hooks: list | None = None) -> "Agent":
    from src.tools.comfort import (
        ac_control,
        blinds_control,
        execute_batch,
        get_ac_status,
        get_room_state,
        heating_control,
        lights_control,
        list_rooms,
    )
    from src.tools.energy import get_weather
    from src.tools.memory import recall_preference, remember_correction
    from src.tools.scenes import create_scene, delete_scene, execute_scene, list_scenes
    from src.tools.rules import approve_custom_rule, configure_house_rule, list_house_rules
    from src.tools.topology import (
        get_nearby_rooms,
        get_room_info,
        get_rooms_by_floor,
        get_solar_exposure,
    )

    # Create lightweight nested agents for rare queries
    router_model = create_model_for_slot(config, ModelSlot.ROUTER_TIER)

    _nested_agents["topology"] = Agent(
        model=router_model,
        system_prompt="Du beantwortest Fragen zur Raumtopologie des Hauses. Antworte auf Deutsch.",
        tools=[get_room_info, get_nearby_rooms, get_rooms_by_floor, get_solar_exposure],
        hooks=hooks or [],  # AD-9: pass training hooks to nested agents
    )
    _nested_agents["rules"] = Agent(
        model=router_model,
        system_prompt="Du verwaltest die Hausregeln. Antworte auf Deutsch.",
        tools=[configure_house_rule, list_house_rules, approve_custom_rule],
        hooks=hooks or [],  # AD-9: pass training hooks to nested agents
    )
    logger.info("Comfort: nested topology + rules agents created (router)")

    topology_tool = _make_nested_agent_tool(
        "topology",
        "Raumtopologie-Spezialist: Rauminfos, Nachbarräume, Etagen, Sonneneinstrahlung.",
    )
    rules_tool = _make_nested_agent_tool(
        "rules",
        "Hausregel-Verwaltung: Regeln aktivieren/deaktivieren, auflisten, genehmigen.",
    )

    return create_specialist_agent(
        config,
        COMFORT_PROMPT,
        [
            lights_control,
            blinds_control,
            heating_control,
            get_room_state,
            list_rooms,
            execute_batch,
            get_weather,
            recall_preference,
            remember_correction,
            ac_control,
            get_ac_status,
            topology_tool,
            rules_tool,
            execute_scene,
            create_scene,
            list_scenes,
            delete_scene,
        ],
        model_slot=ModelSlot.SPECIALISTS_TIER,
        domain="comfort",
        hooks=hooks,
    )
