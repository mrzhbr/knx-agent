from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .models import DeviceId, DeviceInfo, DeviceState

logger = logging.getLogger(__name__)


class StateStore:
    """Async SQLite-backed store for device state and event history."""

    def __init__(self, db_path: Path = Path("data/hal_state.db")) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()
        self._listeners: list[Callable[[DeviceInfo, dict | None, dict, str], Awaitable[None]]] = []
        self._device_infos: dict[str, DeviceInfo] = {}
        self._brain_action_marks: dict[str, float] = {}
        self._BRAIN_ACTION_TTL: float = 2.0
        self._states: dict[str, dict] = {}  # in-memory mirror of current_state.state_json

    async def start(self) -> None:
        """Create parent directories, open connection, and create tables."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self._db_path))
        await self._create_tables()
        # Warm the in-memory _states cache from the existing DB
        async with self._conn.execute("SELECT device_key, state_json FROM current_state") as cursor:
            rows = await cursor.fetchall()
        for device_key, state_json in rows:
            self._states[device_key] = json.loads(state_json)

    async def stop(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _create_tables(self) -> None:
        assert self._conn is not None
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS current_state (
                device_key   TEXT PRIMARY KEY,
                platform     TEXT NOT NULL,
                native_id    TEXT NOT NULL,
                room         TEXT NOT NULL DEFAULT '',
                name         TEXT NOT NULL DEFAULT '',
                device_type  TEXT NOT NULL DEFAULT '',
                state_json   TEXT NOT NULL,
                source       TEXT NOT NULL DEFAULT 'external',
                last_updated TEXT NOT NULL
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS event_history (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                device_key     TEXT NOT NULL,
                room           TEXT NOT NULL DEFAULT '',
                device_name    TEXT NOT NULL DEFAULT '',
                device_type    TEXT NOT NULL DEFAULT '',
                old_state_json TEXT,
                new_state_json TEXT NOT NULL,
                source         TEXT NOT NULL DEFAULT 'external',
                timestamp      TEXT NOT NULL
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS rule_configs (
                rule_name  TEXT PRIMARY KEY,
                enabled    INTEGER NOT NULL DEFAULT 1,
                params_json TEXT NOT NULL DEFAULT '{}'
            )
        """)
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_rules (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT NOT NULL UNIQUE,
                device_type      TEXT NOT NULL DEFAULT '',
                sensor_class     TEXT NOT NULL DEFAULT '',
                room             TEXT NOT NULL DEFAULT '',
                state_key        TEXT NOT NULL,
                operator         TEXT NOT NULL,
                threshold        REAL NOT NULL,
                severity         TEXT NOT NULL DEFAULT 'suggestion',
                message_template TEXT NOT NULL,
                enabled          INTEGER NOT NULL DEFAULT 1,
                approved         INTEGER NOT NULL DEFAULT 0,
                source           TEXT NOT NULL DEFAULT 'user'
            )
        """)
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_history_timestamp ON event_history(timestamp)"
        )
        await self._conn.commit()

    def register_device(self, info: DeviceInfo) -> None:
        """Cache DeviceInfo for use in event history and listener callbacks."""
        self._device_infos[info.id.key] = info

    def on_change(
        self,
        listener: Callable[[DeviceInfo, dict | None, dict, str], Awaitable[None]],
    ) -> None:
        """Register a listener called with (device_info, old_state, new_state, source) on change.

        Listener is called:
        - On first write (old_state=None, new_state=new dict)
        - On subsequent state changes (old_state and new_state both populated)
        - NOT called when state is unchanged (duplicate update)

        Listener invocation is fire-and-forget (asyncio.create_task).
        """
        self._listeners.append(listener)

    async def update(self, device_state: DeviceState) -> None:
        """Upsert current_state, append event_history if changed, notify listeners."""
        assert self._conn is not None
        key = device_state.device_id.key
        new_state_json = json.dumps(device_state.state)
        ts = device_state.last_updated.isoformat()

        # Determine source with TTL-based brain-action marking
        source = device_state.source
        now = time.monotonic()
        mark_time = self._brain_action_marks.get(key)
        if mark_time is not None:
            if now - mark_time < self._BRAIN_ACTION_TTL:
                source = "brain"
                # Do NOT remove the mark — multi-UID commands (e.g. set_brightness)
                # produce 2+ callbacks within ~100ms. Mark stays until TTL expires.
            else:
                # Mark expired: treat as external, clean up
                del self._brain_action_marks[key]

        # Cleanup pass: remove stale marks older than 5 seconds
        stale_keys = [k for k, t in self._brain_action_marks.items() if now - t > 5.0]
        for k in stale_keys:
            del self._brain_action_marks[k]

        info = self._device_infos.get(key)
        room = info.room if info else ""
        name = info.name if info else ""
        device_type = str(info.device_type) if info else ""

        async with self._write_lock:
            # Read existing state for diff
            async with self._conn.execute(
                "SELECT state_json FROM current_state WHERE device_key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
            old_state_json: str | None = row[0] if row else None

            # Upsert current_state
            await self._conn.execute(
                """
                INSERT INTO current_state
                    (device_key, platform, native_id, room, name, device_type,
                     state_json, source, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_key) DO UPDATE SET
                    state_json   = excluded.state_json,
                    source       = excluded.source,
                    last_updated = excluded.last_updated,
                    room         = excluded.room,
                    name         = excluded.name,
                    device_type  = excluded.device_type
                """,
                (
                    key,
                    device_state.device_id.platform,
                    device_state.device_id.native_id,
                    room,
                    name,
                    device_type,
                    new_state_json,
                    source,
                    ts,
                ),
            )

            # Only append history if state was previously known AND has changed
            history_changed = old_state_json is not None and old_state_json != new_state_json
            if history_changed:
                await self._conn.execute(
                    """
                    INSERT INTO event_history
                        (device_key, room, device_name, device_type,
                         old_state_json, new_state_json, source, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (key, room, name, device_type, old_state_json, new_state_json, source, ts),
                )

            await self._conn.commit()

        # Keep in-memory cache in sync (outside lock — write-once, single event loop)
        self._states[key] = json.loads(new_state_json)

        # Notify listeners: fire on first write (old_state_json is None) OR on actual change.
        # Do NOT fire when state is unchanged (old == new).
        should_notify = old_state_json is None or old_state_json != new_state_json
        if should_notify and info is not None:
            old_state: dict | None = json.loads(old_state_json) if old_state_json else None
            new_state: dict = json.loads(new_state_json)

            async def _safe_call(
                fn: Any,
                _info: Any,
                _old: Any,
                _new: Any,
                _src: str,
            ) -> None:
                try:
                    await fn(_info, _old, _new, _src)
                except Exception:
                    logger.warning(
                        "StateStore listener error in %s",
                        getattr(fn, "__qualname__", repr(fn)),
                        exc_info=True,
                    )

            for listener in self._listeners:
                asyncio.create_task(_safe_call(listener, info, old_state, new_state, source))

    def mark_brain_action(self, device_id: DeviceId) -> None:
        """Mark the next update(s) for this device as source='brain' (TTL-based flag).

        Stores a timestamp. The update() method checks if the mark is within the
        _BRAIN_ACTION_TTL window (default 2.0s). The mark is NOT removed on first
        use — this handles multi-UID commands that produce 2+ callbacks per action.
        """
        self._brain_action_marks[device_id.key] = time.monotonic()

    def get_state_sync(self, device_id: DeviceId) -> dict | None:
        """Return the current state dict for a device from the in-memory cache.

        Synchronous — no I/O. Used by the webhook handler hot path to avoid
        SQLite calls per incoming event. Returns ONLY the state dict (not source
        or last_updated). Use get_state() when full DeviceState metadata is needed.
        """
        return self._states.get(device_id.key)

    async def get_state(self, device_id: DeviceId) -> DeviceState | None:
        """Return the current state for a device, or None if unknown."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT state_json, source, last_updated FROM current_state WHERE device_key = ?",
            (device_id.key,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        state_json, source, last_updated = row
        return DeviceState(
            device_id=device_id,
            state=json.loads(state_json),
            last_updated=datetime.fromisoformat(last_updated),
            source=source,
        )

    async def get_room_state(self, room: str) -> dict[str, dict[str, Any]]:
        """Return all current states for devices in a room, keyed by device_key."""
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT device_key, state_json FROM current_state WHERE room = ?", (room,)
        ) as cursor:
            rows = await cursor.fetchall()
        return {key: json.loads(state_json) for key, state_json in rows}

    async def get_recent_events(
        self, minutes: int = 30, device_key: str | None = None
    ) -> list[dict[str, Any]]:
        """Return event history within the last N minutes, optionally filtered by device_key."""
        assert self._conn is not None
        from datetime import timedelta

        cutoff_str = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

        if device_key is not None:
            async with self._conn.execute(
                """
                SELECT device_key, room, device_name, device_type,
                       old_state_json, new_state_json, source, timestamp
                FROM event_history
                WHERE timestamp >= ? AND device_key = ?
                ORDER BY id DESC
                """,
                (cutoff_str, device_key),
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with self._conn.execute(
                """
                SELECT device_key, room, device_name, device_type,
                       old_state_json, new_state_json, source, timestamp
                FROM event_history
                WHERE timestamp >= ?
                ORDER BY id DESC
                """,
                (cutoff_str,),
            ) as cursor:
                rows = await cursor.fetchall()

        result = []
        for row in rows:
            result.append(
                {
                    "device_key": row[0],
                    "room": row[1],
                    "device_name": row[2],
                    "device_type": row[3],
                    "old_state": json.loads(row[4]) if row[4] else None,
                    "new_state": json.loads(row[5]),
                    "source": row[6],
                    "timestamp": row[7],
                }
            )
        return result

    async def get_full_state_summary(self) -> dict[str, dict[str, Any]]:
        """Return all current states keyed by device_key."""
        assert self._conn is not None
        async with self._conn.execute("SELECT device_key, state_json FROM current_state") as cursor:
            rows = await cursor.fetchall()
        return {key: json.loads(state_json) for key, state_json in rows}

    async def prune(self, keep_days: int = 30) -> int:
        """Delete old event history entries and return count of deleted rows.

        Deletes all event_history rows where timestamp is older than keep_days days ago.
        Uses write_lock to serialize with concurrent writes.

        Args:
            keep_days: Number of days of history to retain. Entries older than this
                       are deleted. Default 30 days.

        Returns:
            Number of rows deleted.
        """
        assert self._conn is not None
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        async with self._write_lock:
            async with self._conn.execute(
                "DELETE FROM event_history WHERE timestamp < ?", (cutoff,)
            ) as cursor:
                deleted = cursor.rowcount
            await self._conn.commit()
        return deleted

    async def load_rule_configs(self) -> dict[str, tuple[bool, dict]]:
        """Load all rule configs from the rule_configs table.

        Returns a dict mapping rule_name to (enabled, params) tuple.
        """
        assert self._conn is not None
        async with self._conn.execute(
            "SELECT rule_name, enabled, params_json FROM rule_configs"
        ) as cursor:
            rows = await cursor.fetchall()
        result: dict[str, tuple[bool, dict]] = {}
        for rule_name, enabled, params_json in rows:
            result[rule_name] = (bool(enabled), json.loads(params_json))
        return result

    async def save_rule_config(self, name: str, enabled: bool, params: dict) -> None:
        """Upsert a rule config in the rule_configs table."""
        assert self._conn is not None
        async with self._write_lock:
            await self._conn.execute(
                """
                INSERT INTO rule_configs (rule_name, enabled, params_json)
                VALUES (?, ?, ?)
                ON CONFLICT(rule_name) DO UPDATE SET
                    enabled     = excluded.enabled,
                    params_json = excluded.params_json
                """,
                (name, int(enabled), json.dumps(params)),
            )
            await self._conn.commit()

    async def load_custom_rules(self) -> list[dict]:
        """Load all custom rules from the custom_rules table."""
        assert self._conn is not None
        async with self._conn.execute(
            """
            SELECT id, name, device_type, sensor_class, room, state_key, operator,
                   threshold, severity, message_template, enabled, approved, source
            FROM custom_rules
            """
        ) as cursor:
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            result.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "device_type": row[2],
                    "sensor_class": row[3],
                    "room": row[4],
                    "state_key": row[5],
                    "operator": row[6],
                    "threshold": float(row[7]),
                    "severity": row[8],
                    "message_template": row[9],
                    "enabled": bool(row[10]),
                    "approved": bool(row[11]),
                    "source": row[12],
                }
            )
        return result

    async def save_custom_rule(self, rule: dict) -> int:
        """Insert a new custom rule and return the row id."""
        assert self._conn is not None
        async with self._write_lock:
            async with self._conn.execute(
                """
                INSERT INTO custom_rules
                    (name, device_type, sensor_class, room, state_key, operator,
                     threshold, severity, message_template, enabled, approved, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule.get("name", ""),
                    rule.get("device_type", ""),
                    rule.get("sensor_class", ""),
                    rule.get("room", ""),
                    rule.get("state_key", ""),
                    rule.get("operator", ">"),
                    float(rule.get("threshold", 0)),
                    rule.get("severity", "suggestion"),
                    rule.get("message_template", ""),
                    int(rule.get("enabled", True)),
                    int(rule.get("approved", False)),
                    rule.get("source", "user"),
                ),
            ) as cursor:
                row_id = cursor.lastrowid or 0
            await self._conn.commit()
        return row_id

    async def update_custom_rule(
        self,
        name: str,
        approved: bool | None = None,
        enabled: bool | None = None,
    ) -> bool:
        """Update approval/enabled status for a custom rule by name.

        Uses a FIXED SQL template (no dynamic column construction) to avoid
        injection risk. If either parameter is None, preserves current value
        by reading it first via SELECT.

        Returns True if a row was updated, False if no matching rule found.
        """
        assert self._conn is not None

        # Read current values if needed
        if approved is None or enabled is None:
            async with self._conn.execute(
                "SELECT approved, enabled FROM custom_rules WHERE name = ?", (name,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return False
            current_approved, current_enabled = row
            if approved is None:
                approved = bool(current_approved)
            if enabled is None:
                enabled = bool(current_enabled)

        async with self._write_lock:
            async with self._conn.execute(
                "UPDATE custom_rules SET approved = ?, enabled = ? WHERE name = ?",
                (int(approved), int(enabled), name),
            ) as cursor:
                rows_affected = cursor.rowcount
            await self._conn.commit()

        return rows_affected > 0
