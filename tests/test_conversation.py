"""Tests for ConversationWindow — per-user-per-room sliding context window."""

from __future__ import annotations

import time

from src.core.conversation import ConversationWindow


def test_add_and_get_context():
    """add() stores exchanges; get_context() returns formatted block."""
    w = ConversationWindow(max_exchanges=5, ttl_minutes=15)
    w.add("!r1:s", "@user:s", "Licht an", "Licht eingeschaltet.")
    w.add("!r1:s", "@user:s", "Wie spät ist es?", "Es ist 22:00 Uhr.")

    ctx = w.get_context("!r1:s", "@user:s")
    assert "Konversationsverlauf:" in ctx
    assert "Benutzer: Licht an" in ctx
    assert "Haus: Licht eingeschaltet." in ctx
    assert "Benutzer: Wie spät ist es?" in ctx
    assert "Haus: Es ist 22:00 Uhr." in ctx


def test_max_exchanges_enforced():
    """Only the last max_exchanges entries are retained."""
    w = ConversationWindow(max_exchanges=5, ttl_minutes=15)
    for i in range(7):
        w.add("!r1:s", "@user:s", f"Frage {i}", f"Antwort {i}")

    ctx = w.get_context("!r1:s", "@user:s")
    # First 2 entries (0, 1) should be evicted
    assert "Frage 0" not in ctx
    assert "Frage 1" not in ctx
    # Last 5 entries (2-6) should be present
    for i in range(2, 7):
        assert f"Frage {i}" in ctx


def test_ttl_expiry():
    """Entries older than ttl_minutes are pruned on get_context()."""
    import collections

    w = ConversationWindow(max_exchanges=5, ttl_minutes=15)
    # Simulate old timestamp: 1000 seconds ago (> 15 min = 900s)
    old_time = time.monotonic() - 1000
    w._store[("!r1:s", "@user:s")] = collections.deque(
        [("alte Frage", "alte Antwort", old_time)], maxlen=5
    )
    ctx = w.get_context("!r1:s", "@user:s")
    assert ctx == ""


def test_empty_room_returns_empty_string():
    """get_context() returns '' for unknown room_id + sender."""
    w = ConversationWindow()
    assert w.get_context("!unknown:server", "@user:s") == ""


def test_clear_removes_history():
    """clear() removes all history for a user in a room."""
    w = ConversationWindow()
    w.add("!r1:s", "@user:s", "test", "response")
    w.clear("!r1:s", "@user:s")
    assert w.get_context("!r1:s", "@user:s") == ""


def test_separate_rooms_isolated():
    """History in room_a does not appear in room_b for the same user."""
    w = ConversationWindow()
    w.add("!room_a:s", "@user:s", "Frage A", "Antwort A")
    w.add("!room_b:s", "@user:s", "Frage B", "Antwort B")

    ctx_a = w.get_context("!room_a:s", "@user:s")
    ctx_b = w.get_context("!room_b:s", "@user:s")

    assert "Frage A" in ctx_a
    assert "Frage B" not in ctx_a
    assert "Frage B" in ctx_b
    assert "Frage A" not in ctx_b


def test_separate_users_isolated_in_same_room():
    """Two users in the same room get isolated conversation histories."""
    w = ConversationWindow()
    w.add("!room:s", "@alice:s", "Alice Frage", "Alice Antwort")
    w.add("!room:s", "@bob:s", "Bob Frage", "Bob Antwort")

    ctx_alice = w.get_context("!room:s", "@alice:s")
    ctx_bob = w.get_context("!room:s", "@bob:s")

    assert "Alice Frage" in ctx_alice
    assert "Bob Frage" not in ctx_alice
    assert "Bob Frage" in ctx_bob
    assert "Alice Frage" not in ctx_bob
