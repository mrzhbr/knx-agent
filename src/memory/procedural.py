"""Procedural memory — aiosqlite SQLite store.

Tables:
  preferences     — user/agent preferences (facts)
  device_knowledge — natural-language descriptions of devices
  state_changes   — recent device state transitions (rolling log)
  routines        — candidate automations
  routine_executions — execution history for routines
  scenes           — user-defined named action sequences

WAL mode. Single brain.db file at data/brain.db.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.hal.models import DeviceInfo

import aiosqlite

logger = logging.getLogger(__name__)

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS preferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    fact TEXT NOT NULL,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_preferences_fact ON preferences(fact);

CREATE TABLE IF NOT EXISTS device_knowledge (
    uid TEXT PRIMARY KEY,
    room TEXT,
    device_name TEXT,
    description TEXT,
    location_hint TEXT,
    usage_notes TEXT,
    confirmed INTEGER DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    uid TEXT NOT NULL,
    room TEXT,
    device TEXT,
    datapoint TEXT,
    old_value TEXT,
    new_value TEXT NOT NULL,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_state_changes_uid ON state_changes(uid);
CREATE INDEX IF NOT EXISTS idx_state_changes_timestamp ON state_changes(timestamp);

CREATE TABLE IF NOT EXISTS routines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    trigger TEXT,
    actions TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    confidence REAL DEFAULT 0.0,
    confirmed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    autonomy_level INTEGER DEFAULT 0,
    trigger_type TEXT DEFAULT NULL,
    trigger_value TEXT DEFAULT NULL,
    cron_expr TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS routine_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    routine_id INTEGER NOT NULL,
    executed_at TEXT NOT NULL,
    success INTEGER DEFAULT 1,
    notes TEXT,
    FOREIGN KEY (routine_id) REFERENCES routines(id)
);
CREATE INDEX IF NOT EXISTS idx_routine_executions_routine ON routine_executions(routine_id);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    cron_expr TEXT,
    one_shot_at TEXT,
    routine_id INTEGER,
    action_type TEXT NOT NULL,
    action_params TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    last_run TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled ON schedules(enabled);
CREATE TABLE IF NOT EXISTS scenes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    actions TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_routine_executions_ts ON routine_executions(executed_at);
CREATE TABLE IF NOT EXISTS health_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_snapshots_ts ON health_snapshots(timestamp);
"""


