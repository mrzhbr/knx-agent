from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

import src.tools.memory as memory_module
from src.tools.memory import (
    set_context,
    remember,
    recall,
    recall_preference,
    describe_device,
    get_unknown_devices,
    remember_correction,
)
from src.tools.context import ToolContext


def make_mock_memory():
    """Create a mock ProceduralMemory."""
    mem = MagicMock()
    mem.add_preference = AsyncMock()
    mem.search_preferences = AsyncMock(return_value=[])
    mem.set_device_knowledge = AsyncMock()
    mem.get_unknown_devices = AsyncMock(return_value=[])
    return mem


def _make_ctx(memory=None, hal=None) -> ToolContext:
    return ToolContext(
        hal=hal or MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=memory,
        schedule_engine=MagicMock(),
    )


@pytest.fixture(autouse=True)
def reset_ctx():
    """Reset _ctx after each test."""
    memory_module._ctx = None
    yield
    memory_module._ctx = None


async def test_remember_stores_fact():
    mem = make_mock_memory()
    set_context(_make_ctx(memory=mem))
    result = await remember("Max mag Licht bei 60%")
    mem.add_preference.assert_awaited_once_with("Max mag Licht bei 60%", source="user")
    assert "Gespeichert" in result


async def test_remember_no_memory():
    result = await remember("Fakt ohne Speicher")
    assert "nicht verfügbar" in result


async def test_recall_found():
    mem = make_mock_memory()
    mem.search_preferences = AsyncMock(
        return_value=[
            {"fact": "Max mag Licht bei 60%", "source": "user"},
        ]
    )
    set_context(_make_ctx(memory=mem))
    result = await recall("Licht")
    assert "Gefunden" in result
    assert "Max mag Licht" in result


async def test_recall_not_found():
    mem = make_mock_memory()
    mem.search_preferences = AsyncMock(return_value=[])
    set_context(_make_ctx(memory=mem))
    result = await recall("xyz_no_match")
    assert "Keine" in result


async def test_recall_preference_found():
    mem = make_mock_memory()
    mem.search_preferences = AsyncMock(
        return_value=[
            {"fact": "Person 2 möchte 18°C im Schlafzimmer", "source": "user"},
        ]
    )
    set_context(_make_ctx(memory=mem))
    result = await recall_preference("Temperatur Schlafzimmer")
    assert "Präferenz" in result
    assert "18°C" in result


async def test_recall_preference_not_found():
    mem = make_mock_memory()
    mem.search_preferences = AsyncMock(return_value=[])
    set_context(_make_ctx(memory=mem))
    result = await recall_preference("Heizung Keller")
    assert "Keine Präferenz" in result


async def test_describe_device_saves():
    mem = make_mock_memory()
    set_context(_make_ctx(memory=mem))
    result = await describe_device("Deckenlampe", "Wohnzimmer", "Die Hauptlampe")
    mem.set_device_knowledge.assert_awaited_once()
    assert "beschrieben" in result.lower() or "Deckenlampe" in result


async def test_describe_device_requires_room():
    mem = make_mock_memory()
    set_context(_make_ctx(memory=mem))
    result = await describe_device("Deckenlampe", "", "Irgendwas")
    assert "Raum" in result


async def test_get_unknown_devices_none():
    mem = make_mock_memory()
    mem.get_unknown_devices = AsyncMock(return_value=[])
    set_context(_make_ctx(memory=mem))
    result = await get_unknown_devices()
    assert "Alle" in result


async def test_get_unknown_devices_list():
    mem = make_mock_memory()
    mem.get_unknown_devices = AsyncMock(
        return_value=[
            {"uid": "abc", "device_name": "Schalter", "room": "Küche"},
        ]
    )
    set_context(_make_ctx(memory=mem))
    result = await get_unknown_devices()
    assert "Schalter" in result
    assert "Küche" in result


# ─── New tests for MemoryLayer integration ────────────────────────────────


def make_mock_memory_layer():
    """Create a mock MemoryLayer that is recognized as one."""
    from src.memory.layer import MemoryLayer

    layer = MagicMock(spec=MemoryLayer)
    layer.procedural = make_mock_memory()
    layer.semantic = MagicMock()
    layer.semantic.add_edge = AsyncMock()
    layer.semantic.get_preferences = AsyncMock(return_value={})
    layer.episodic = MagicMock()
    layer.episodic.search = AsyncMock(return_value=[])
    layer.episodic.add_correction = AsyncMock()
    return layer


async def test_remember_with_memory_layer_also_adds_semantic_edge():
    """remember() also adds semantic edge when MemoryLayer provided."""
    layer = make_mock_memory_layer()
    set_context(_make_ctx(memory=layer))
    result = await remember("Max mag Licht bei 60%")
    layer.procedural.add_preference.assert_awaited_once()
    layer.semantic.add_edge.assert_awaited_once()
    assert "Gespeichert" in result


async def test_recall_with_memory_layer_uses_episodic_search():
    """recall() uses episodic vector search when MemoryLayer provided."""
    layer = make_mock_memory_layer()
    layer.episodic.search = AsyncMock(
        return_value=[{"text": "Licht war gestern an", "metadata": {}, "id": "ep-1"}]
    )
    layer.procedural.search_preferences = AsyncMock(return_value=[])
    set_context(_make_ctx(memory=layer))
    result = await recall("Licht")
    layer.episodic.search.assert_awaited_once()
    assert "Licht war gestern an" in result


async def test_recall_preference_with_memory_layer_uses_semantic():
    """recall_preference() uses semantic graph when MemoryLayer provided."""
    layer = make_mock_memory_layer()
    layer.semantic.get_preferences = AsyncMock(
        return_value={"bevorzugt": ["60% Helligkeit im Büro"]}
    )
    layer.procedural.search_preferences = AsyncMock(return_value=[])
    set_context(_make_ctx(memory=layer))
    result = await recall_preference("Büro")
    layer.semantic.get_preferences.assert_awaited_once()
    assert "bevorzugt" in result or "60%" in result


async def test_remember_correction_with_episodic():
    """remember_correction() stores in episodic corrections when available."""
    layer = make_mock_memory_layer()
    set_context(_make_ctx(memory=layer))
    result = await remember_correction("Licht ausschalten", "Licht einschalten")
    layer.episodic.add_correction.assert_awaited_once()
    assert "Korrektur" in result


async def test_remember_correction_no_memory():
    """remember_correction() returns error when no memory."""
    result = await remember_correction("aus", "ein")
    assert "nicht verfügbar" in result


async def test_remember_correction_without_episodic():
    """remember_correction() falls back to procedural when no episodic."""
    mem = make_mock_memory()
    set_context(_make_ctx(memory=mem))  # Plain ProceduralMemory, no episodic
    result = await remember_correction("falsch", "richtig")
    # Should fall back to procedural
    assert "Korrektur" in result
