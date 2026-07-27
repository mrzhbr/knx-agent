"""Tests for ReflectionAgent (src/background/reflection.py)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch


from src.background.reflection import ReflectionAgent


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_config():
    """Return a minimal BrainConfig-like object."""
    config = MagicMock()
    config.models.reflection.model_id = "anthropic/claude-opus-4.6"
    config.models.reflection.base_url = "https://openrouter.ai/api/v1"
    config.models.reflection.api_key_env = "OPENROUTER_API_KEY"
    config.matrix.room_id = "!test:matrix.org"
    return config


def _make_memory_layer(events=None, corrections=None, routines=None):
    """Return a mocked MemoryLayer with episodic + procedural + semantic."""
    memory = MagicMock()

    # Episodic
    episodic = MagicMock()
    episodic.search_recent = AsyncMock(return_value=events or [])
    episodic.get_recent_corrections = AsyncMock(return_value=corrections or [])
    episodic.add_event = AsyncMock()
    episodic.count_events = AsyncMock(return_value=0)
    episodic.count_recent_corrections = AsyncMock(return_value=0)
    episodic.get_repeated_corrections = AsyncMock(return_value=[])
    memory.episodic = episodic

    # Procedural
    procedural = MagicMock()
    procedural.get_routines = AsyncMock(return_value=routines or [])
    procedural.update_routine = AsyncMock()
    procedural.search_preferences = AsyncMock(return_value=[])
    procedural.add_preference = AsyncMock()
    procedural.add_health_snapshot = AsyncMock()
    procedural.get_latest_health_snapshot = AsyncMock(return_value=[])
    procedural.prune_health_snapshots = AsyncMock()
    procedural.count_preferences = AsyncMock(return_value=0)
    procedural.get_top_rooms = AsyncMock(return_value=[])
    procedural.get_top_devices = AsyncMock(return_value=[])
    procedural.get_recent_routine_stats = AsyncMock(return_value={"success": 0, "failure": 0})
    memory.procedural = procedural

    # Semantic
    semantic = MagicMock()
    semantic.edge_count = MagicMock(return_value=0)
    memory.semantic = semantic

    return memory


def _make_hal():
    """Return a mocked HAL with platforms."""
    hal = MagicMock()
    mock_platform = MagicMock()
    mock_platform.health_check = AsyncMock(return_value=True)
    hal._platforms = {"gira_x1": mock_platform}
    return hal


def _make_matrix():
    """Return a mocked MatrixBot."""
    matrix = MagicMock()
    matrix.send = AsyncMock()
    return matrix


def _make_reflection_response(
    routine_updates=None,
    new_suggestions=None,
    anomalies=None,
    energy_insights=None,
    behavioral_changes=None,
):
    """Build a JSON string like reflection tier would return."""
    data = {
        "routine_updates": routine_updates or [],
        "new_suggestions": new_suggestions or [],
        "anomalies": anomalies or [],
        "energy_insights": energy_insights or [],
        "behavioral_changes": behavioral_changes or [],
    }
    return json.dumps(data)


# ── Sleep-until tests ─────────────────────────────────────────────────────────


async def test_sleep_until_future_same_day():
    """_sleep_until computes positive delta to a future hour today."""
    memory = _make_memory_layer()
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    # Patch sleep to capture the duration
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    # Simulate current time at 01:00
    fake_now = datetime.now().replace(hour=1, minute=0, second=0, microsecond=0)
    with patch("src.background.reflection.asyncio.sleep", fake_sleep):
        with patch("src.background.reflection.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            await agent._sleep_until(hour=3)

    assert len(slept) == 1
    # Should sleep ~2 hours = 7200 seconds
    assert 7100 <= slept[0] <= 7300


async def test_sleep_until_wraps_to_next_day():
    """_sleep_until adds 1 day when target hour already passed."""
    memory = _make_memory_layer()
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    # Simulate current time at 04:00 (past 3 AM)
    fake_now = datetime.now().replace(hour=4, minute=0, second=0, microsecond=0)
    with patch("src.background.reflection.asyncio.sleep", fake_sleep):
        with patch("src.background.reflection.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            await agent._sleep_until(hour=3)

    assert len(slept) == 1
    # Should sleep ~23 hours
    assert slept[0] > 80000  # > 22 hours


# ── _should_reflect tests ──────────────────────────────────────────────────────


async def test_should_reflect_first_run_returns_true():
    """_should_reflect returns True on first run (no stored date)."""
    memory = _make_memory_layer()
    # No stored preferences
    memory.procedural.search_preferences = AsyncMock(return_value=[])
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    result = await agent._should_reflect()

    assert result is True
    # Should store the first-run date
    memory.procedural.add_preference.assert_called_once()


async def test_should_reflect_daily_within_window():
    """_should_reflect returns True when within first 14 days."""
    memory = _make_memory_layer()
    yesterday = (datetime.now() - timedelta(days=5)).date().isoformat()
    memory.procedural.search_preferences = AsyncMock(
        return_value=[{"fact": f"reflection_first_run_date:{yesterday}"}]
    )
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    result = await agent._should_reflect()
    assert result is True


async def test_should_reflect_weekly_on_sunday():
    """_should_reflect returns True outside daily window when today is Sunday."""
    memory = _make_memory_layer()
    # 20 days ago = past daily window
    old_date = (datetime.now() - timedelta(days=20)).date().isoformat()
    memory.procedural.search_preferences = AsyncMock(
        return_value=[{"fact": f"reflection_first_run_date:{old_date}"}]
    )
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    # Force weekday to Sunday (6)
    with patch("src.background.reflection.datetime") as mock_dt:
        mock_dt.now.return_value = MagicMock()
        mock_dt.now.return_value.date.return_value = datetime.now().date()
        mock_dt.now.return_value.weekday.return_value = 6
        mock_dt.fromisoformat.return_value = datetime.fromisoformat(old_date)
        result = await agent._should_reflect()

    assert result is True


# ── _reflect tests ────────────────────────────────────────────────────────────


async def test_reflect_sends_suggestions_to_matrix():
    """_reflect sends new_suggestions to Matrix for user approval."""
    suggestions = [
        {"description": "Rollläden um 22 Uhr schließen", "occurrences": 7, "confidence": 0.9}
    ]
    reflection_json = _make_reflection_response(new_suggestions=suggestions)

    memory = _make_memory_layer()
    matrix = _make_matrix()
    agent = ReflectionAgent(memory, _make_hal(), matrix, _make_config())

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=json.loads(reflection_json))
    ):
        await agent._reflect()

    assert matrix.send.called
    # Morning summary is the last call; check any call contains the suggestion
    assert any("Vorschlag" in str(call) for call in matrix.send.call_args_list)
    assert any("Rollläden" in str(call) for call in matrix.send.call_args_list)


async def test_reflect_sends_anomalies_to_matrix():
    """_reflect sends anomalies to Matrix."""
    anomalies = [{"description": "Kamera 243 verlor 47 Verbindungen (normal: 8-12)"}]
    reflection_json = _make_reflection_response(anomalies=anomalies)

    memory = _make_memory_layer()
    matrix = _make_matrix()
    agent = ReflectionAgent(memory, _make_hal(), matrix, _make_config())

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=json.loads(reflection_json))
    ):
        await agent._reflect()

    assert matrix.send.called
    # "Kamera" appears only in the anomaly routing send (not the morning summary)
    assert any("Kamera" in str(call) for call in matrix.send.call_args_list)


async def test_reflect_applies_routine_updates():
    """_reflect calls update_routine for each routine_update in analysis."""
    updates = [{"id": 42, "confidence": 0.95, "enabled": True}]
    reflection_json = _make_reflection_response(routine_updates=updates)

    memory = _make_memory_layer()
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=json.loads(reflection_json))
    ):
        await agent._reflect()

    memory.procedural.update_routine.assert_called_once_with(42, confidence=0.95, enabled=True)


async def test_reflect_no_crash_when_opus_fails():
    """_reflect handles empty analysis dict without crashing."""
    memory = _make_memory_layer()
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    with patch.object(agent, "_call_reflection_tier", AsyncMock(return_value={})):
        await agent._reflect()  # Must not raise


async def test_reflect_skips_matrix_when_no_room_id():
    """_reflect sends nothing to Matrix if room_id is not configured."""
    config = _make_config()
    config.matrix.room_id = ""

    suggestions = [{"description": "Test Vorschlag", "occurrences": 5, "confidence": 0.9}]
    reflection_json = _make_reflection_response(new_suggestions=suggestions)

    memory = _make_memory_layer()
    matrix = _make_matrix()
    agent = ReflectionAgent(memory, _make_hal(), matrix, config)

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=json.loads(reflection_json))
    ):
        await agent._reflect()

    # No send calls when room_id is empty
    assert not matrix.send.called


# ── Lifecycle tests ───────────────────────────────────────────────────────────


async def test_reflection_agent_start_creates_task():
    """start() creates an asyncio Task."""
    memory = _make_memory_layer()
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    with patch.object(agent, "run", AsyncMock()):
        agent.start()
        assert agent._task is not None
        agent.stop()


async def test_reflection_agent_stop_cancels_task():
    """stop() cancels the running task."""
    memory = _make_memory_layer()
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    with patch.object(agent, "run", AsyncMock()):
        agent.start()
        agent.stop()
        # Task may be in cancelling state before the event loop processes it
        assert agent._task is not None
        assert agent._task.cancelling() > 0 or agent._task.cancelled() or agent._task.done()


# ── New tests for health snapshot integration ─────────────────────────────────


async def test_reflect_writes_structured_snapshot():
    """_reflect calls add_health_snapshot on procedural memory with valid JSON payload."""
    import json as _json

    reflection_json = _make_reflection_response()
    memory = _make_memory_layer()
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=_json.loads(reflection_json))
    ):
        await agent._reflect()

    memory.procedural.add_health_snapshot.assert_called_once()
    call_args = memory.procedural.add_health_snapshot.call_args[0]
    timestamp_arg = call_args[0]
    payload_arg = call_args[1]
    # timestamp should be ISO format
    assert "T" in timestamp_arg
    # payload should be valid JSON
    parsed = _json.loads(payload_arg)
    assert "anomalies_found" in parsed
    assert "memory_episodic_count" in parsed


async def test_reflect_preserves_chromadb_write():
    """_reflect still calls episodic.add_event with health_check metadata."""
    import json as _json

    reflection_json = _make_reflection_response()
    memory = _make_memory_layer()
    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=_json.loads(reflection_json))
    ):
        await agent._reflect()

    # episodic.add_event must be called with type=health_check metadata
    memory.episodic.add_event.assert_called()
    call_kwargs = memory.episodic.add_event.call_args.kwargs
    metadata = call_kwargs.get("metadata") or {}
    assert metadata.get("type") == "health_check"
    assert "integrations_healthy" in metadata
    assert "integrations_total" in metadata
    assert "anomalies_found" in metadata


async def test_reflect_sends_morning_summary():
    """_reflect sends a German morning summary as the last Matrix.send call."""
    import json as _json

    reflection_json = _make_reflection_response()
    memory = _make_memory_layer()
    matrix = _make_matrix()
    agent = ReflectionAgent(memory, _make_hal(), matrix, _make_config())

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=_json.loads(reflection_json))
    ):
        await agent._reflect()

    # Morning summary must be the last send call
    last_call = matrix.send.call_args_list[-1]
    msg = last_call[0][1]
    assert "Guten Morgen" in msg
    assert "reflektiert" in msg


async def test_reflect_computes_delta():
    """_reflect computes a delta when a previous snapshot exists."""
    import json as _json

    from src.background.health_snapshot import HealthSnapshot

    # Build a minimal previous snapshot
    prev_snapshot = HealthSnapshot(
        timestamp="2026-04-05T03:00:00",
        platform_health={},
        memory_episodic_count=80,
        memory_semantic_edges=40,
        memory_procedural_prefs=10,
        corrections_count_7d=2,
        repeated_corrections=[],
        active_routines=3,
        candidate_routines=1,
        routine_fires_success=5,
        routine_fires_failure=0,
        pattern_detector_hits=0,
        top_rooms=[],
        top_devices=[],
        anomalies_found=0,
        anomalies_high=0,
        anomalies_medium=0,
        anomalies_low=0,
        energy_insights=[],
        behavioral_changes=[],
        free_text_suggestion="",
    )
    prev_payload = prev_snapshot.model_dump_json()

    reflection_json = _make_reflection_response()
    memory = _make_memory_layer()
    # Return 2 rows: current (just inserted) + previous
    current_row = {"id": 2, "timestamp": "2026-04-12T03:00:00", "payload": prev_payload}
    prev_row = {"id": 1, "timestamp": "2026-04-05T03:00:00", "payload": prev_payload}
    memory.procedural.get_latest_health_snapshot = AsyncMock(return_value=[current_row, prev_row])

    matrix = _make_matrix()
    agent = ReflectionAgent(memory, _make_hal(), matrix, _make_config())

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=_json.loads(reflection_json))
    ):
        await agent._reflect()

    # Morning summary should still be sent
    assert matrix.send.called


async def test_gather_snapshot_data_populates_all_fields():
    """_gather_snapshot_data returns a HealthSnapshot with values from memory stores."""
    memory = _make_memory_layer()
    memory.episodic.count_events = AsyncMock(return_value=42)
    memory.episodic.count_recent_corrections = AsyncMock(return_value=5)
    memory.episodic.get_repeated_corrections = AsyncMock(
        return_value=[{"original": "a", "correction": "b", "count": 2}]
    )
    memory.semantic.edge_count = MagicMock(return_value=10)
    memory.procedural.count_preferences = AsyncMock(return_value=7)
    memory.procedural.get_routines = AsyncMock(
        return_value=[
            {"enabled": 1, "confirmed": 1, "created_at": "2026-01-01"},
            {"enabled": 1, "confirmed": 0, "created_at": "2026-04-12T02:00:00"},
        ]
    )
    memory.procedural.get_recent_routine_stats = AsyncMock(
        return_value={"success": 8, "failure": 1}
    )

    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())

    health = {"gira_x1": True, "frigate": False}
    analysis = {
        "anomalies": [{"severity": "high", "description": "test"}],
        "energy_insights": ["Insight 1"],
        "behavioral_changes": ["Change 1"],
    }
    snapshot = await agent._gather_snapshot_data(health, analysis)

    assert snapshot.memory_episodic_count == 42
    assert snapshot.memory_semantic_edges == 10
    assert snapshot.memory_procedural_prefs == 7
    assert snapshot.corrections_count_7d == 5
    assert len(snapshot.repeated_corrections) == 1
    assert snapshot.active_routines == 1
    assert snapshot.candidate_routines == 1
    assert snapshot.routine_fires_success == 8
    assert snapshot.routine_fires_failure == 1
    assert snapshot.anomalies_found == 1
    assert snapshot.anomalies_high == 1
    assert snapshot.free_text_suggestion == "Insight 1"


async def test_gather_snapshot_data_tolerates_query_failure():
    """_gather_snapshot_data returns default value when one query fails."""
    memory = _make_memory_layer()
    # Make count_events raise an exception
    from unittest.mock import AsyncMock as AM2

    memory.episodic.count_events = AM2(side_effect=RuntimeError("ChromaDB down"))

    agent = ReflectionAgent(memory, _make_hal(), _make_matrix(), _make_config())
    health = {}
    analysis = {"anomalies": [], "energy_insights": [], "behavioral_changes": []}

    # Should not raise — count_events failure produces default 0
    snapshot = await agent._gather_snapshot_data(health, analysis)
    assert snapshot.memory_episodic_count == 0
    # Other fields are still populated normally
    assert snapshot.memory_procedural_prefs == 0  # from mock returning 0


async def test_morning_summary_skipped_without_room_id():
    """No Matrix.send when room_id is empty string."""
    import json as _json

    config = _make_config()
    config.matrix.room_id = ""

    reflection_json = _make_reflection_response()
    memory = _make_memory_layer()
    matrix = _make_matrix()
    agent = ReflectionAgent(memory, _make_hal(), matrix, config)

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=_json.loads(reflection_json))
    ):
        await agent._reflect()

    assert not matrix.send.called


async def test_morning_summary_includes_medium_anomaly_descriptions():
    """Morning summary includes description of medium-severity anomalies."""
    import json as _json

    anomalies = [
        {
            "severity": "medium",
            "description": "Erhoehter Energieverbrauch im Wohnzimmer",
            "category": "energy",
        }
    ]
    reflection_json = _make_reflection_response(anomalies=anomalies)
    memory = _make_memory_layer()
    matrix = _make_matrix()
    agent = ReflectionAgent(memory, _make_hal(), matrix, _make_config())

    with patch.object(
        agent, "_call_reflection_tier", AsyncMock(return_value=_json.loads(reflection_json))
    ):
        await agent._reflect()

    # Morning summary (last send) should contain the medium anomaly description
    last_msg = matrix.send.call_args_list[-1][0][1]
    assert "Erhoehter Energieverbrauch im Wohnzimmer" in last_msg
    assert "mittel" in last_msg
