from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from src.hal.models import DeviceId, DeviceInfo, DeviceState, DeviceType
from src.hal.state import StateStore


def _make_device_id(native_id: str = "1") -> DeviceId:
    return DeviceId(platform="mock", native_id=native_id)


def _make_info(device_id: DeviceId, name: str = "Lampe", room: str = "Wohnzimmer") -> DeviceInfo:
    return DeviceInfo(
        id=device_id,
        name=name,
        device_type=DeviceType.LIGHT,
        room=room,
    )


def _make_state(
    device_id: DeviceId,
    state: dict | None = None,
    source: str = "external",
    dt: datetime | None = None,
) -> DeviceState:
    return DeviceState(
        device_id=device_id,
        state=state or {"on": True},
        last_updated=dt or datetime.now(timezone.utc),
        source=source,
    )


async def test_state_store_lifecycle(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    await store.stop()
    assert store._conn is None


async def test_update_inserts_new_state(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    await store.update(_make_state(device_id))
    result = await store.get_state(device_id)
    assert result is not None
    assert result.state["on"] is True
    await store.stop()


async def test_update_upserts_existing(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    await store.update(_make_state(device_id, state={"on": True}))
    await store.update(_make_state(device_id, state={"on": False}))
    result = await store.get_state(device_id)
    assert result is not None
    assert result.state["on"] is False
    await store.stop()


async def test_update_appends_history(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    await store.update(_make_state(device_id, state={"on": True}))
    await store.update(_make_state(device_id, state={"on": False}))
    events = await store.get_recent_events(minutes=60)
    assert len(events) == 1  # Only the change from True -> False
    await store.stop()


async def test_update_no_history_on_first_write(tmp_path: Path) -> None:
    """First write should NOT append event_history (no prior state to diff against)."""
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    await store.update(_make_state(device_id, state={"on": True}))
    events = await store.get_recent_events(minutes=60)
    assert len(events) == 0  # First write does not create history
    await store.stop()


async def test_update_no_history_on_duplicate(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    await store.update(_make_state(device_id, state={"on": True}))
    await store.update(_make_state(device_id, state={"on": True}))
    events = await store.get_recent_events(minutes=60)
    assert len(events) == 0  # No history on duplicate
    await store.stop()


async def test_listener_called_on_first_write(tmp_path: Path) -> None:
    """Listener should fire on first write with old_state=None."""
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)

    received: list[tuple] = []

    async def listener(info, old_state, new_state, source) -> None:
        received.append((info, old_state, new_state, source))

    store.on_change(listener)
    await store.update(_make_state(device_id, state={"on": True}))
    await asyncio.sleep(0)  # Let fire-and-forget tasks complete

    assert len(received) == 1
    assert received[0][1] is None  # old_state is None on first write
    assert received[0][2] == {"on": True}
    await store.stop()


async def test_listener_called_on_change(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)

    received: list[tuple] = []

    async def listener(info, old_state, new_state, source) -> None:
        received.append((info, old_state, new_state, source))

    store.on_change(listener)
    await store.update(_make_state(device_id, state={"on": True}))
    await store.update(_make_state(device_id, state={"on": False}))
    await asyncio.sleep(0)  # Let fire-and-forget tasks complete

    # Fires twice: once for first write (old=None), once for the change
    assert len(received) == 2
    assert received[0][1] is None  # first write: old_state=None
    assert received[0][2] == {"on": True}
    assert received[1][1] == {"on": True}
    assert received[1][2] == {"on": False}
    await store.stop()


async def test_listener_not_called_on_duplicate(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)

    called = []

    async def listener(info, old_state, new_state, source) -> None:
        called.append(1)

    store.on_change(listener)
    await store.update(_make_state(device_id, state={"on": True}))
    await store.update(_make_state(device_id, state={"on": True}))
    await asyncio.sleep(0)  # Let fire-and-forget tasks complete

    # Fires once for first write, not for duplicate
    assert len(called) == 1
    await store.stop()


async def test_listener_source_passed_through(tmp_path: Path) -> None:
    """Listener receives source as 4th argument."""
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)

    sources: list[str] = []

    async def listener(info, old_state, new_state, source) -> None:
        sources.append(source)

    store.on_change(listener)
    await store.update(_make_state(device_id, state={"on": True}, source="external"))
    await asyncio.sleep(0)

    assert sources == ["external"]
    await store.stop()


async def test_mark_brain_action(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    store.mark_brain_action(device_id)
    await store.update(_make_state(device_id, state={"on": True}))
    result = await store.get_state(device_id)
    assert result is not None
    assert result.source == "brain"
    await store.stop()


async def test_get_state_returns_none_for_unknown(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("unknown")
    result = await store.get_state(device_id)
    assert result is None
    await store.stop()


async def test_get_recent_events(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)
    await store.update(_make_state(device_id, state={"on": True}))
    await store.update(_make_state(device_id, state={"on": False}))
    events = await store.get_recent_events(minutes=60)
    assert len(events) == 1
    assert events[0]["new_state"] == {"on": False}
    assert events[0]["old_state"] == {"on": True}
    await store.stop()


async def test_get_full_state_summary(tmp_path: Path) -> None:
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    dev1 = _make_device_id("1")
    dev2 = _make_device_id("2")
    await store.update(_make_state(dev1, state={"on": True}))
    await store.update(_make_state(dev2, state={"on": False}))
    summary = await store.get_full_state_summary()
    assert len(summary) == 2
    assert summary[dev1.key]["on"] is True
    assert summary[dev2.key]["on"] is False
    await store.stop()


# --- Tests for StateStore.prune() (Task 2.2) ---


async def test_prune_deletes_old_events_and_returns_count(tmp_path: Path) -> None:
    """prune() should delete events older than keep_days and return the count."""
    from datetime import timedelta

    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()

    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)

    # Insert two events with a past timestamp directly
    old_ts = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    assert store._conn is not None
    await store._conn.execute(
        """
        INSERT INTO event_history
            (device_key, room, device_name, device_type, old_state_json, new_state_json, source, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "mock:1",
            "Wohnzimmer",
            "Lampe",
            "light",
            '{"on": true}',
            '{"on": false}',
            "external",
            old_ts,
        ),
    )
    await store._conn.execute(
        """
        INSERT INTO event_history
            (device_key, room, device_name, device_type, old_state_json, new_state_json, source, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "mock:1",
            "Wohnzimmer",
            "Lampe",
            "light",
            '{"on": false}',
            '{"on": true}',
            "external",
            old_ts,
        ),
    )
    await store._conn.commit()

    # prune with keep_days=30 should delete both old events
    deleted = await store.prune(keep_days=30)
    assert deleted == 2

    # No events should remain
    events = await store.get_recent_events(minutes=60 * 24 * 365)
    assert len(events) == 0

    await store.stop()


async def test_prune_keeps_recent_events(tmp_path: Path) -> None:
    """prune() should not delete events within keep_days."""
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()

    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)

    # Insert a recent event via update()
    await store.update(_make_state(device_id, state={"on": True}))
    await store.update(_make_state(device_id, state={"on": False}))

    # prune with keep_days=30 should keep events from today
    deleted = await store.prune(keep_days=30)
    assert deleted == 0

    # The one event should remain
    events = await store.get_recent_events(minutes=60)
    assert len(events) == 1

    await store.stop()


async def test_prune_keep_days_zero_deletes_all(tmp_path: Path) -> None:
    """prune(keep_days=0) deletes all events older than now (i.e., all events)."""
    from datetime import timedelta

    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()

    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)

    # Insert an old event
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert store._conn is not None
    await store._conn.execute(
        """
        INSERT INTO event_history
            (device_key, room, device_name, device_type, old_state_json, new_state_json, source, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "mock:1",
            "Wohnzimmer",
            "Lampe",
            "light",
            '{"on": true}',
            '{"on": false}',
            "external",
            old_ts,
        ),
    )
    await store._conn.commit()

    deleted = await store.prune(keep_days=0)
    assert deleted >= 1

    await store.stop()


# --- Tests for StateStore listener safety (Task 2.3) ---


async def test_listener_error_does_not_crash_update(tmp_path: Path) -> None:
    """A listener that raises should not cause update() to fail."""
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()

    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)

    async def bad_listener(info, old_state, new_state, source) -> None:
        raise RuntimeError("Test error from listener")

    store.on_change(bad_listener)

    # Should not raise
    await store.update(_make_state(device_id, state={"on": True}))
    await asyncio.sleep(0)  # Let fire-and-forget tasks complete

    # State should still be stored correctly
    result = await store.get_state(device_id)
    assert result is not None
    assert result.state["on"] is True

    await store.stop()


async def test_listener_error_does_not_prevent_other_listeners(tmp_path: Path) -> None:
    """A failing listener should not prevent other listeners from running."""
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()

    device_id = _make_device_id("1")
    info = _make_info(device_id)
    store.register_device(info)

    async def bad_listener(info, old_state, new_state, source) -> None:
        raise RuntimeError("First listener fails")

    good_calls: list[int] = []

    async def good_listener(info, old_state, new_state, source) -> None:
        good_calls.append(1)

    store.on_change(bad_listener)
    store.on_change(good_listener)

    await store.update(_make_state(device_id, state={"on": True}))
    await asyncio.sleep(0)  # Let fire-and-forget tasks complete

    assert len(good_calls) == 1

    await store.stop()


# --- Test for timestamp index (Task 2.1) ---


async def test_timestamp_index_exists_after_start(tmp_path: Path) -> None:
    """The idx_event_history_timestamp index should be created on start()."""
    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()

    assert store._conn is not None
    async with store._conn.execute("PRAGMA index_list('event_history')") as cursor:
        rows = await cursor.fetchall()

    index_names = [row[1] for row in rows]
    assert "idx_event_history_timestamp" in index_names

    await store.stop()


# ---------------------------------------------------------------------------
# Wave 4.6 tests: TTL-based brain-action marking
# ---------------------------------------------------------------------------


async def test_brain_action_ttl_multiple_callbacks(tmp_path: Path) -> None:
    """Mark brain action once; call update() twice within TTL -- both should be 'brain' source."""
    from unittest.mock import patch

    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("ttl1")

    # Freeze time so both updates are within the 2s TTL
    with patch("time.monotonic", return_value=1000.0):
        store.mark_brain_action(device_id)

    with patch("time.monotonic", return_value=1000.5):  # 0.5s later, within TTL
        await store.update(_make_state(device_id, state={"on": True}))
        result1 = await store.get_state(device_id)

    with patch("time.monotonic", return_value=1001.0):  # 1.0s later, still within 2s TTL
        await store.update(_make_state(device_id, state={"on": False}))
        result2 = await store.get_state(device_id)

    assert result1 is not None
    assert result1.source == "brain", f"Expected 'brain', got {result1.source!r}"
    assert result2 is not None
    assert result2.source == "brain", f"Expected 'brain', got {result2.source!r}"
    await store.stop()


async def test_brain_action_ttl_expires(tmp_path: Path) -> None:
    """Mark brain action; update() after TTL expires should NOT be 'brain' source."""
    from unittest.mock import patch

    store = StateStore(db_path=tmp_path / "test.db")
    await store.start()
    device_id = _make_device_id("ttl2")

    with patch("time.monotonic", return_value=1000.0):
        store.mark_brain_action(device_id)

    # Update 3 seconds later -- TTL is 2.0s, so this should be treated as external
    with patch("time.monotonic", return_value=1003.0):  # 3s later, past TTL
        await store.update(_make_state(device_id, state={"on": True}))
        result = await store.get_state(device_id)

    assert result is not None
    assert result.source != "brain", f"Expected non-brain source after TTL, got {result.source!r}"
    await store.stop()
