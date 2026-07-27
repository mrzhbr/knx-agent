"""Tests for SemanticMemory — SQLite + networkx DiGraph semantic graph."""

from __future__ import annotations

import aiosqlite
import pytest

from src.memory.semantic import SemanticMemory


# ─── Fixture ───────────────────────────────────────────────────────────────


@pytest.fixture
async def memory():
    """Open a fresh in-memory SemanticMemory for each test."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    sem = SemanticMemory()
    await sem.open(db)
    yield sem
    await db.close()


# ─── Tests: open() ─────────────────────────────────────────────────────────


async def test_open_creates_table(memory):
    """open() creates the semantic_edges table."""
    db = memory._db
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='semantic_edges'"
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None


async def test_open_creates_index(memory):
    """open() creates the index on semantic_edges.subject."""
    db = memory._db
    async with db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_semantic_edges_subject'"
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None


async def test_open_empty_graph(memory):
    """Freshly opened memory has empty graph."""
    assert memory._graph.number_of_edges() == 0
    assert memory._graph.number_of_nodes() == 0


# ─── Tests: add_edge() ─────────────────────────────────────────────────────


async def test_add_edge_stores_in_db(memory):
    """add_edge() persists the edge in SQLite."""
    await memory.add_edge("Max", "bevorzugt", "Buero Licht 60%")
    async with memory._db.execute("SELECT * FROM semantic_edges") as cursor:
        rows = await cursor.fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["subject"] == "Max"
    assert row["predicate"] == "bevorzugt"
    assert row["object"] == "Buero Licht 60%"
    assert row["weight"] == 1.0


async def test_add_edge_updates_graph(memory):
    """add_edge() adds the edge to the in-memory DiGraph."""
    await memory.add_edge("Max", "bevorzugt", "Buero Licht 60%")
    assert memory._graph.has_edge("Max", "Buero Licht 60%")
    edge_data = memory._graph.get_edge_data("Max", "Buero Licht 60%")
    assert edge_data["predicate"] == "bevorzugt"


async def test_add_edge_duplicate_is_ignored(memory):
    """add_edge() with duplicate edge does not raise or create duplicate."""
    await memory.add_edge("Max", "mag", "Kaffee")
    await memory.add_edge("Max", "mag", "Kaffee")  # should not raise
    async with memory._db.execute("SELECT COUNT(*) FROM semantic_edges") as cursor:
        count = (await cursor.fetchone())[0]
    assert count == 1


async def test_add_edge_with_source(memory):
    """add_edge() stores the source field."""
    await memory.add_edge("Person 2", "bevorzugt", "18 Grad", source="stated")
    async with memory._db.execute("SELECT source FROM semantic_edges") as cursor:
        row = await cursor.fetchone()
    assert row[0] == "stated"


# ─── Tests: strengthen_edge() ──────────────────────────────────────────────


async def test_strengthen_edge_increments_weight(memory):
    """strengthen_edge() increments weight by 1 in DB and graph."""
    await memory.add_edge("Max", "mag", "Kaffee")
    await memory.strengthen_edge("Max", "mag", "Kaffee")

    async with memory._db.execute("SELECT weight FROM semantic_edges") as cursor:
        row = await cursor.fetchone()
    assert row[0] == 2.0

    edge_data = memory._graph.get_edge_data("Max", "Kaffee")
    assert edge_data["weight"] == 2.0


async def test_strengthen_edge_updates_timestamp(memory):
    """strengthen_edge() updates last_updated timestamp."""
    await memory.add_edge("Max", "mag", "Kaffee")

    async with memory._db.execute("SELECT last_updated FROM semantic_edges") as cursor:
        original_ts = (await cursor.fetchone())[0]

    import asyncio

    await asyncio.sleep(0.01)  # Ensure timestamp changes
    await memory.strengthen_edge("Max", "mag", "Kaffee")

    async with memory._db.execute("SELECT last_updated FROM semantic_edges") as cursor:
        new_ts = (await cursor.fetchone())[0]

    assert new_ts >= original_ts


# ─── Tests: get_preferences() ──────────────────────────────────────────────


async def test_get_preferences_empty_for_unknown_user(memory):
    """get_preferences() returns empty dict for unknown user."""
    result = await memory.get_preferences("UnknownUser")
    assert result == {}


async def test_get_preferences_returns_edges_grouped_by_predicate(memory):
    """get_preferences() returns {predicate: [targets]} for user."""
    await memory.add_edge("Max", "bevorzugt", "Buero Licht 60%")
    await memory.add_edge("Max", "bevorzugt", "Schlafzimmer 20 Grad")
    await memory.add_edge("Max", "mag", "Kaffee")

    prefs = await memory.get_preferences("Max")
    assert "bevorzugt" in prefs
    assert len(prefs["bevorzugt"]) == 2
    assert "Buero Licht 60%" in prefs["bevorzugt"]
    assert "mag" in prefs
    assert "Kaffee" in prefs["mag"]


# ─── Tests: format_preferences() ──────────────────────────────────────────


async def test_format_preferences_empty_for_unknown_user(memory):
    """format_preferences() returns empty string for unknown user."""
    result = await memory.format_preferences("UnknownUser")
    assert result == ""


async def test_format_preferences_returns_german_text(memory):
    """format_preferences() returns German-formatted text."""
    await memory.add_edge("Max", "bevorzugt", "Buero Licht 60%")
    result = await memory.format_preferences("Max")
    assert "Max" in result
    assert "bevorzugt" in result
    assert "Buero Licht 60%" in result
    assert "Präferenzen" in result


# ─── Tests: get_edges_by_predicate() ──────────────────────────────────────


async def test_get_edges_by_predicate_returns_correct_rows(memory):
    """get_edges_by_predicate() returns all edges matching the predicate."""
    await memory.add_edge("Max", "bevorzugt", "60% Helligkeit")
    await memory.add_edge("Person 2", "bevorzugt", "18 Grad")
    await memory.add_edge("Max", "mag", "Kaffee")

    edges = await memory.get_edges_by_predicate("bevorzugt")
    assert len(edges) == 2
    subjects = {e["subject"] for e in edges}
    assert "Max" in subjects
    assert "Person 2" in subjects


async def test_get_edges_by_predicate_keys(memory):
    """get_edges_by_predicate() returns dicts with correct keys."""
    await memory.add_edge("Max", "bevorzugt", "60%")
    edges = await memory.get_edges_by_predicate("bevorzugt")
    assert len(edges) == 1
    e = edges[0]
    assert "subject" in e
    assert "predicate" in e
    assert "object" in e
    assert "weight" in e
    assert "source" in e


# ─── Tests: _load_graph() ─────────────────────────────────────────────────


async def test_load_graph_on_reopen():
    """_load_graph() loads persisted edges when reopening a DB."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row

    sem1 = SemanticMemory()
    await sem1.open(db)
    await sem1.add_edge("Max", "bevorzugt", "60% Helligkeit")
    assert sem1._graph.number_of_edges() == 1

    # Simulate reopen: new SemanticMemory, same DB connection
    sem2 = SemanticMemory()
    await sem2.open(db)
    assert sem2._graph.number_of_edges() == 1
    assert sem2._graph.has_edge("Max", "60% Helligkeit")

    await db.close()


