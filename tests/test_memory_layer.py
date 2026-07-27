"""Tests for MemoryLayer — unified interface over all three memory stores."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.memory.layer import MemoryLayer


# ─── Fixtures ──────────────────────────────────────────────────────────────


def _make_mock_episodic(similar="", corrections=""):
    """Build a mock EpisodicMemory."""
    ep = MagicMock()
    ep.close = AsyncMock()
    ep.format_similar = AsyncMock(return_value=similar)
    ep.format_recent_corrections = AsyncMock(return_value=corrections)
    ep.add_event = AsyncMock()
    ep.add_correction = AsyncMock()
    ep.search = AsyncMock(return_value=[])
    ep.search_corrections = AsyncMock(return_value=[])
    return ep


def _make_mock_semantic(prefs=""):
    """Build a mock SemanticMemory."""
    sem = MagicMock()
    sem.open = AsyncMock()
    sem.close = AsyncMock()
    sem.format_preferences = AsyncMock(return_value=prefs)
    sem.add_edge = AsyncMock()
    sem.get_preferences = AsyncMock(return_value={})
    return sem


def _make_mock_procedural(routines=None):
    """Build a mock ProceduralMemory."""
    proc = MagicMock()
    proc.open = AsyncMock()
    proc.close = AsyncMock()
    proc.get_routines = AsyncMock(return_value=routines or [])
    proc.log_state_change = AsyncMock()
    proc.add_preference = AsyncMock()
    proc.search_preferences = AsyncMock(return_value=[])
    proc._db = MagicMock()
    return proc


@pytest.fixture
def mock_layer(tmp_path):
    """Return a MemoryLayer with mocked stores."""
    layer = MemoryLayer(
        db_path=str(tmp_path / "test.db"),
        chromadb_path=str(tmp_path / "chromadb"),
        ollama_host="http://localhost:11434",
    )
    return layer


# ─── Tests: open() / close() lifecycle ────────────────────────────────────


async def test_open_opens_all_stores(mock_layer, monkeypatch):
    """open() opens procedural, semantic, and episodic stores."""
    mock_proc = _make_mock_procedural()
    mock_sem = _make_mock_semantic()
    mock_ep = _make_mock_episodic()

    mock_layer.procedural = mock_proc
    mock_layer.semantic = mock_sem

    with patch("src.memory.layer.EpisodicMemory") as MockEpisodic:
        MockEpisodic.open = AsyncMock(return_value=mock_ep)
        await mock_layer.open()

    mock_proc.open.assert_awaited_once()
    mock_sem.open.assert_awaited_once()
    MockEpisodic.open.assert_awaited_once()


async def test_open_handles_episodic_failure(mock_layer):
    """open() continues if EpisodicMemory fails to open."""
    mock_proc = _make_mock_procedural()
    mock_sem = _make_mock_semantic()

    mock_layer.procedural = mock_proc
    mock_layer.semantic = mock_sem

    with patch("src.memory.layer.EpisodicMemory") as MockEpisodic:
        MockEpisodic.open = AsyncMock(side_effect=RuntimeError("ChromaDB unavailable"))
        await mock_layer.open()  # Must not raise

    assert mock_layer.episodic is None


async def test_close_closes_all_stores(mock_layer):
    """close() closes all three stores."""
    mock_proc = _make_mock_procedural()
    mock_sem = _make_mock_semantic()
    mock_ep = _make_mock_episodic()

    mock_layer.procedural = mock_proc
    mock_layer.semantic = mock_sem
    mock_layer.episodic = mock_ep

    await mock_layer.close()

    mock_ep.close.assert_awaited_once()
    mock_sem.close.assert_awaited_once()
    mock_proc.close.assert_awaited_once()


async def test_close_works_without_episodic(mock_layer):
    """close() works when episodic is None (open failed)."""
    mock_proc = _make_mock_procedural()
    mock_sem = _make_mock_semantic()

    mock_layer.procedural = mock_proc
    mock_layer.semantic = mock_sem
    mock_layer.episodic = None

    await mock_layer.close()  # Must not raise
    mock_proc.close.assert_awaited_once()


# ─── Tests: build_context() ───────────────────────────────────────────────


async def test_build_context_returns_string_with_all_sections(mock_layer):
    """build_context() assembles sections from all stores."""
    mock_layer.episodic = _make_mock_episodic(
        similar="Ähnliche Situationen:\n- Licht an",
        corrections="Letzte Korrekturen:\n- 'aus' → 'ein'",
    )
    mock_layer.semantic = _make_mock_semantic(prefs="Präferenzen von Max:\n- bevorzugt: 60%")
    mock_layer.procedural = _make_mock_procedural(
        routines=[{"name": "Bettfertig", "description": "Lichter aus", "trigger": "22:00"}]
    )

    result = await mock_layer.build_context(user="Max", intent="Licht einschalten")

    assert "Ähnliche Situationen" in result
    assert "Präferenzen von Max" in result
    assert "Meine aktiven Routinen" in result
    assert "Bettfertig" in result
    assert "Letzte Korrekturen" in result


async def test_build_context_empty_when_no_data(mock_layer):
    """build_context() returns empty string when all stores return empty."""
    mock_layer.episodic = _make_mock_episodic(similar="", corrections="")
    mock_layer.semantic = _make_mock_semantic(prefs="")
    mock_layer.procedural = _make_mock_procedural(routines=[])

    result = await mock_layer.build_context(user="Max", intent="test")
    assert result == ""


async def test_build_context_skips_empty_sections(mock_layer):
    """build_context() omits sections with no data."""
    mock_layer.episodic = None  # No episodic
    mock_layer.semantic = _make_mock_semantic(prefs="Präferenzen von Max:\n- bevorzugt: 60%")
    mock_layer.procedural = _make_mock_procedural(routines=[])

    result = await mock_layer.build_context(user="Max", intent="test")
    assert "Präferenzen" in result
    # Routines and episodic sections absent
    assert "Routinen" not in result


async def test_build_context_routines_formatted_correctly(mock_layer):
    """build_context() formats routines with name: description (trigger)."""
    mock_layer.episodic = None
    mock_layer.semantic = _make_mock_semantic(prefs="")
    mock_layer.procedural = _make_mock_procedural(
        routines=[
            {"name": "Morgens", "description": "Rollläden hoch", "trigger": "07:00"},
        ]
    )

    result = await mock_layer.build_context(user="Max", intent="test")
    assert "Meine aktiven Routinen:" in result
    assert "Morgens: Rollläden hoch (07:00)" in result


async def test_build_context_handles_store_exceptions(mock_layer):
    """build_context() continues if a store raises an exception."""
    mock_ep = _make_mock_episodic()
    mock_ep.format_similar = AsyncMock(side_effect=RuntimeError("DB error"))
    mock_layer.episodic = mock_ep
    mock_layer.semantic = _make_mock_semantic(prefs="Präferenzen:\n- test")
    mock_layer.procedural = _make_mock_procedural(routines=[])

    result = await mock_layer.build_context(user="Max", intent="test")
    # Should still have semantic section, not crash
    assert "Präferenzen" in result


# ─── Tests: log_interaction() ─────────────────────────────────────────────


async def test_log_interaction_writes_to_episodic(mock_layer):
    """log_interaction() adds event to episodic store."""
    mock_ep = _make_mock_episodic()
    mock_layer.episodic = mock_ep
    mock_layer.semantic = _make_mock_semantic()
    mock_layer.procedural = _make_mock_procedural()

    await mock_layer.log_interaction("Max", "Licht an", "Licht eingeschaltet.")
    mock_ep.add_event.assert_awaited_once()


async def test_log_interaction_works_with_tool_calls_none(mock_layer):
    """log_interaction() accepts tool_calls=None (default)."""
    mock_ep = _make_mock_episodic()
    mock_layer.episodic = mock_ep
    mock_layer.semantic = _make_mock_semantic()
    mock_layer.procedural = _make_mock_procedural()

    await mock_layer.log_interaction("Max", "test", "response", tool_calls=None)
    mock_ep.add_event.assert_awaited_once()


async def test_log_interaction_without_episodic(mock_layer):
    """log_interaction() works when episodic is None."""
    mock_layer.episodic = None
    mock_layer.semantic = _make_mock_semantic()
    mock_layer.procedural = _make_mock_procedural()

    await mock_layer.log_interaction("Max", "test", "response")  # Must not raise


async def test_log_interaction_writes_to_procedural(mock_layer):
    """log_interaction() logs state change to procedural store."""
    mock_layer.episodic = None
    mock_layer.semantic = _make_mock_semantic()
    mock_proc = _make_mock_procedural()
    mock_layer.procedural = mock_proc

    await mock_layer.log_interaction("Max", "Licht an", "OK")
    mock_proc.log_state_change.assert_awaited_once()


# ─── Tests: parallel build_context() ──────────────────────────────────────


async def test_build_context_runs_concurrently(mock_layer):
    """build_context() runs all 4 helpers concurrently — total time < 100ms even with 50ms delays."""
    import asyncio
    import time

    async def slow_similar(*args, **kwargs):
        await asyncio.sleep(0.05)
        return "Ähnliche: test"

    async def slow_corrections(*args, **kwargs):
        await asyncio.sleep(0.05)
        return "Korrekturen: test"

    async def slow_prefs(*args, **kwargs):
        await asyncio.sleep(0.05)
        return ""

    async def slow_routines(*args, **kwargs):
        await asyncio.sleep(0.05)
        return []

    mock_ep = _make_mock_episodic()
    mock_ep.format_similar = AsyncMock(side_effect=slow_similar)
    mock_ep.format_recent_corrections = AsyncMock(side_effect=slow_corrections)
    mock_layer.episodic = mock_ep
    mock_layer.semantic = _make_mock_semantic()
    mock_layer.semantic.format_preferences = AsyncMock(side_effect=slow_prefs)
    mock_layer.procedural = _make_mock_procedural()
    mock_layer.procedural.get_routines = AsyncMock(side_effect=slow_routines)

    start = time.monotonic()
    result = await mock_layer.build_context(user="Max", intent="test")
    elapsed = time.monotonic() - start

    assert elapsed < 0.10, f"Expected < 0.1s (parallel), got {elapsed:.3f}s"
    assert "Ähnliche" in result
    assert "Korrekturen" in result


async def test_build_context_partial_failure_parallel(mock_layer):
    """build_context() returns other sections even if episodic.format_similar raises."""
    mock_ep = _make_mock_episodic()
    mock_ep.format_similar = AsyncMock(side_effect=RuntimeError("Ollama down"))
    mock_ep.format_recent_corrections = AsyncMock(return_value="Letzte Korrekturen:\n- test")
    mock_layer.episodic = mock_ep
    mock_layer.semantic = _make_mock_semantic(prefs="Präferenzen von Max:\n- Helligkeit 60%")
    mock_layer.procedural = _make_mock_procedural(routines=[])

    result = await mock_layer.build_context(user="Max", intent="test")
    assert "Präferenzen" in result
    assert "Korrekturen" in result


async def test_build_context_episodic_none(mock_layer):
    """build_context() works correctly when self.episodic is None (Ollama offline)."""
    mock_layer.episodic = None
    mock_layer.semantic = _make_mock_semantic(prefs="Präferenzen von Max:\n- bevorzugt: dunkel")
    mock_layer.procedural = _make_mock_procedural(
        routines=[{"name": "Bettfertig", "description": "Lichter aus", "trigger": "22:00"}]
    )

    result = await mock_layer.build_context(user="Max", intent="test")
    assert result  # Must not be empty
    assert "Präferenzen" in result
    assert "Bettfertig" in result
    # No episodic sections
    assert "Ähnliche" not in result
    assert "Korrekturen" not in result
