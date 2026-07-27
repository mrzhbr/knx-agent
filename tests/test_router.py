from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.router import route, _classify_offline_intent, _agents_for_intent


async def test_offline_lights_routing():
    """Simple light command should route offline."""
    result = await route("Licht an im Wohnzimmer", router_agent=None, check_internet=False)
    assert result.model == "offline"
    assert result.intent == "comfort"
    assert result.offline_action == "lights_on"


async def test_offline_blinds_routing():
    result = await route("Jalousie hoch", router_agent=None, check_internet=False)
    assert result.model == "offline"
    assert result.offline_action == "blinds_up"


async def test_fallback_when_no_client():
    """Complex query with no router agent falls back to default routing."""
    result = await route(
        "Erkläre mir wie die Heizung funktioniert und was ich sparen kann.",
        router_agent=None,
        check_internet=False,
    )
    assert result.model == "specialists"
    assert result.intent == "comfort"


async def test_offline_who_is_home():
    result = await route("Wer ist zuhause?", router_agent=None)
    assert result.model == "offline"
    assert result.offline_action == "who_is_home"


async def test_specialists_routing_mocked():
    """Test router agent routing with mocked Strands Agent."""
    # Create mock agent that returns a JSON classification
    mock_result = MagicMock()
    mock_result.__str__ = lambda self: json.dumps(
        {
            "intent": "comfort",
            "complexity": 2,
            "agents": ["comfort"],
            "model": "specialists",
            "safety_critical": False,
        }
    )
    mock_agent = MagicMock()
    mock_agent.return_value = mock_result
    mock_agent.messages = MagicMock()

    # Patch asyncio.to_thread to call agent synchronously in test
    with patch(
        "src.agents.router.asyncio.to_thread",
        new=AsyncMock(side_effect=lambda fn, *a: fn(*a)),
    ):
        result = await route(
            "Dimme das Licht im Wohnzimmer auf 50%",
            router_agent=mock_agent,
            check_internet=False,
        )
    # Should get agent result (or offline if pattern matches)
    assert result.intent in ("comfort", "unknown")


async def test_safety_critical_door():
    """Door open command should set safety_critical."""
    result = await route("Tür aufmachen", router_agent=None)
    assert result.safety_critical is True


def test_classify_offline_intent():
    assert _classify_offline_intent("lights_on") == "comfort"
    assert _classify_offline_intent("blinds_up") == "comfort"
    assert _classify_offline_intent("door_open") == "security"
    assert _classify_offline_intent("who_is_home") == "query"


def test_agents_for_intent():
    assert _agents_for_intent("comfort") == ["comfort"]
    assert _agents_for_intent("security") == ["security"]
    assert _agents_for_intent("unknown") == ["comfort"]


def test_agents_for_memory_intent():
    """memory intent routes to supervisor."""
    assert _agents_for_intent("memory") == ["supervisor"]
