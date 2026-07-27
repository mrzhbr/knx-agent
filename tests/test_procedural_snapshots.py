"""Tests for ProceduralMemory health snapshot methods and query helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from src.memory.procedural import ProceduralMemory


@pytest.fixture
async def memory(tmp_path):
    """Open a fresh ProceduralMemory for each test."""
    db_path = tmp_path / "test_brain.db"
    mem = ProceduralMemory(db_path)
    await mem.open()
    yield mem
    await mem.close()


# ─── health_snapshots table ────────────────────────────────────────────────────


async def test_health_snapshots_table_created(memory):
    """health_snapshots table is created on open()."""
    db = memory._db
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='health_snapshots'"
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None


async def test_idx_health_snapshots_ts_exists(memory):
    """idx_health_snapshots_ts index exists."""
    db = memory._db
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_health_snapshots_ts'"
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None


async def test_idx_routine_executions_ts_exists(memory):
    """idx_routine_executions_ts index exists."""
    db = memory._db
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_routine_executions_ts'"
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None


# ─── add_health_snapshot ──────────────────────────────────────────────────────


async def test_add_health_snapshot_returns_id(memory):
    """add_health_snapshot inserts a row and returns its id."""
    ts = datetime.now().isoformat()
    row_id = await memory.add_health_snapshot(ts, '{"test": true}')
    assert isinstance(row_id, int)
    assert row_id > 0


async def test_add_health_snapshot_stores_data(memory):
    """add_health_snapshot data can be retrieved."""
    ts = "2026-04-12T03:00:00"
    payload = json.dumps({"memory_episodic_count": 42})
    await memory.add_health_snapshot(ts, payload)
    rows = await memory.get_latest_health_snapshot(n=1)
    assert len(rows) == 1
    assert rows[0]["timestamp"] == ts
    assert json.loads(rows[0]["payload"])["memory_episodic_count"] == 42


# ─── get_latest_health_snapshot ───────────────────────────────────────────────


async def test_get_latest_health_snapshot_returns_latest(memory):
    """get_latest_health_snapshot(n=1) returns the most recent snapshot."""
    await memory.add_health_snapshot("2026-04-10T03:00:00", '{"v": 1}')
    await memory.add_health_snapshot("2026-04-12T03:00:00", '{"v": 2}')
    rows = await memory.get_latest_health_snapshot(n=1)
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"])["v"] == 2


async def test_get_latest_health_snapshot_returns_two(memory):
    """get_latest_health_snapshot(n=2) returns last two ordered by timestamp DESC."""
    await memory.add_health_snapshot("2026-04-10T03:00:00", '{"v": 1}')
    await memory.add_health_snapshot("2026-04-11T03:00:00", '{"v": 2}')
    await memory.add_health_snapshot("2026-04-12T03:00:00", '{"v": 3}')
    rows = await memory.get_latest_health_snapshot(n=2)
    assert len(rows) == 2
    assert json.loads(rows[0]["payload"])["v"] == 3
    assert json.loads(rows[1]["payload"])["v"] == 2


async def test_get_latest_health_snapshot_empty(memory):
    """Returns empty list when no snapshots."""
    rows = await memory.get_latest_health_snapshot()
    assert rows == []


# ─── prune_health_snapshots ───────────────────────────────────────────────────


async def test_prune_health_snapshots_deletes_old(memory):
    """prune_health_snapshots deletes snapshots older than max_age_days."""
    old_ts = (datetime.now() - timedelta(days=100)).isoformat()
    recent_ts = datetime.now().isoformat()
    await memory.add_health_snapshot(old_ts, '{"old": true}')
    await memory.add_health_snapshot(recent_ts, '{"recent": true}')
    deleted = await memory.prune_health_snapshots(max_age_days=90)
    assert deleted == 1
    rows = await memory.get_latest_health_snapshot(n=10)
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"])["recent"] is True


async def test_prune_health_snapshots_keeps_recent(memory):
    """prune_health_snapshots does not delete recent snapshots."""
    recent_ts = datetime.now().isoformat()
    await memory.add_health_snapshot(recent_ts, '{"recent": true}')
    deleted = await memory.prune_health_snapshots(max_age_days=90)
    assert deleted == 0


# ─── get_top_rooms ───────────────────────────────────────────────────────────


async def test_get_top_rooms_returns_grouping(memory):
    """get_top_rooms groups by room and counts correctly."""
    db = memory._db
    ts = datetime.now().isoformat()
    # Insert state_changes with room populated
    for _ in range(3):
        await db.execute(
            "INSERT INTO state_changes (timestamp, uid, room, device, new_value) VALUES (?, ?, ?, ?, ?)",
            (ts, "uid1", "Schlafzimmer", "Deckenlampe", "1"),
        )
    for _ in range(2):
        await db.execute(
            "INSERT INTO state_changes (timestamp, uid, room, device, new_value) VALUES (?, ?, ?, ?, ?)",
            (ts, "uid2", "Buero", "Deckenlampe", "1"),
        )
    await db.commit()
    rooms = await memory.get_top_rooms(days=7, limit=5)
    assert len(rooms) == 2
    assert rooms[0]["room"] == "Schlafzimmer"
    assert rooms[0]["count"] == 3
    assert rooms[1]["room"] == "Buero"
    assert rooms[1]["count"] == 2


async def test_get_top_rooms_returns_empty_when_room_null(memory):
    """get_top_rooms returns empty list when all state_changes have room=NULL."""
    db = memory._db
    ts = datetime.now().isoformat()
    await db.execute(
        "INSERT INTO state_changes (timestamp, uid, room, device, new_value) VALUES (?, ?, ?, ?, ?)",
        (ts, "uid1", None, "Deckenlampe", "1"),
    )
    await db.commit()
    rooms = await memory.get_top_rooms()
    assert rooms == []


# ─── get_top_devices ──────────────────────────────────────────────────────────


async def test_get_top_devices_returns_grouping(memory):
    """get_top_devices groups by device and counts correctly."""
    db = memory._db
    ts = datetime.now().isoformat()
    for _ in range(4):
        await db.execute(
            "INSERT INTO state_changes (timestamp, uid, room, device, new_value) VALUES (?, ?, ?, ?, ?)",
            (ts, "uid1", None, "Kuehlschrank", "1"),
        )
    await db.execute(
        "INSERT INTO state_changes (timestamp, uid, room, device, new_value) VALUES (?, ?, ?, ?, ?)",
        (ts, "uid2", None, "Lampe", "1"),
    )
    await db.commit()
    devices = await memory.get_top_devices(days=7, limit=5)
    assert len(devices) == 2
    assert devices[0]["device"] == "Kuehlschrank"
    assert devices[0]["count"] == 4


# ─── get_recent_routine_stats ────────────────────────────────────────────────


async def test_get_recent_routine_stats_returns_counts(memory):
    """get_recent_routine_stats returns success/failure counts."""
    # First create a routine to reference
    routine_id = await memory.store_candidate_routine(
        "Test", "desc", "trigger", [{"action": "test"}]
    )
    ts = datetime.now().isoformat()
    db = memory._db
    await db.execute(
        "INSERT INTO routine_executions (routine_id, executed_at, success) VALUES (?, ?, ?)",
        (routine_id, ts, 1),
    )
    await db.execute(
        "INSERT INTO routine_executions (routine_id, executed_at, success) VALUES (?, ?, ?)",
        (routine_id, ts, 1),
    )
    await db.execute(
        "INSERT INTO routine_executions (routine_id, executed_at, success) VALUES (?, ?, ?)",
        (routine_id, ts, 0),
    )
    await db.commit()
    stats = await memory.get_recent_routine_stats(days=7)
    assert stats["success"] == 2
    assert stats["failure"] == 1


async def test_get_recent_routine_stats_empty(memory):
    """get_recent_routine_stats returns zeros when no executions."""
    stats = await memory.get_recent_routine_stats()
    assert stats == {"success": 0, "failure": 0}


# ─── count_preferences ────────────────────────────────────────────────────────


async def test_count_preferences_returns_correct_count(memory):
    """count_preferences returns the total number of preferences."""
    assert await memory.count_preferences() == 0
    await memory.add_preference("Fact 1")
    await memory.add_preference("Fact 2")
    assert await memory.count_preferences() == 2
