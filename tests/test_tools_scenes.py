"""Tests for src/tools/scenes.py — scene management tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.tools.scenes import create_scene, delete_scene, execute_scene, list_scenes, set_context
from src.tools.context import ToolContext


def _make_ctx(procedural=None):
    """Create a minimal ToolContext for tests."""
    mock_memory = MagicMock()
    mock_memory.procedural = procedural or MagicMock()
    return ToolContext(
        hal=MagicMock(),
        presence_manager=MagicMock(),
        energy_awareness=MagicMock(),
        rule_engine=MagicMock(),
        memory_layer=mock_memory,
        schedule_engine=MagicMock(),
    )


@pytest.fixture(autouse=True)
def reset_ctx():
    """Reset _ctx to None after each test."""
    import src.tools.scenes as scenes_module

    yield
    scenes_module._ctx = None


async def test_create_scene_valid_json():
    """create_scene with valid JSON actions stores in DB and returns confirmation."""
    mock_procedural = MagicMock()
    mock_procedural.add_scene = AsyncMock(return_value=1)
    ctx = _make_ctx(mock_procedural)
    set_context(ctx)

    actions = '[{"room": "Wohnzimmer", "action": "lights_off"}]'
    result = await create_scene("Bettfertig", "Nacht-Routine", actions)
    assert "Bettfertig" in result
    assert "1" in result
    mock_procedural.add_scene.assert_called_once_with("Bettfertig", "Nacht-Routine", actions)


async def test_create_scene_invalid_json():
    """create_scene with invalid JSON returns German error message."""
    ctx = _make_ctx()
    set_context(ctx)

    result = await create_scene("Test", "Beschreibung", "not valid json")
    assert "Ungueltige Aktionen" in result
    assert "JSON-Format erwartet" in result


async def test_create_scene_no_context():
    """create_scene without context returns unavailability message."""
    result = await create_scene("Test", "Desc", '[{"room": "X", "action": "lights_off"}]')
    assert "nicht verfuegbar" in result.lower() or "verfuegbar" in result.lower()


async def test_execute_scene_found():
    """execute_scene with a stored scene calls execute_batch with the scene actions."""
    from unittest.mock import patch

    mock_procedural = MagicMock()
    scene = {
        "id": 1,
        "name": "Bettfertig",
        "description": "Nacht",
        "actions": [{"room": "Wohnzimmer", "action": "lights_off"}],
    }
    mock_procedural.get_scene_by_name = AsyncMock(return_value=[scene])
    ctx = _make_ctx(mock_procedural)
    set_context(ctx)

    # execute_batch is imported lazily inside execute_scene from src.tools.comfort
    with patch("src.tools.comfort.execute_batch", new=AsyncMock(return_value="Licht aus.")):
        result = await execute_scene("Bettfertig")
    assert result == "Licht aus."


async def test_execute_scene_not_found():
    """execute_scene with nonexistent scene returns German not-found message."""
    mock_procedural = MagicMock()
    mock_procedural.get_scene_by_name = AsyncMock(return_value=[])
    ctx = _make_ctx(mock_procedural)
    set_context(ctx)

    result = await execute_scene("NichtVorhanden")
    assert "nicht gefunden" in result
    assert "NichtVorhanden" in result


async def test_execute_scene_ambiguous():
    """execute_scene with ambiguous name returns disambiguation error listing matches."""
    mock_procedural = MagicMock()
    scenes = [
        {"id": 1, "name": "Filmabend", "description": "", "actions": []},
        {"id": 2, "name": "Filmstunde", "description": "", "actions": []},
    ]
    mock_procedural.get_scene_by_name = AsyncMock(return_value=scenes)
    ctx = _make_ctx(mock_procedural)
    set_context(ctx)

    result = await execute_scene("Film")
    assert "Mehrere Szenen gefunden" in result
    assert "Filmabend" in result
    assert "Filmstunde" in result


async def test_list_scenes_empty():
    """list_scenes with no scenes returns 'keine Szenen' message."""
    mock_procedural = MagicMock()
    mock_procedural.get_all_scenes = AsyncMock(return_value=[])
    ctx = _make_ctx(mock_procedural)
    set_context(ctx)

    result = await list_scenes()
    assert "Keine Szenen" in result


async def test_list_scenes_two_scenes():
    """list_scenes with 2 scenes returns both names."""
    mock_procedural = MagicMock()
    mock_procedural.get_all_scenes = AsyncMock(
        return_value=[
            {"id": 1, "name": "Bettfertig", "description": "Nacht"},
            {"id": 2, "name": "Filmabend", "description": "Film"},
        ]
    )
    ctx = _make_ctx(mock_procedural)
    set_context(ctx)

    result = await list_scenes()
    assert "Bettfertig" in result
    assert "Filmabend" in result


async def test_delete_scene_existing():
    """delete_scene with existing scene removes it and confirms deletion."""
    mock_procedural = MagicMock()
    mock_procedural.get_scene_by_name = AsyncMock(
        return_value=[{"id": 1, "name": "Bettfertig", "description": ""}]
    )
    mock_procedural.delete_scene = AsyncMock(return_value=True)
    ctx = _make_ctx(mock_procedural)
    set_context(ctx)

    result = await delete_scene("Bettfertig")
    assert "geloescht" in result
    assert "Bettfertig" in result


async def test_delete_scene_not_found():
    """delete_scene with nonexistent name returns not-found message."""
    mock_procedural = MagicMock()
    mock_procedural.get_scene_by_name = AsyncMock(return_value=[])
    ctx = _make_ctx(mock_procedural)
    set_context(ctx)

    result = await delete_scene("NichtVorhanden")
    assert "nicht gefunden" in result


async def test_delete_scene_ambiguous():
    """delete_scene with ambiguous name returns disambiguation error."""
    mock_procedural = MagicMock()
    mock_procedural.get_scene_by_name = AsyncMock(
        return_value=[
            {"id": 1, "name": "Filmabend", "description": ""},
            {"id": 2, "name": "Filmstunde", "description": ""},
        ]
    )
    ctx = _make_ctx(mock_procedural)
    set_context(ctx)

    result = await delete_scene("Film")
    assert "Mehrere Szenen gefunden" in result
    assert "Filmabend" in result
    assert "Filmstunde" in result
