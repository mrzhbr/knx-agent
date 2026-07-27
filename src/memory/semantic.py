"""Semantic memory — SQLite-backed networkx DiGraph for entity relationships.

Persists subject-predicate-object edges in the shared brain.db (same aiosqlite
connection as ProceduralMemory). Loads the full graph into memory on open().
Edge weights strengthen on repeated observations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiosqlite
import networkx as nx

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS semantic_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    last_updated TEXT NOT NULL,
    source TEXT DEFAULT 'observed',
    UNIQUE (subject, predicate, object)
);
CREATE INDEX IF NOT EXISTS idx_semantic_edges_subject ON semantic_edges(subject);
"""


class SemanticMemory:
    """Async SQLite + networkx semantic graph memory.

    Accepts a shared aiosqlite connection (owned by ProceduralMemory).
    All graph mutations write to both SQLite and the in-memory nx.DiGraph.

    Usage::

        sem = SemanticMemory()
        await sem.open(procedural_memory._db)
    """

    def __init__(self) -> None:
        self._db: aiosqlite.Connection | None = None
        self._graph: nx.DiGraph = nx.DiGraph()

    async def open(self, db: aiosqlite.Connection) -> None:
        """Initialize with an existing aiosqlite connection.

        Creates the semantic_edges table and loads all edges into the graph.
        """
        self._db = db
        for stmt in _CREATE_TABLE.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                await self._db.execute(stmt)
        await self._db.commit()
        await self._load_graph()
        logger.info("SemanticMemory opened (%d edges loaded)", self._graph.number_of_edges())

    async def close(self) -> None:
        """No-op — connection lifecycle is owned by ProceduralMemory."""

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("SemanticMemory not open. Call await sem.open(db) first.")
        return self._db

    async def _load_graph(self) -> None:
        """Load all edges from SQLite into the in-memory DiGraph."""
        db = self._require_db()
        self._graph.clear()
        async with db.execute(
            "SELECT subject, predicate, object, weight, source FROM semantic_edges"
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            subject, predicate, obj, weight, source = row
            self._graph.add_edge(subject, obj, predicate=predicate, weight=weight, source=source)
        logger.debug("SemanticMemory graph loaded: %d edges", self._graph.number_of_edges())

    async def add_edge(
        self, subject: str, predicate: str, obj: str, source: str = "observed"
    ) -> None:
        """Add a subject-predicate-object edge. Ignores duplicates."""
        db = self._require_db()
        now = datetime.now().isoformat()
        await db.execute(
            "INSERT OR IGNORE INTO semantic_edges "
            "(subject, predicate, object, weight, last_updated, source) "
            "VALUES (?, ?, ?, 1.0, ?, ?)",
            (subject, predicate, obj, now, source),
        )
        await db.commit()
        if not self._graph.has_edge(subject, obj):
            self._graph.add_edge(subject, obj, predicate=predicate, weight=1.0, source=source)

    async def strengthen_edge(self, subject: str, predicate: str, obj: str) -> None:
        """Increment weight of an existing edge by 1."""
        db = self._require_db()
        now = datetime.now().isoformat()
        await db.execute(
            "UPDATE semantic_edges SET weight = weight + 1, last_updated = ? "
            "WHERE subject = ? AND predicate = ? AND object = ?",
            (now, subject, predicate, obj),
        )
        await db.commit()
        if self._graph.has_edge(subject, obj):
            self._graph[subject][obj]["weight"] = self._graph[subject][obj].get("weight", 1.0) + 1

    async def get_preferences(self, user: str) -> dict[str, list[str]]:
        """Return user's outgoing edges grouped by predicate.

        Returns {predicate: [target1, target2, ...]}
        """
        result: dict[str, list[str]] = {}
        if user not in self._graph:
            return result
        for neighbor in self._graph.successors(user):
            edge_data = self._graph.get_edge_data(user, neighbor) or {}
            pred = edge_data.get("predicate", "related")
            result.setdefault(pred, []).append(neighbor)
        return result

    async def get_related(self, node: str, depth: int = 2) -> list[str]:
        """BFS to depth, returning all reachable nodes (excluding start)."""
        if node not in self._graph:
            return []
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(node, 0)]
        seen: set[str] = {node}
        while queue:
            current, d = queue.pop(0)
            if current != node:
                visited.add(current)
            if d < depth:
                for neighbor in self._graph.successors(current):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append((neighbor, d + 1))
        return list(visited)

    async def format_preferences(self, user: str) -> str:
        """Format user preferences as a German context block."""
        prefs = await self.get_preferences(user)
        if not prefs:
            return ""
        lines = [f"Präferenzen von {user}:"]
        for predicate, targets in prefs.items():
            targets_str = ", ".join(targets)
            lines.append(f"- {predicate}: {targets_str}")
        return "\n".join(lines)

    async def get_edges_by_predicate(self, predicate: str) -> list[dict[str, Any]]:
        """Return all edges with the given predicate from the database."""
        db = self._require_db()
        async with db.execute(
            "SELECT subject, predicate, object, weight, source FROM semantic_edges "
            "WHERE predicate = ?",
            (predicate,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "subject": row[0],
                "predicate": row[1],
                "object": row[2],
                "weight": row[3],
                "source": row[4],
            }
            for row in rows
        ]

    async def decay_edges(self, days: int = 90, factor: float = 0.9) -> None:
        """Reduce weights of edges not updated in the last N days."""
        db = self._require_db()
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        await db.execute(
            "UPDATE semantic_edges SET weight = weight * ? WHERE last_updated < ?",
            (factor, cutoff),
        )
        await db.commit()
        # Reload graph to reflect updated weights
        await self._load_graph()
        logger.info("SemanticMemory: edge decay applied (factor=%.2f, cutoff=%s)", factor, cutoff)

    def edge_count(self) -> int:
        """Return the number of edges in the in-memory semantic graph."""
        return self._graph.number_of_edges()