class ProceduralMemory:
    """Async SQLite store for procedural/episodic agent memory.

    Usage::

        memory = ProceduralMemory("data/brain.db")
        await memory.open()
        await memory.add_preference("Max mag das Licht im Büro bei 60%")
        await memory.close()
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        """Open database and create tables if needed (WAL mode)."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        for stmt in CREATE_TABLES.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await self._db.execute(stmt)
        await self._db.commit()
        # Migration: add new columns to routines table for existing databases
        for _col_sql in [
            "ALTER TABLE routines ADD COLUMN autonomy_level INTEGER DEFAULT 0",
            "ALTER TABLE routines ADD COLUMN trigger_type TEXT DEFAULT NULL",
            "ALTER TABLE routines ADD COLUMN trigger_value TEXT DEFAULT NULL",
            "ALTER TABLE routines ADD COLUMN cron_expr TEXT DEFAULT NULL",
        ]:
            try:
                await self._db.execute(_col_sql)
                await self._db.commit()
            except Exception:
                pass  # Column already exists in this database
        logger.info("ProceduralMemory opened at %s (WAL mode)", self._db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("ProceduralMemory not open. Call await memory.open() first.")
        return self._db

    # ── Preferences ────────────────────────────────────────────────────────

    async def add_preference(self, fact: str, source: str | None = None) -> None:
        """Store a preference or fact."""
        db = self._require_db()
        await db.execute(
            "INSERT INTO preferences (created_at, fact, source) VALUES (?, ?, ?)",
            (datetime.now().isoformat(), fact, source),
        )
        await db.commit()

    async def get_preferences(self, limit: int = 50) -> list[dict]:
        """Return the most recent preferences."""
        db = self._require_db()
        async with db.execute(
            "SELECT id, created_at, fact, source FROM preferences ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def search_preferences(self, keyword: str) -> list[dict]:
        """Full-text search preferences by keyword."""
        db = self._require_db()
        async with db.execute(
            "SELECT id, created_at, fact, source FROM preferences "
            "WHERE fact LIKE ? ORDER BY created_at DESC",
            (f"%{keyword}%",),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Device Knowledge ───────────────────────────────────────────────────

    async def set_device_knowledge(
        self,
        uid: str,
        room: str,
        device_name: str,
        description: str,
        location_hint: str | None = None,
        usage_notes: str | None = None,
        confirmed: bool = False,
    ) -> None:
        """Insert or replace device knowledge entry."""
        db = self._require_db()
        await db.execute(
            """INSERT INTO device_knowledge
               (uid, room, device_name, description, location_hint, usage_notes, confirmed, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(uid) DO UPDATE SET
                 room=excluded.room,
                 device_name=excluded.device_name,
                 description=excluded.description,
                 location_hint=excluded.location_hint,
                 usage_notes=excluded.usage_notes,
                 confirmed=excluded.confirmed,
                 updated_at=excluded.updated_at""",
            (
                uid,
                room,
                device_name,
                description,
                location_hint,
                usage_notes,
                int(confirmed),
                datetime.now().isoformat(),
            ),
        )
        await db.commit()

    async def get_device_knowledge(self, uid: str) -> dict | None:
        """Return knowledge for a specific device UID."""
        db = self._require_db()
        async with db.execute("SELECT * FROM device_knowledge WHERE uid = ?", (uid,)) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_unknown_devices(self, limit: int = 10) -> list[dict]:
        """Return devices with no description (unconfirmed knowledge)."""
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM device_knowledge WHERE confirmed = 0 OR description IS NULL OR description = '' "
            "ORDER BY updated_at ASC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def seed_from_devices(self, devices: list[DeviceInfo]) -> None:
        """Populate device_knowledge rows from a list of DeviceInfo objects (uid, name, room)."""
        db = self._require_db()
        now = datetime.now().isoformat()
        for device in devices:
            await db.execute(
                """INSERT OR IGNORE INTO device_knowledge
                   (uid, room, device_name, description, confirmed, updated_at)
                   VALUES (?, ?, ?, '', 0, ?)""",
                (device.id.native_id, device.room, device.name, now),
            )
        await db.commit()
        logger.info("device_knowledge seeded from %d devices", len(devices))

    # ── State Changes ──────────────────────────────────────────────────────

    async def log_state_change(
        self,
        uid: str,
        new_value: str,
        old_value: str | None = None,
        room: str | None = None,
        device: str | None = None,
        datapoint: str | None = None,
        source: str | None = None,
    ) -> None:
        """Log a device state change."""
        db = self._require_db()
        await db.execute(
            "INSERT INTO state_changes "
            "(timestamp, uid, room, device, datapoint, old_value, new_value, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now().isoformat(),
                uid,
                room,
                device,
                datapoint,
                old_value,
                new_value,
                source,
            ),
        )
        await db.commit()

    # ── Routines ───────────────────────────────────────────────────────────

    async def get_routines(self, enabled_only: bool = True) -> list[dict]:
        """Return stored routines."""
        db = self._require_db()
        query = "SELECT * FROM routines"
        params: tuple = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY created_at DESC"
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("actions"):
                try:
                    d["actions"] = json.loads(d["actions"])
                except (json.JSONDecodeError, TypeError):
                    d["actions"] = []
            result.append(d)
        return result

    async def store_candidate_routine(
        self,
        name: str,
        description: str,
        trigger: str,
        actions: list[dict],
        confidence: float = 0.0,
        trigger_type: str | None = None,
        trigger_value: str | None = None,
        autonomy_level: int = 0,
        cron_expr: str | None = None,
    ) -> int:
        """Insert a new candidate routine. Returns new routine id."""
        db = self._require_db()
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "INSERT INTO routines (name, description, trigger, actions, confidence, created_at, updated_at, "
            "trigger_type, trigger_value, autonomy_level, cron_expr) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                description,
                trigger,
                json.dumps(actions),
                confidence,
                now,
                now,
                trigger_type,
                trigger_value,
                autonomy_level,
                cron_expr,
            ),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def update_routine(
        self,
        routine_id: int,
        *,
        enabled: bool | None = None,
        confirmed: bool | None = None,
        confidence: float | None = None,
    ) -> None:
        """Update routine fields."""
        db = self._require_db()
        updates: list[str] = ["updated_at = ?"]
        values: list[Any] = [datetime.now().isoformat()]
        if enabled is not None:
            updates.append("enabled = ?")
            values.append(int(enabled))
        if confirmed is not None:
            updates.append("confirmed = ?")
            values.append(int(confirmed))
        if confidence is not None:
            updates.append("confidence = ?")
            values.append(confidence)
        values.append(routine_id)
        await db.execute(
            f"UPDATE routines SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        await db.commit()

    # ── Schedules ──────────────────────────────────────────────────────────

    async def add_schedule(
        self,
        description: str,
        action_type: str,
        action_params: str,
        *,
        cron_expr: str | None = None,
        one_shot_at: str | None = None,
        routine_id: int | None = None,
        created_by: str | None = None,
    ) -> int:
        """Store a new schedule. Returns new schedule id."""
        db = self._require_db()
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "INSERT INTO schedules (description, cron_expr, one_shot_at, routine_id, "
            "action_type, action_params, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                description,
                cron_expr,
                one_shot_at,
                routine_id,
                action_type,
                action_params,
                now,
                created_by,
            ),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_active_schedules(self) -> list[dict]:
        """Return all enabled schedules."""
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM schedules WHERE enabled = 1 ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_schedule(self, schedule_id: int) -> dict | None:
        """Return a specific schedule by id."""
        db = self._require_db()
        async with db.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row else None

    async def cancel_schedule(self, schedule_id: int) -> bool:
        """Disable a schedule by id. Returns True if found and updated."""
        db = self._require_db()
        cursor = await db.execute("UPDATE schedules SET enabled = 0 WHERE id = ?", (schedule_id,))
        await db.commit()
        return cursor.rowcount > 0

    async def mark_schedule_run(self, schedule_id: int) -> None:
        """Update last_run timestamp for a schedule."""
        db = self._require_db()
        await db.execute(
            "UPDATE schedules SET last_run = ? WHERE id = ?",
            (datetime.now().isoformat(), schedule_id),
        )
        await db.commit()

    async def delete_expired_one_shots(self) -> int:
        """Delete one-shot schedules that have already run. Returns count deleted."""
        db = self._require_db()
        cursor = await db.execute(
            "DELETE FROM schedules WHERE one_shot_at IS NOT NULL AND last_run IS NOT NULL"
        )
        await db.commit()
        return cursor.rowcount

    # ── Routine lookup (extended) ────────────────────────────────────────────

    async def get_routine_by_name(self, name: str) -> dict | None:
        """Find a routine by partial name match (case-insensitive SQL LIKE)."""
        db = self._require_db()
        async with db.execute(
            "SELECT * FROM routines WHERE name LIKE ? LIMIT 1", (f"%{name}%",)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("actions"):
            try:
                d["actions"] = json.loads(d["actions"])
            except (json.JSONDecodeError, TypeError):
                pass
        return d

    async def get_routines_by_trigger(
        self, trigger_type: str, trigger_value: str | None = None
    ) -> list[dict]:
        """Return routines filtered by trigger_type and optionally trigger_value."""
        db = self._require_db()
        if trigger_value is not None:
            query = (
                "SELECT * FROM routines WHERE trigger_type = ? AND trigger_value = ? AND enabled = 1 "
                "ORDER BY created_at DESC"
            )
            params: tuple = (trigger_type, trigger_value)
        else:
            query = "SELECT * FROM routines WHERE trigger_type = ? AND enabled = 1 ORDER BY created_at DESC"
            params = (trigger_type,)
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("actions"):
                try:
                    d["actions"] = json.loads(d["actions"])
                except (json.JSONDecodeError, TypeError):
                    d["actions"] = []
            result.append(d)
        return result

    async def log_routine_execution(
        self, routine_id: int, success: bool, notes: str | None = None
    ) -> None:
        """Log a routine execution to routine_executions table."""
        db = self._require_db()
        await db.execute(
            "INSERT INTO routine_executions (routine_id, executed_at, success, notes) "
            "VALUES (?, ?, ?, ?)",
            (routine_id, datetime.now().isoformat(), int(success), notes),
        )
        await db.commit()

    async def get_routine_executions(
        self, routine_id: int | None = None, limit: int = 100
    ) -> list[dict]:
        """Return recent execution history, optionally filtered by routine_id."""
        db = self._require_db()
        params: tuple[int, ...]
        if routine_id is not None:
            query = (
                "SELECT * FROM routine_executions WHERE routine_id = ? "
                "ORDER BY executed_at DESC LIMIT ?"
            )
            params = (routine_id, limit)
        else:
            query = "SELECT * FROM routine_executions ORDER BY executed_at DESC LIMIT ?"
            params = (limit,)
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── Scenes ──────────────────────────────────────────────────────────────

    async def add_scene(self, name: str, description: str, actions: str) -> int:
        """Store a new scene. Returns the new scene id."""
        db = self._require_db()
        now = datetime.now().isoformat()
        cursor = await db.execute(
            "INSERT INTO scenes (name, description, actions, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (name, description, actions, now, now),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_scene_by_name(self, name: str) -> list[dict]:
        """Find scenes by name. Returns list[dict].

        First tries exact case-insensitive match. If found, returns [scene].
        If not found, tries partial LIKE match and returns all matches (0, 1, or many).
        """
        db = self._require_db()
        # Exact match first
        async with db.execute(
            "SELECT * FROM scenes WHERE LOWER(name) = LOWER(?)", (name,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is not None:
            d = dict(row)
            if d.get("actions"):
                try:
                    d["actions"] = json.loads(d["actions"])
                except (json.JSONDecodeError, TypeError):
                    pass
            return [d]
        # Partial match
        async with db.execute(
            "SELECT * FROM scenes WHERE LOWER(name) LIKE LOWER(?)", (f"%{name}%",)
        ) as cursor:
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("actions"):
                try:
                    d["actions"] = json.loads(d["actions"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return result

    async def get_all_scenes(self) -> list[dict]:
        """Return all scenes ordered by name."""
        db = self._require_db()
        async with db.execute("SELECT * FROM scenes ORDER BY name ASC") as cursor:
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("actions"):
                try:
                    d["actions"] = json.loads(d["actions"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return result

    async def update_scene(
        self,
        scene_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        actions: str | None = None,
    ) -> bool:
        """Update scene fields. Returns True if a row was updated."""
        db = self._require_db()
        updates: list[str] = ["updated_at = ?"]
        values: list[Any] = [datetime.now().isoformat()]
        if name is not None:
            updates.append("name = ?")
            values.append(name)
        if description is not None:
            updates.append("description = ?")
            values.append(description)
        if actions is not None:
            updates.append("actions = ?")
            values.append(actions)
        values.append(scene_id)
        cursor = await db.execute(
            f"UPDATE scenes SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        await db.commit()
        return cursor.rowcount > 0

    async def delete_scene(self, scene_id: int) -> bool:
        """Delete a scene by id. Returns True if deleted."""
        db = self._require_db()
        cursor = await db.execute("DELETE FROM scenes WHERE id = ?", (scene_id,))
        await db.commit()
        return cursor.rowcount > 0

    # ── Health Snapshots ────────────────────────────────────────────────────

    async def add_health_snapshot(self, timestamp: str, payload: str) -> int:
        """Insert a health snapshot row. Returns the new row id."""
        db = self._require_db()
        cursor = await db.execute(
            "INSERT INTO health_snapshots (timestamp, payload) VALUES (?, ?)",
            (timestamp, payload),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def get_latest_health_snapshot(self, n: int = 1) -> list[dict]:
        """Return the N most recent health snapshots ordered by timestamp DESC."""
        db = self._require_db()
        async with db.execute(
            "SELECT id, timestamp, payload FROM health_snapshots ORDER BY timestamp DESC LIMIT ?",
            (n,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def prune_health_snapshots(self, max_age_days: int = 90) -> int:
        """Delete health snapshots older than max_age_days. Returns count deleted."""
        from datetime import timedelta

        db = self._require_db()
        cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
        cursor = await db.execute(
            "DELETE FROM health_snapshots WHERE timestamp < ?",
            (cutoff,),
        )
        await db.commit()
        return cursor.rowcount

    # ── State Changes Query Helpers ─────────────────────────────────────────

    async def get_top_rooms(self, days: int = 7, limit: int = 5) -> list[dict]:
        """Return top rooms by state change count in the last N days.

        NOTE: In the current codebase, room is never populated (log_state_change
        is called with room=None from layer.py). Returns empty list until HAL
        state changes start populating room data.
        """
        from datetime import timedelta

        db = self._require_db()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        async with db.execute(
            "SELECT room, COUNT(*) as count FROM state_changes "
            "WHERE timestamp >= ? AND room IS NOT NULL "
            "GROUP BY room ORDER BY count DESC LIMIT ?",
            (cutoff, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_top_devices(self, days: int = 7, limit: int = 5) -> list[dict]:
        """Return top devices by state change count in the last N days."""
        from datetime import timedelta

        db = self._require_db()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        async with db.execute(
            "SELECT device, COUNT(*) as count FROM state_changes "
            "WHERE timestamp >= ? AND device IS NOT NULL "
            "GROUP BY device ORDER BY count DESC LIMIT ?",
            (cutoff, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_recent_routine_stats(self, days: int = 7) -> dict:
        """Return success/failure counts for routine executions in the last N days."""
        from datetime import timedelta

        db = self._require_db()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        async with db.execute(
            "SELECT success, COUNT(*) as cnt FROM routine_executions "
            "WHERE executed_at >= ? GROUP BY success",
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()
        result = {"success": 0, "failure": 0}
        for row in rows:
            d = dict(row)
            if d["success"] == 1:
                result["success"] = d["cnt"]
            else:
                result["failure"] = d["cnt"]
        return result

    # ── Preferences Count ────────────────────────────────────────────────────

    async def count_preferences(self) -> int:
        """Return total count of stored preferences."""
        db = self._require_db()
        async with db.execute("SELECT COUNT(*) as cnt FROM preferences") as cursor:
            row = await cursor.fetchone()
        return dict(row)["cnt"] if row else 0