# ─── Tests: get_related() ─────────────────────────────────────────────────


async def test_get_related_returns_reachable_nodes(memory):
    """get_related() returns BFS-reachable nodes at given depth."""
    await memory.add_edge("Max", "lebt_in", "Zimmer")
    await memory.add_edge("Zimmer", "hat_licht", "Schreibtischlampe")
    related = await memory.get_related("Max", depth=2)
    assert "Zimmer" in related
    assert "Schreibtischlampe" in related
    assert "Max" not in related  # start node excluded


async def test_get_related_empty_for_unknown_node(memory):
    """get_related() returns empty list for unknown node."""
    result = await memory.get_related("UnknownNode")
    assert result == []


# ─── Tests: close() ───────────────────────────────────────────────────────


async def test_close_is_noop(memory):
    """close() does nothing (connection owned by ProceduralMemory)."""
    await memory.close()  # Must not raise
    # DB should still be accessible
    assert memory._db is not None


# ─── Tests: edge_count() ──────────────────────────────────────────────────────


async def test_edge_count_returns_zero_when_empty(memory):
    """edge_count() returns 0 for a fresh empty graph."""
    assert memory.edge_count() == 0


async def test_edge_count_returns_correct_count(memory):
    """edge_count() returns the number of edges in the graph."""
    await memory.add_edge("Max", "bevorzugt", "Buero-Licht 60%")
    await memory.add_edge("Max", "mag", "Rollladen zu um 22h")
    assert memory.edge_count() == 2


async def test_edge_count_after_reload(memory):
    """edge_count() is correct after edges are added and graph loaded."""
    await memory.add_edge("Subject", "predicate", "Object")
    # Reload the graph
    await memory._load_graph()
    assert memory.edge_count() == 1
