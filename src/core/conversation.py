"""Per-user conversation window for short-term context.

Maintains a sliding window of recent exchanges per (room_id, sender) with TTL expiration.
History is injected into LLM prompts as a Konversationsverlauf block.
Not persisted — in-memory only, intentionally lightweight.
"""

from __future__ import annotations

import time
from collections import deque


class ConversationWindow:
    """In-memory sliding window of recent conversation exchanges per user per room.

    Maintains last N exchanges with TTL expiration. Thread-safe for single-event-loop use.
    History is formatted as a German Konversationsverlauf block for LLM injection.

    Keyed by (room_id, sender) so users in shared rooms get isolated history.

    Usage::

        window = ConversationWindow(max_exchanges=5, ttl_minutes=15)
        window.add("!room1:server", "@user:server", "Mach das Licht an", "Licht eingeschaltet.")
        ctx = window.get_context("!room1:server", "@user:server")
    """

    def __init__(self, max_exchanges: int = 5, ttl_minutes: int = 15) -> None:
        self._max = max_exchanges
        self._ttl = ttl_minutes * 60.0  # seconds
        self._store: dict[tuple[str, str], deque[tuple[str, str, float]]] = {}

    def add(self, room_id: str, sender: str, user_text: str, assistant_text: str) -> None:
        """Append an exchange to the user's room history, enforcing max_exchanges."""
        key = (room_id, sender)
        if key not in self._store:
            self._store[key] = deque(maxlen=self._max)
        self._store[key].append((user_text, assistant_text, time.monotonic()))

    def get_context(self, room_id: str, sender: str) -> str:
        """Return formatted German context block for a user in a room, pruning expired entries.

        Returns empty string if no valid history exists.
        """
        key = (room_id, sender)
        if key not in self._store:
            return ""

        now = time.monotonic()
        valid = [(u, a, ts) for (u, a, ts) in self._store[key] if now - ts < self._ttl]

        if not valid:
            return ""

        lines = [
            "Konversationsverlauf: (rein informativ — jede neue Anfrage muss "
            "tatsaechlich ausgefuehrt werden, auch bei Wiederholung)"
        ]
        for user_text, assistant_text, _ in valid:
            lines.append(f"Benutzer: {user_text}")
            lines.append(f"Haus: {assistant_text}")
        return "\n".join(lines)

    def clear(self, room_id: str, sender: str) -> None:
        """Clear all history for a user in a room."""
        self._store.pop((room_id, sender), None)
