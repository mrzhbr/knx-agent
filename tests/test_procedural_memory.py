from __future__ import annotations

import pytest

from src.memory.procedural import ProceduralMemory


@pytest.fixture
async def memory(tmp_path):
    """Open a fresh in-memory-like memory store for each test."""
    db_path = tmp_path / "test_brain.db"
    mem = ProceduralMemory(db_path)
    await mem.open()
    yield mem
    await mem.close()


async def test_open_creates_tables(memory):
    """Opening the store creates the required tables without errors."""
    # If we got here, open() succeeded
    prefs = await memory.get_preferences()
    assert prefs == []


async def test_add_and_search_preference(memory):
    """add_preference stores a fact that search_preferences can find."""
    await memory.add_preference("Max mag das Licht im Büro bei 60%", source="user")
    results = await memory.search_preferences("Büro")
    assert len(results) == 1
    assert "Büro" in results[0]["fact"]
    assert results[0]["source"] == "user"


async def test_search_returns_empty_for_no_match(memory):
    """search_preferences returns empty list if no match."""
    await memory.add_preference("Irgendwas anderes")
    results = await memory.search_preferences("xyz_no_match")
    assert results == []


async def test_get_preferences_returns_recent_first(memory):
    """get_preferences returns items newest-first."""
    await memory.add_preference("Erster Fakt")
    await memory.add_preference("Zweiter Fakt")
    prefs = await memory.get_preferences()
    assert len(prefs) == 2
    assert prefs[0]["fact"] == "Zweiter Fakt"


async def test_set_and_get_device_knowledge(memory):
    """Device knowledge can be stored and retrieved by UID."""
    await memory.set_device_knowledge(
        uid="abc123",
        room="Wohnzimmer",
        device_name="Deckenlampe",
        description="Die Hauptlampe im Wohnzimmer",
        location_hint="Mitte der Decke",
        confirmed=True,
    )
    result = await memory.get_device_knowledge("abc123")
    assert result is not None
    assert result["device_name"] == "Deckenlampe"
    assert result["room"] == "Wohnzimmer"
    assert result["confirmed"] == 1


async def test_get_device_knowledge_missing(memory):
    """Returns None for unknown UID."""
    result = await memory.get_device_knowledge("nonexistent")
    assert result is None


async def test_get_unknown_devices_returns_unconfirmed(memory):
    """get_unknown_devices returns devices with confirmed=0."""
    await memory.set_device_knowledge(
        uid="uid1", room="Küche", device_name="Licht", description="", confirmed=False
    )
    await memory.set_device_knowledge(
        uid="uid2", room="Büro", device_name="Steckdose", description="Beschrieben", confirmed=True
    )
    unknown = await memory.get_unknown_devices()
    uids = [d["uid"] for d in unknown]
    assert "uid1" in uids
    assert "uid2" not in uids


async def test_store_and_get_routines(memory):
    """Routines can be stored and retrieved."""
    routine_id = await memory.store_candidate_routine(
        name="Abend-Routine",
        description="Licht dimmen und Jalousien schließen",
        trigger="Sonnenuntergang",
        actions=[{"tool": "lights_control", "room": "Wohnzimmer", "brightness": 30}],
        confidence=0.8,
    )
    assert routine_id > 0
    routines = await memory.get_routines()
    assert len(routines) == 1
    assert routines[0]["name"] == "Abend-Routine"
    assert isinstance(routines[0]["actions"], list)


async def test_update_routine(memory):
    """Routine fields can be updated."""
    rid = await memory.store_candidate_routine(
        name="Test", description="", trigger="", actions=[], confidence=0.5
    )
    await memory.update_routine(rid, confirmed=True, confidence=0.9)
    routines = await memory.get_routines()
    assert routines[0]["confirmed"] == 1
    assert abs(routines[0]["confidence"] - 0.9) < 0.001


async def test_log_state_change(memory):
    """State changes are logged without errors."""
    await memory.log_state_change(
        uid="dp001",
        new_value="true",
        old_value="false",
        room="Wohnzimmer",
        device="Licht",
        source="gira_x1",
    )
    # No assertion needed — just verifying no exception


async def test_require_db_raises_if_not_open():
    """_require_db raises RuntimeError if db not opened."""
    mem = ProceduralMemory("/tmp/never_opened.db")
    with pytest.raises(RuntimeError, match="not open"):
        mem._require_db()


async def test_store_routine_with_cron_expr(memory):
    """store_candidate_routine persists cron_expr and get_routines returns it."""
    rid = await memory.store_candidate_routine(
        name="Jalousien abend",
        description="Jalousien taeglich um 19:05 schliessen",
        trigger="",
        actions=[{"tool": "blinds_control", "args": {"room": "Zimmer 1", "action": "down"}}],
        trigger_type="time",
        trigger_value="19:05",
        autonomy_level=2,
        cron_expr="5 19 * * *",
    )
    assert isinstance(rid, int)

    routines = await memory.get_routines(enabled_only=False)
    found = next((r for r in routines if r["id"] == rid), None)
    assert found is not None
    assert found["cron_expr"] == "5 19 * * *"
    assert found["trigger_type"] == "time"
    assert found["trigger_value"] == "19:05"
