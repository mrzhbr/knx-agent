"""Strands Agent-based intent router with offline regex fallback.

Routing logic:
1. Try offline regex patterns (instant, no API)
2. If no match AND internet available: call router agent for classification
3. If agent fails or no internet: return safe default routing
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Module-level training writer — set by bootstrapper when training_data.enabled (AD-5)
_training_writer: Any = None  # TrainingDataWriter | None


def set_training_writer(writer: Any) -> None:
    """Inject the TrainingDataWriter instance for router logging."""
    global _training_writer
    _training_writer = writer


@dataclass
class RouterResult:
    """Result of routing a user request."""

    intent: str  # comfort|security|energy|schedule|query|unknown
    complexity: int  # 1-5
    agents: list[str]  # e.g. ["comfort"]
    model: str  # offline|router|specialists|reflection
    safety_critical: bool = False
    offline_action: str = ""  # action type if offline match
    offline_params: dict = field(default_factory=dict)


_DEFAULT_ROUTING = RouterResult(
    intent="comfort",
    complexity=3,
    agents=["comfort"],
    model="specialists",
    safety_critical=False,
)


async def _check_internet(timeout: float = 2.0) -> bool:
    """Check internet connectivity by attempting TCP connection to Cloudflare DNS."""
    try:
        conn = asyncio.open_connection("1.1.1.1", 443)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def route(
    text: str,
    router_agent: "Any | None" = None,
    check_internet: bool = True,
) -> RouterResult:
    """Route a user request to the appropriate agent and model.

    Args:
        text: User input text (German)
        router_agent: Strands Agent for classification (optional)
        check_internet: Whether to check internet before API call

    Returns:
        RouterResult with routing decision
    """
    from .offline import try_offline
    from src.training.context import get_request_id

    # 1. Try offline regex patterns first
    offline_match = try_offline(text)
    if offline_match:
        action_type, params = offline_match
        result = RouterResult(
            intent=_classify_offline_intent(action_type),
            complexity=1,
            agents=_agents_for_intent(_classify_offline_intent(action_type)),
            model="offline",
            safety_critical=action_type in ("door_open", "alarm_status"),
            offline_action=action_type,
            offline_params=params,
        )

        # Log offline match (AD-5: we are in the main event loop, safe to create_task)
        if _training_writer is not None:
            asyncio.create_task(
                _training_writer.log_routing(
                    {
                        "request_id": get_request_id(),
                        "source": "offline",
                        "input_text": text,
                        "raw_response": "",
                        "parsed": {
                            "intent": result.intent,
                            "complexity": result.complexity,
                            "agents": result.agents,
                            "model": result.model,
                            "safety_critical": result.safety_critical,
                        },
                        "router_model": "",
                        "offline_action": action_type,
                        "offline_params": params,
                    }
                )
            )

        return result

    # 2. Check internet and try router agent
    if router_agent is not None:
        internet_ok = True
        if check_internet:
            internet_ok = await _check_internet()

        if internet_ok:
            try:
                # Get router model_id for logging (AD-10)
                router_model_id = ""
                if hasattr(router_agent, "model") and hasattr(router_agent.model, "model_id"):
                    router_model_id = router_agent.model.model_id or ""

                result = await _route_with_agent(
                    text, router_agent, router_model_id=router_model_id
                )
                if result:
                    return result
            except Exception as e:
                logger.warning("Agent routing failed: %s", e)

    # 3. Fallback: safe default
    logger.debug("Using default routing for: %s", text[:50])
    return _DEFAULT_ROUTING


async def _route_with_agent(
    text: str,
    agent: "Any",
    router_model_id: str = "",
) -> RouterResult | None:
    """Call Strands Agent to classify the intent and return routing decision."""
    from src.training.context import get_request_id

    try:
        agent.messages.clear()  # AD-7: prevent history accumulation
        result = await asyncio.to_thread(agent, text)  # AD-6: sync->async bridge
        content = str(result).strip()

        # Parse JSON response
        # Strip markdown code blocks if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])

        data = json.loads(content)

        intent = data.get("intent", "unknown")
        # Agent should never return "offline" — that's only for regex matches
        model = data.get("model", "specialists")
        if model == "offline":
            model = "router"
        agents = data.get("agents", _agents_for_intent(intent))
        # Ensure we always have at least one agent
        if not agents:
            agents = _agents_for_intent(intent)
        if not agents:
            agents = ["comfort"]

        routing = RouterResult(
            intent=intent,
            complexity=int(data.get("complexity", 3)),
            agents=agents,
            model=model,
            safety_critical=bool(data.get("safety_critical", False)),
        )

        # Log agent classification (AD-5: back in main event loop after to_thread returns)
        if _training_writer is not None:
            asyncio.create_task(
                _training_writer.log_routing(
                    {
                        "request_id": get_request_id(),
                        "source": "agent",
                        "input_text": text,
                        "raw_response": content,
                        "parsed": {
                            "intent": intent,
                            "complexity": data.get("complexity"),
                            "agents": agents,
                            "model": model,
                            "safety_critical": data.get("safety_critical"),
                        },
                        "router_model": router_model_id,
                    }
                )
            )

        return routing
    except (json.JSONDecodeError, KeyError, Exception) as e:
        logger.warning("Agent routing failed: %s", e)
        return None


def _classify_offline_intent(action_type: str) -> str:
    """Map offline action type to intent category."""
    if action_type.startswith(("lights_", "blinds_", "heating_", "all_off")):
        return "comfort"
    elif action_type in ("door_open", "alarm_status"):
        return "security"
    elif action_type in ("who_is_home", "arriving_soon", "leaving"):
        return "query"
    return "comfort"


def _agents_for_intent(intent: str) -> list[str]:
    """Map intent to list of agents to invoke."""
    mapping: dict[str, list[str]] = {
        "comfort": ["comfort"],
        "security": ["security"],
        "energy": ["energy"],
        "schedule": ["schedule"],
        "memory": ["supervisor"],
        "query": ["comfort"],
        "unknown": ["comfort"],
    }
    return mapping.get(intent, ["comfort"])
