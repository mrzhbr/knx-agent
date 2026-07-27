"""Tests for ReflectionAgent anomaly detection enhancements (Task 18.2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from src.background.reflection import ReflectionAgent


def make_config():
    config = MagicMock()
    config.models.reflection.model_id = "anthropic/claude-opus-4.6"
    config.models.reflection.base_url = "https://openrouter.ai/api/v1"
    config.models.reflection.api_key_env = "OPENROUTER_API_KEY"
    config.matrix.room_id = "!room:example.test"
    return config


def make_hal():
    bus = MagicMock()
    bus.health = AsyncMock(return_value={"gira_x1": True, "matrix_bot": True})
    return bus


def make_matrix():
    matrix = MagicMock()
    matrix.send = AsyncMock()
    return matrix


def make_memory(episodic_snapshots=None):
    episodic = AsyncMock()
    # search() returns health snapshots based on count requested
    snapshots = episodic_snapshots or []
    episodic.search = AsyncMock(return_value=snapshots)
    episodic.search_recent = AsyncMock(return_value=[])
    episodic.get_recent_corrections = AsyncMock(return_value=[])
    episodic.add_event = AsyncMock()
    episodic.count_events = AsyncMock(return_value=0)
    episodic.count_recent_corrections = AsyncMock(return_value=0)
    episodic.get_repeated_corrections = AsyncMock(return_value=[])

    procedural = AsyncMock()
    procedural.get_routines = AsyncMock(return_value=[])
    procedural.update_routine = AsyncMock()
    procedural.search_preferences = AsyncMock(return_value=[])
    procedural.add_preference = AsyncMock()
    procedural.get_routine_executions = AsyncMock(return_value=[])
    procedural.add_health_snapshot = AsyncMock()
    procedural.get_latest_health_snapshot = AsyncMock(return_value=[])
    procedural.prune_health_snapshots = AsyncMock()
    procedural.count_preferences = AsyncMock(return_value=0)
    procedural.get_top_rooms = AsyncMock(return_value=[])
    procedural.get_top_devices = AsyncMock(return_value=[])
    procedural.get_recent_routine_stats = AsyncMock(return_value={"success": 0, "failure": 0})

    semantic = MagicMock()
    semantic.edge_count = MagicMock(return_value=0)

    memory = MagicMock()
    memory.episodic = episodic
    memory.procedural = procedural
    memory.semantic = semantic
    return memory


# ─── REFLECTION_PROMPT tests ─────────────────────────────────────────────────


def test_reflection_prompt_contains_anomaly_categories():
    """REFLECTION_PROMPT includes all 4 anomaly categories."""
    from src.agents.prompts import REFLECTION_PROMPT

    assert (
        "Geraete-Anomalien" in REFLECTION_PROMPT
        or "Geräte-Anomalien" in REFLECTION_PROMPT
        or "Gerät" in REFLECTION_PROMPT
    )
    assert "Verhaltens-Anomalien" in REFLECTION_PROMPT or "Verhalten" in REFLECTION_PROMPT
    assert "Energie-Anomalien" in REFLECTION_PROMPT or "Energie" in REFLECTION_PROMPT
    assert "Sicherheits-Anomalien" in REFLECTION_PROMPT or "Sicherheit" in REFLECTION_PROMPT


def test_reflection_prompt_contains_severity_in_output_format():
    """REFLECTION_PROMPT output format includes severity field."""
    from src.agents.prompts import REFLECTION_PROMPT

    assert (
        "severity" in REFLECTION_PROMPT
        or "high|medium|low" in REFLECTION_PROMPT
        or "low|medium|high" in REFLECTION_PROMPT
    )


# ─── Health snapshot tests ────────────────────────────────────────────────────


async def test_health_snapshot_written_after_reflection():
    """_reflect writes health snapshot to episodic memory."""
    memory = make_memory(
        episodic_snapshots=[{"text": "snap1"}, {"text": "snap2"}, {"text": "snap3"}]
    )
    matrix = make_matrix()
    agent = ReflectionAgent(memory, make_hal(), matrix, make_config())

    analysis = {
        "routine_updates": [],
        "new_suggestions": [],
        "anomalies": [],
        "energy_insights": [],
        "behavioral_changes": [],
    }
    with patch.object(agent, "_call_reflection_tier", AsyncMock(return_value=analysis)):
        with patch.object(agent, "_should_reflect", AsyncMock(return_value=True)):
            await agent._reflect()

    # add_event should have been called for health snapshot
    memory.episodic.add_event.assert_called()
    call_kwargs = memory.episodic.add_event.call_args
    metadata = call_kwargs.kwargs.get("metadata") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else {}
    )
    assert metadata.get("type") == "health_check"


async def test_health_snapshot_contains_required_fields():
    """Health snapshot metadata contains integrations_healthy, integrations_total, anomalies_found."""
    memory = make_memory(episodic_snapshots=[{"text": "s1"}, {"text": "s2"}, {"text": "s3"}])
    agent = ReflectionAgent(memory, make_hal(), make_matrix(), make_config())

    analysis = {
        "routine_updates": [],
        "new_suggestions": [],
        "anomalies": [{"description": "test"}],
        "energy_insights": [],
        "behavioral_changes": [],
    }
    with patch.object(agent, "_call_reflection_tier", AsyncMock(return_value=analysis)):
        await agent._reflect()

    add_event_call = memory.episodic.add_event.call_args
    metadata = add_event_call.kwargs.get("metadata", {}) or {}
    assert "integrations_healthy" in metadata
    assert "integrations_total" in metadata
    assert "anomalies_found" in metadata


# ─── Insufficient data tests ──────────────────────────────────────────────────


async def test_anomaly_skipped_with_insufficient_snapshots():
    """With fewer than 3 health snapshots, anomaly analysis is noted as skipped."""
    # Only 1 snapshot
    memory = make_memory(episodic_snapshots=[{"text": "snap1"}])
    agent = ReflectionAgent(memory, make_hal(), make_matrix(), make_config())

    assert not await agent._check_sufficient_data()


async def test_anomaly_runs_with_3_or_more_snapshots():
    """With 3+ health snapshots, _check_sufficient_data returns True."""
    snapshots = [{"text": f"s{i}"} for i in range(5)]
    memory = make_memory(episodic_snapshots=snapshots)
    agent = ReflectionAgent(memory, make_hal(), make_matrix(), make_config())

    assert await agent._check_sufficient_data()


async def test_anomaly_skipped_no_episodic():
    """_check_sufficient_data returns False when no episodic memory."""
    memory = make_memory()
    memory.episodic = None  # No episodic memory
    agent = ReflectionAgent(memory, make_hal(), make_matrix(), make_config())

    assert not await agent._check_sufficient_data()


# ─── Severity routing tests ───────────────────────────────────────────────────


async def test_high_severity_anomaly_sent_to_matrix():
    """High-severity anomaly is sent to Matrix immediately."""
    memory = make_memory()
    matrix = make_matrix()
    agent = ReflectionAgent(memory, make_hal(), matrix, make_config())

    high_anomaly = {
        "category": "device",
        "severity": "high",
        "description": "Kamera verlor 47 Verbindungen",
        "evidence": "47 reconnects in 7 days",
    }
    await agent._route_anomalies([high_anomaly])

    matrix.send.assert_called_once()
    call_text = matrix.send.call_args[0][1]
    assert "Warnung" in call_text or "Kamera" in call_text


async def test_low_severity_anomaly_not_sent_to_matrix():
    """Low-severity anomaly is only logged, not sent to Matrix."""
    memory = make_memory()
    matrix = make_matrix()
    agent = ReflectionAgent(memory, make_hal(), matrix, make_config())

    low_anomaly = {
        "category": "energy",
        "severity": "low",
        "description": "Leicht erhöhter Verbrauch",
        "evidence": "3% over baseline",
    }
    await agent._route_anomalies([low_anomaly])

    matrix.send.assert_not_called()


async def test_medium_severity_anomaly_not_sent_immediately():
    """Medium-severity anomaly is logged but not sent to Matrix immediately."""
    memory = make_memory()
    matrix = make_matrix()
    agent = ReflectionAgent(memory, make_hal(), matrix, make_config())

    medium_anomaly = {
        "category": "behavior",
        "severity": "medium",
        "description": "Ungewöhnliche Aktivität um 3 Uhr",
        "evidence": "lights on at 3 AM",
    }
    await agent._route_anomalies([medium_anomaly])

    matrix.send.assert_not_called()


# ─── _format_anomaly_notification tests ──────────────────────────────────────


def test_format_anomaly_notification_high_severity():
    """High severity anomaly notification uses [Warnung] prefix."""
    memory = make_memory()
    agent = ReflectionAgent(memory, make_hal(), make_matrix(), make_config())

    anomaly = {
        "category": "device",
        "severity": "high",
        "description": "Kamera Problem",
        "evidence": "47 reconnects",
    }
    msg = agent._format_anomaly_notification(anomaly)

    assert "[Warnung]" in msg
    assert "Kamera Problem" in msg


def test_format_anomaly_notification_medium_severity():
    """Medium severity anomaly notification uses [Hinweis] prefix."""
    memory = make_memory()
    agent = ReflectionAgent(memory, make_hal(), make_matrix(), make_config())

    anomaly = {
        "category": "energy",
        "severity": "medium",
        "description": "Erhöhter Verbrauch",
        "evidence": "",
    }
    msg = agent._format_anomaly_notification(anomaly)

    assert "[Hinweis]" in msg
    assert "Erhöhter Verbrauch" in msg
