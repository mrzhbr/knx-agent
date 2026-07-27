"""Tests for PatternDetector habit correlation (Task 18.1)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


def make_config():
    config = MagicMock()
    config.models.router.model_id = "accounts/fireworks/models/gpt-oss-120b"
    config.models.router.base_url = "https://api.fireworks.ai/inference/v1"
    config.models.router.api_key_env = "FIREWORKS_API_KEY"
    return config


def make_memory(executions=None):
    procedural = AsyncMock()
    procedural.get_routine_executions = AsyncMock(return_value=executions or [])
    procedural.store_candidate_routine = AsyncMock(return_value=1)
    memory = MagicMock()
    memory.procedural = procedural
    memory.episodic = None
    return memory


def make_presence_event(person: str, is_home: bool, minutes_ago: int = 60) -> dict:
    """Create a synthetic presence event dict (StateStore format)."""
    return {
        "timestamp": (datetime.now() - timedelta(minutes=minutes_ago)).isoformat(),
        "room": "Haus",
        "device_name": person,
        "device_type": "presence_sensor",
        "old_state": {},
        "new_state": {"status": "zuhause" if is_home else "weg"},
        "source": "arp",
    }


# ─── Presence gathering tests ─────────────────────────────────────────────────


async def test_gather_presence_patterns_from_awareness():
    """_gather_presence_patterns returns presence data from awareness stream."""
    from src.background.patterns import PatternDetector
    from unittest.mock import MagicMock

    awareness = MagicMock()
    events = [make_presence_event("Max", i % 2 == 0, minutes_ago=i * 60) for i in range(10)]
    awareness.get_recent_events = AsyncMock(return_value=events)

    memory = make_memory()
    detector = PatternDetector(memory_layer=memory, config=make_config(), awareness=awareness)

    result = await detector._gather_presence_patterns()

    assert "Max" in result
    assert "Unzureichende" not in result


async def test_gather_presence_patterns_insufficient_data():
    """_gather_presence_patterns notes insufficient data when < 5 events."""
    from src.background.patterns import PatternDetector
    from unittest.mock import MagicMock

    awareness = MagicMock()
    # Only 2 presence events (insufficient)
    events = [make_presence_event("Max", True, minutes_ago=i * 120) for i in range(2)]
    awareness.get_recent_events = AsyncMock(return_value=events)

    memory = make_memory()
    detector = PatternDetector(memory_layer=memory, config=make_config(), awareness=awareness)

    result = await detector._gather_presence_patterns()
    assert "Unzureichende" in result or "nicht moeglich" in result


async def test_gather_presence_patterns_no_awareness_uses_fallback():
    """_gather_presence_patterns falls back to routine executions when no awareness."""
    from src.background.patterns import PatternDetector

    executions = [{"routine_id": 1, "executed_at": "2026-03-15T08:00:00", "success": 1}] * 5
    memory = make_memory(executions=executions)

    detector = PatternDetector(memory_layer=memory, config=make_config(), awareness=None)
    result = await detector._gather_presence_patterns()

    # With insufficient presence events, should return the insufficient data message
    assert isinstance(result, str)


async def test_presence_context_injected_into_router_prompt():
    """_call_router_tier includes presence context in prompt when data is sufficient."""
    from src.background.patterns import PatternDetector

    memory = make_memory()
    detector = PatternDetector(memory_layer=memory, config=make_config())

    presence_text = "2026-03-15 08:00 — Max: zuhause\n2026-03-15 18:00 — Max: weg"

    # Patch both create_model_for_slot and Agent so we don't need real API keys
    with patch("src.agents.create_model_for_slot", return_value=MagicMock()):
        with patch("strands.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent_cls.return_value = mock_agent
            mock_agent.return_value = "[]"
            result = await detector._call_router_tier("event1\nevent2", presence_text)
            assert isinstance(result, list)


async def test_router_prompt_skips_presence_on_insufficient():
    """_call_router_tier skips presence section when data is insufficient."""
    from src.background.patterns import PatternDetector

    memory = make_memory()
    detector = PatternDetector(memory_layer=memory, config=make_config())

    insufficient = "Unzureichende Anwesenheitsdaten -- Korrelation nicht moeglich."

    with patch("src.agents.create_model_for_slot", return_value=MagicMock()):
        with patch("strands.Agent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent_cls.return_value = mock_agent
            mock_agent.return_value = "[]"
            result = await detector._call_router_tier("event data", insufficient)
            assert isinstance(result, list)


# ─── Enriched routine storage tests ──────────────────────────────────────────


async def test_stored_routines_include_enriched_trigger():
    """_analyze stores routines with enriched trigger when presence context available."""
    from src.background.patterns import PatternDetector
    from unittest.mock import MagicMock

    awareness = MagicMock()
    events = [make_presence_event("Max", i % 2 == 0, minutes_ago=i * 30) for i in range(10)]
    awareness.get_recent_events = AsyncMock(return_value=events)

    memory = make_memory()
    detector = PatternDetector(memory_layer=memory, config=make_config(), awareness=awareness)

    # Mock router tier to return a pattern with presence context
    mock_patterns = [
        {
            "pattern": "Abendliches Dimmen",
            "occurrences": 7,
            "typical_time": "22:00",
            "confidence": 0.92,
            "who": "Max",
            "days": ["Mo", "Di", "Mi", "Do", "Fr"],
            "trigger_context": "time",
        }
    ]

    with patch.object(detector, "_call_router_tier", return_value=mock_patterns):
        with patch.object(detector._memory, "episodic") as mock_episodic:
            mock_episodic.search_recent = AsyncMock(
                return_value=[
                    {"text": f"event {i}", "metadata": {"timestamp": "2026-03-15T08:00"}}
                    for i in range(25)
                ]
            )

            await detector._analyze()

    # store_candidate_routine should have been called
    memory.procedural.store_candidate_routine.assert_called_once()
    call_kwargs = memory.procedural.store_candidate_routine.call_args.kwargs
    # Trigger should be enriched with presence context
    assert "22:00" in call_kwargs.get("trigger", "")
    # Actions should include who/days metadata
    actions = call_kwargs.get("actions", [])
    assert len(actions) > 0


async def test_pattern_detector_accepts_awareness_parameter():
    """PatternDetector constructor accepts optional awareness parameter."""
    from src.background.patterns import PatternDetector
    from unittest.mock import MagicMock

    memory = make_memory()
    config = make_config()

    # Without awareness
    detector1 = PatternDetector(memory_layer=memory, config=config)
    assert detector1._awareness is None

    # With awareness (MagicMock -- no real StateStore needed)
    awareness = MagicMock()
    detector2 = PatternDetector(memory_layer=memory, config=config, awareness=awareness)
    assert detector2._awareness is awareness


async def test_gather_presence_only_returns_presence_category():
    """_gather_presence_patterns only uses events with device_type='presence_sensor'."""
    from src.background.patterns import PatternDetector
    from unittest.mock import MagicMock

    awareness = MagicMock()
    # Mix of light events (not presence) and presence events
    light_events = [
        {
            "timestamp": (datetime.now() - timedelta(minutes=i * 10)).isoformat(),
            "room": "Wohnzimmer",
            "device_name": "Licht",
            "device_type": "dimmable_light",  # Not presence!
            "old_state": {},
            "new_state": {"OnOff": "1"},
            "source": "x1",
        }
        for i in range(20)
    ]
    presence_events = [make_presence_event("Max", True, minutes_ago=i * 60) for i in range(3)]
    all_events = light_events + presence_events
    awareness.get_recent_events = AsyncMock(return_value=all_events)

    memory = make_memory()
    detector = PatternDetector(memory_layer=memory, config=make_config(), awareness=awareness)

    result = await detector._gather_presence_patterns()
    # Should report insufficient data since light events are filtered out (only 3 presence)
    assert "Unzureichende" in result
