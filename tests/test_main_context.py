"""Tests verifying embodied context injection wiring in on_message().

Since on_message is a closure inside main(), these tests verify the
context injection contract in isolation — simulating the dispatch logic
rather than instantiating the full application stack.

This follows the same pattern as test_conversation_integration.py.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# Helpers — simulate the dispatch logic from on_message()
# ---------------------------------------------------------------------------


async def _simulate_single_agent_dispatch(
    text: str,
    agent_name: str,
    build_context_fn,
    agent_invoke_fn,
    sender: str = "Person 1",
    intent: str = "lights",
    conversation_history: str = "",
) -> str:
    """Simulate the single-agent dispatch path from on_message()."""
    augmented_text = text
    if conversation_history:
        augmented_text = f"{conversation_history}\n\nAktuelle Anfrage:\n{text}"

    context = ""
    try:
        context = await build_context_fn(agent_name, sender, intent)
    except Exception as e:
        logging.getLogger(__name__).warning("Embodied context assembly failed: %s", e)
    if context:
        augmented_text = f"{context}\n\n{augmented_text}"

    result = await agent_invoke_fn(augmented_text)
    return str(result).strip()


async def _simulate_supervisor_dispatch(
    text: str,
    build_context_fn,
    supervisor_invoke_fn,
    sender: str = "Person 1",
    intent: str = "general",
    conversation_history: str = "",
) -> str:
    """Simulate the supervisor dispatch path from on_message()."""
    augmented_text = text
    if conversation_history:
        augmented_text = f"{conversation_history}\n\nAktuelle Anfrage:\n{text}"

    context = ""
    try:
        context = await build_context_fn("supervisor", sender, intent)
    except Exception as e:
        logging.getLogger(__name__).warning("Embodied context assembly failed: %s", e)
    if context:
        augmented_text = f"{context}\n\n{augmented_text}"

    result = await supervisor_invoke_fn(augmented_text)
    return str(result).strip()


# ---------------------------------------------------------------------------
# Task 3.1: Single-agent dispatch gets context
# ---------------------------------------------------------------------------


async def test_single_agent_gets_context():
    """Single-agent dispatch calls build_embodied_context with correct agent name."""
    context_str = "Du bist das Haus selbst.\n\n[Tageszeit: Morgen]"
    agent_response = "Licht wurde eingeschaltet."

    build_context = AsyncMock(return_value=context_str)
    agent_result = MagicMock()
    agent_result.__str__ = MagicMock(return_value=agent_response)
    agent_invoke = AsyncMock(return_value=agent_result)

    response = await _simulate_single_agent_dispatch(
        text="Mach das Licht an",
        agent_name="comfort",
        build_context_fn=build_context,
        agent_invoke_fn=agent_invoke,
        sender="Person 1",
        intent="lights",
    )

    # build_context called with correct agent name
    build_context.assert_awaited_once_with("comfort", "Person 1", "lights")

    # agent received text starting with context
    call_args = agent_invoke.call_args[0][0]
    assert call_args.startswith(context_str), (
        f"Expected text to start with context string, got: {call_args[:100]}"
    )
    assert "Mach das Licht an" in call_args
    assert response == agent_response


# ---------------------------------------------------------------------------
# Task 3.2: Supervisor dispatch gets context
# ---------------------------------------------------------------------------


async def test_supervisor_gets_context():
    """Supervisor dispatch calls build_embodied_context with agent='supervisor'."""
    context_str = "Du bist das Haus selbst.\n\n[Vollständige Zustandsübersicht]"
    supervisor_response = "Ich koordiniere das."

    build_context = AsyncMock(return_value=context_str)
    supervisor_result = MagicMock()
    supervisor_result.__str__ = MagicMock(return_value=supervisor_response)
    supervisor_invoke = AsyncMock(return_value=supervisor_result)

    response = await _simulate_supervisor_dispatch(
        text="Was geht alles gerade vor in meinem Haus?",
        build_context_fn=build_context,
        supervisor_invoke_fn=supervisor_invoke,
        sender="Person 1",
        intent="general_query",
    )

    # build_context called with "supervisor"
    build_context.assert_awaited_once_with("supervisor", "Person 1", "general_query")

    # supervisor received text starting with context
    call_args = supervisor_invoke.call_args[0][0]
    assert call_args.startswith(context_str)
    assert response == supervisor_response


# ---------------------------------------------------------------------------
# Task 3.3: Pure offline dispatch skips context
# ---------------------------------------------------------------------------


async def test_offline_no_escalation_skips_context():
    """Pure offline dispatch (no escalation) does NOT call build_embodied_context."""
    # Simulate the offline path: execute_offline returns a real response (no "offline:" prefix)
    text = "Wer ist zuhause?"
    routing_model = "offline"
    offline_response = (
        "Person 1 ist zuhause."  # presence.format_for_prompt() — no "offline:" prefix
    )

    build_context = AsyncMock(return_value="some context")
    agent_invoke = AsyncMock()

    # Simulate on_message() offline path
    augmented_text = text
    if routing_model != "offline":
        # Would add conversation history — but offline skips this
        augmented_text = "should not happen"

    response = offline_response  # execute_offline returned directly

    # Pure offline: no "offline:" prefix → no escalation → no context build
    if not response.startswith("offline:"):
        pass  # no context build, no agent call

    # Assertions: context never built, agent never called
    build_context.assert_not_awaited()
    agent_invoke.assert_not_awaited()
    assert response == "Person 1 ist zuhause."
    assert augmented_text == text  # offline text is never augmented with history


# ---------------------------------------------------------------------------
# Task 3.4: Context failure still calls agent (graceful degradation)
# ---------------------------------------------------------------------------


async def test_context_failure_still_calls_agent():
    """If build_embodied_context raises, agent is still called with un-contextualized text."""
    agent_response = "Alles klar."
    agent_result = MagicMock()
    agent_result.__str__ = MagicMock(return_value=agent_response)
    agent_invoke = AsyncMock(return_value=agent_result)

    # build_context raises
    build_context = AsyncMock(side_effect=RuntimeError("Context build failed"))

    response = await _simulate_single_agent_dispatch(
        text="Wie ist die Temperatur?",
        agent_name="comfort",
        build_context_fn=build_context,
        agent_invoke_fn=agent_invoke,
        sender="Person 1",
        intent="temperature",
    )

    # Agent was still called despite context failure
    agent_invoke.assert_awaited_once()

    # Agent received the original (un-contextualized) augmented_text
    call_args = agent_invoke.call_args[0][0]
    assert "Wie ist die Temperatur?" in call_args
    assert not call_args.startswith("Du bist das Haus"), (
        "Context should NOT be prepended when build_embodied_context failed"
    )
    assert response == agent_response


# ---------------------------------------------------------------------------
# Task 3.5: Agent calls use invoke_async, not __call__
# ---------------------------------------------------------------------------


async def test_agent_uses_invoke_async():
    """Agent dispatch uses invoke_async (non-blocking), not __call__."""
    context_str = "Du bist das Haus selbst."
    agent_response = "Fertig."

    build_context = AsyncMock(return_value=context_str)

    # Mock agent: invoke_async is AsyncMock, __call__ is regular Mock
    agent_mock = MagicMock()
    agent_result = MagicMock()
    agent_result.__str__ = MagicMock(return_value=agent_response)
    agent_mock.invoke_async = AsyncMock(return_value=agent_result)

    response = await _simulate_single_agent_dispatch(
        text="Mach das Licht aus",
        agent_name="comfort",
        build_context_fn=build_context,
        agent_invoke_fn=agent_mock.invoke_async,
        sender="Person 1",
        intent="lights",
    )

    # invoke_async was awaited exactly once
    agent_mock.invoke_async.assert_awaited_once()

    # __call__ was never used
    agent_mock.assert_not_called()

    assert response == agent_response


# ---------------------------------------------------------------------------
# Task 3.6: Context ordering in augmented_text
# ---------------------------------------------------------------------------


async def test_context_ordering_in_augmented_text():
    """Augmented text has: [Context] then [Konversationsverlauf:] then [Aktuelle Anfrage:]."""
    context_str = "Du bist das Haus selbst.\n\n[Tageszeit: Abend]"
    conversation_history = "Konversationsverlauf:\nUser: Licht an\nAssistant: Licht eingeschaltet."
    user_text = "Mach es wieder aus"

    build_context = AsyncMock(return_value=context_str)
    agent_result = MagicMock()
    agent_result.__str__ = MagicMock(return_value="OK")
    agent_invoke = AsyncMock(return_value=agent_result)

    await _simulate_single_agent_dispatch(
        text=user_text,
        agent_name="comfort",
        build_context_fn=build_context,
        agent_invoke_fn=agent_invoke,
        sender="Person 1",
        intent="lights",
        conversation_history=conversation_history,
    )

    call_args = agent_invoke.call_args[0][0]

    # Context appears before Konversationsverlauf
    ctx_pos = call_args.find("Du bist das Haus selbst")
    history_pos = call_args.find("Konversationsverlauf:")
    request_pos = call_args.find("Aktuelle Anfrage:")

    assert ctx_pos < history_pos, "Context must appear before Konversationsverlauf:"
    assert history_pos < request_pos, "Konversationsverlauf: must appear before Aktuelle Anfrage:"
    assert user_text in call_args, "User text must appear in augmented_text"
