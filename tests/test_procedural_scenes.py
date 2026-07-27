"""Tests for ProceduralMemory scene CRUD methods."""

from __future__ import annotations

import pytest

from src.memory.procedural import ProceduralMemory


@pytest.fixture
async def memory(tmp_path):
    """Open a fresh memory store for each test."""
    db_path = tmp_path / "test_brain.db"
    mem = ProceduralMemory(db_path)
    await mem.open()
    yield mem
    await mem.close()


async def test_add_and_get_all_scenes(memory):
    """add_scene stores records that get_all_scenes returns."""
    await memory.add_scene(
        "Bettfertig", "Vor dem Schlafen", '[{"room": "Schlafzimmer", "action": "lights_off"}]'
    )
    await memory.add_scene(
        "Filmabend", "Film schauen", '[{"room": "Wohnzimmer", "action": "lights_dim"}]'
    )
    scenes = await memory.get_all_scenes()
    assert len(scenes) == 2
    names = [s["name"] for s in scenes]
    assert "Bettfertig" in names
    assert "Filmabend" in names


async def test_get_scene_by_name_exact(memory):
    """Exact case-insensitive match returns single-item list."""
    await memory.add_scene(
        "Bettfertig", "Schlafen gehen", '[{"room": "Schlafzimmer", "action": "lights_off"}]'
    )
    result = await memory.get_scene_by_name("bettfertig")
    assert len(result) == 1
    assert result[0]["name"] == "Bettfertig"


async def test_get_scene_by_name_partial(memory):
    """Partial match returns single-item list when unambiguous."""
    await memory.add_scene(
        "Filmabend", "Film schauen", '[{"room": "Wohnzimmer", "action": "lights_dim"}]'
    )
    result = await memory.get_scene_by_name("film")
    assert len(result) == 1
    assert result[0]["name"] == "Filmabend"


async def test_get_scene_by_name_ambiguous(memory):
    """Ambiguous partial match returns multi-item list."""
    await memory.add_scene(
        "Filmabend", "Film schauen", '[{"room": "Wohnzimmer", "action": "lights_dim"}]'
    )
    await memory.add_scene(
        "Filmstunde", "Kurzer Film", '[{"room": "Kino", "action": "lights_off"}]'
    )
    result = await memory.get_scene_by_name("Film")
    assert len(result) == 2
    names = {s["name"] for s in result}
    assert names == {"Filmabend", "Filmstunde"}


async def test_get_scene_by_name_no_match(memory):
    """No match returns empty list."""
    result = await memory.get_scene_by_name("xyz_nicht_gefunden")
    assert result == []


async def test_exact_takes_priority_over_partial(memory):
    """Exact match for 'Bett' returns only Bett, not Bettfertig."""
    await memory.add_scene(
        "Bett", "Ins Bett gehen", '[{"room": "Schlafzimmer", "action": "lights_off"}]'
    )
    await memory.add_scene(
        "Bettfertig", "Alles fuer die Nacht", '[{"room": "Flur", "action": "lights_off"}]'
    )
    result = await memory.get_scene_by_name("Bett")
    assert len(result) == 1
    assert result[0]["name"] == "Bett"


async def test_update_scene_name_and_description(memory):
    """update_scene changes the specified fields."""
    scene_id = await memory.add_scene(
        "AlteName", "Alte Beschreibung", '[{"room": "Wohnzimmer", "action": "lights_off"}]'
    )
    updated = await memory.update_scene(scene_id, name="NeuerName", description="Neue Beschreibung")
    assert updated is True
    result = await memory.get_scene_by_name("NeuerName")
    assert len(result) == 1
    assert result[0]["description"] == "Neue Beschreibung"


async def test_delete_scene(memory):
    """delete_scene removes the record and returns True."""
    scene_id = await memory.add_scene(
        "Loeschtest", "Wird geloescht", '[{"room": "Wohnzimmer", "action": "lights_off"}]'
    )
    deleted = await memory.delete_scene(scene_id)
    assert deleted is True
    scenes = await memory.get_all_scenes()
    assert scenes == []


async def test_delete_scene_nonexistent(memory):
    """delete_scene returns False for nonexistent id."""
    deleted = await memory.delete_scene(9999)
    assert deleted is False


async def test_actions_json_stored_and_parsed(memory):
    """Actions are stored as JSON string and parsed back to list on retrieval."""
    actions = '[{"room": "Wohnzimmer", "action": "lights_off"}]'
    await memory.add_scene("JsonTest", "Testszene", actions)
    result = await memory.get_scene_by_name("JsonTest")
    assert len(result) == 1
    assert isinstance(result[0]["actions"], list)
    assert result[0]["actions"][0]["room"] == "Wohnzimmer"
    assert result[0]["actions"][0]["action"] == "lights_off"
