"""Integration tests for ConversationWindow injection logic in on_message().

Since on_message is a closure inside main(), these tests verify the
conversation injection contract in isolation rather than instantiating
the full application stack.
"""

from __future__ import annotations

from src.core.conversation import ConversationWindow


def test_conversation_prepend_for_llm_route():
    """LLM-routed messages get conversation history prepended to augmented_text."""
    window = ConversationWindow(max_exchanges=5, ttl_minutes=15)
    sender = "@person1:server"
    window.add("!room:server", sender, "Licht an", "Licht eingeschaltet.")

    # Simulate the augmented_text logic from on_message()
    text = "Wie war das nochmal mit dem Licht?"
    routing_model = "haiku"  # not "offline"

    augmented_text = text
    if routing_model != "offline":
        history = window.get_context("!room:server", sender)
        if history:
            augmented_text = f"{history}\n\nAktuelle Anfrage:\n{text}"

    assert "Konversationsverlauf:" in augmented_text
    assert "Licht an" in augmented_text
    assert "Aktuelle Anfrage:" in augmented_text
    assert text in augmented_text


def test_conversation_not_prepended_for_offline():
    """Offline-routed messages do NOT get conversation history prepended."""
    window = ConversationWindow(max_exchanges=5, ttl_minutes=15)
    sender = "@person1:server"
    window.add("!room:server", sender, "Licht an", "Licht eingeschaltet.")

    text = "Licht aus"
    routing_model = "offline"

    # Simulate the augmented_text logic from on_message()
    augmented_text = text
    if routing_model != "offline":
        history = window.get_context("!room:server", sender)
        if history:
            augmented_text = f"{history}\n\nAktuelle Anfrage:\n{text}"

    # Offline: augmented_text == original text
    assert augmented_text == text
    assert "Konversationsverlauf:" not in augmented_text


def test_conversation_isolated_between_users_in_same_room():
    """Two users in the same room don't see each other's conversation history."""
    window = ConversationWindow(max_exchanges=5, ttl_minutes=15)
    window.add("!room:server", "@alice:server", "Alice fragt", "Antwort für Alice")
    window.add("!room:server", "@bob:server", "Bob fragt", "Antwort für Bob")

    alice_ctx = window.get_context("!room:server", "@alice:server")
    bob_ctx = window.get_context("!room:server", "@bob:server")

    assert "Alice fragt" in alice_ctx
    assert "Bob fragt" not in alice_ctx
    assert "Bob fragt" in bob_ctx
    assert "Alice fragt" not in bob_ctx
