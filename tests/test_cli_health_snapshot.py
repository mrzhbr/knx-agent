"""Tests for CLI health-snapshot command (src/cli/health_snapshot.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_snapshot_payload(**kwargs) -> str:
    """Build a minimal JSON payload for a health snapshot."""
    defaults = {
        "timestamp": "2026-04-12T03:00:00",
        "platform_health": {"gira_x1": True},
        "memory_episodic_count": 42,
        "memory_semantic_edges": 10,
        "memory_procedural_prefs": 5,
        "corrections_count_7d": 3,
        "repeated_corrections": [],
        "active_routines": 4,
        "candidate_routines": 1,
        "routine_fires_success": 8,
        "routine_fires_failure": 1,
        "pattern_detector_hits": 1,
        "top_rooms": [],
        "top_devices": [],
        "anomalies_found": 0,
        "anomalies_high": 0,
        "anomalies_medium": 0,
        "anomalies_low": 0,
        "energy_insights": [],
        "behavioral_changes": [],
        "free_text_suggestion": "",
    }
    defaults.update(kwargs)
    return json.dumps(defaults)


def _make_mock_memory(rows=None):
    """Return a mocked ProceduralMemory."""
    memory = MagicMock()
    memory.open = AsyncMock()
    memory.close = AsyncMock()
    memory.get_latest_health_snapshot = AsyncMock(return_value=rows or [])
    return memory


# ─── Tests ────────────────────────────────────────────────────────────────────


def test_main_no_snapshot_prints_message(capsys):
    """main([]) prints 'Kein Snapshot vorhanden.' when no snapshots exist."""
    from src.cli.health_snapshot import main

    mock_memory = _make_mock_memory(rows=[])

    with patch("src.cli.health_snapshot.ProceduralMemory", return_value=mock_memory):
        main([])

    captured = capsys.readouterr()
    assert "Kein Snapshot vorhanden." in captured.out


def test_main_with_snapshot_prints_formatted_output(capsys):
    """main([]) with a snapshot prints formatted German text."""
    from src.cli.health_snapshot import main

    payload = _make_snapshot_payload(memory_episodic_count=42)
    row = {"id": 1, "timestamp": "2026-04-12T03:00:00", "payload": payload}
    mock_memory = _make_mock_memory(rows=[row])

    with patch("src.cli.health_snapshot.ProceduralMemory", return_value=mock_memory):
        main([])

    captured = capsys.readouterr()
    assert "Gesundheitsbericht" in captured.out
    assert "2026-04-12" in captured.out


def test_main_json_flag_prints_valid_json(capsys):
    """main(['--json']) prints valid JSON."""
    from src.cli.health_snapshot import main

    payload = _make_snapshot_payload()
    row = {"id": 1, "timestamp": "2026-04-12T03:00:00", "payload": payload}
    mock_memory = _make_mock_memory(rows=[row])

    with patch("src.cli.health_snapshot.ProceduralMemory", return_value=mock_memory):
        main(["--json"])

    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert "snapshot" in parsed


def test_main_custom_db_path():
    """main(['--db', '/tmp/test.db']) uses the provided path."""
    from src.cli.health_snapshot import main

    mock_memory = _make_mock_memory(rows=[])
    captured_paths = []

    def mock_memory_constructor(path):
        captured_paths.append(path)
        return mock_memory

    with patch("src.cli.health_snapshot.ProceduralMemory", side_effect=mock_memory_constructor):
        main(["--db", "/tmp/custom_test.db"])

    assert "/tmp/custom_test.db" in captured_paths


def test_main_close_always_called_on_success():
    """ProceduralMemory.close() is called even on successful run."""
    from src.cli.health_snapshot import main

    mock_memory = _make_mock_memory(rows=[])

    with patch("src.cli.health_snapshot.ProceduralMemory", return_value=mock_memory):
        main([])

    mock_memory.close.assert_called_once()


def test_main_close_called_even_when_snapshot_fails(capsys):
    """ProceduralMemory.close() is called even when get_latest_health_snapshot raises."""
    from src.cli.health_snapshot import main

    mock_memory = _make_mock_memory()
    mock_memory.get_latest_health_snapshot = AsyncMock(side_effect=RuntimeError("DB error"))

    with patch("src.cli.health_snapshot.ProceduralMemory", return_value=mock_memory):
        with pytest.raises(RuntimeError):
            main([])

    # close must always be called
    mock_memory.close.assert_called_once()


def test_main_with_two_snapshots_computes_delta(capsys):
    """main([]) with two snapshots prints delta info."""
    from src.cli.health_snapshot import main

    current_payload = _make_snapshot_payload(memory_episodic_count=100)
    previous_payload = _make_snapshot_payload(
        memory_episodic_count=80,
        timestamp="2026-04-05T03:00:00",
    )
    rows = [
        {"id": 2, "timestamp": "2026-04-12T03:00:00", "payload": current_payload},
        {"id": 1, "timestamp": "2026-04-05T03:00:00", "payload": previous_payload},
    ]
    mock_memory = _make_mock_memory(rows=rows)

    with patch("src.cli.health_snapshot.ProceduralMemory", return_value=mock_memory):
        main([])

    captured = capsys.readouterr()
    # Delta of +20 should appear in output
    assert "+20" in captured.out
