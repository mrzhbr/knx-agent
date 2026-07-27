from __future__ import annotations

from src.agents.prompts import (
    build_embodied_context,
    build_supervisor_prompt,
    COMFORT_PROMPT,
    SECURITY_PROMPT,
    ROUTER_PROMPT,
)


async def test_build_embodied_context_no_sources():
    result = await build_embodied_context()
    assert isinstance(result, str)
    assert len(result) > 100
    # Should contain all required sections
    assert (
        "Tageszeit" in result
        or "Morgen" in result
        or "Abend" in result
        or "Nacht" in result
        or "Nachmittag" in result
        or "Vormittag" in result
    )


async def test_build_embodied_context_with_awareness():
    from unittest.mock import AsyncMock, MagicMock

    awareness = MagicMock()
    awareness.format_recent = AsyncMock(
        return_value=(
            "14:05 -- Wohnzimmer/Esstisch: OnOff=1\n"
            "14:05 -- Schlafzimmer/Fenster: open=True\n"
            "14:05 -- Schlafzimmer/Heizung: SetValue=22"
        )
    )

    result = await build_embodied_context(awareness=awareness, user="Max")
    assert "Wohnzimmer" in result
    assert "Max" in result


async def test_build_embodied_context_contradictions():
    """rule_engine.get_contradictions() result appears in context output."""
    from unittest.mock import MagicMock

    rule_engine = MagicMock()
    rule_engine.get_contradictions.return_value = ["Schlafzimmer: Heizung an bei offenem Fenster"]

    result = await build_embodied_context(rule_engine=rule_engine)
    assert "Schlafzimmer" in result
    assert "Heizung" in result


async def test_build_embodied_context_rule_engine_contradictions():
    """Contradictions from rule_engine appear in output."""
    from unittest.mock import MagicMock

    rule_engine = MagicMock()
    rule_engine.get_contradictions.return_value = [
        "Küche: AC läuft, Fenster offen",
        "Wohnzimmer: Heizung aktiv, Fenster offen",
    ]

    result = await build_embodied_context(rule_engine=rule_engine)
    assert "Küche" in result
    assert "Wohnzimmer" in result


async def test_build_embodied_context_no_rule_engine_shows_keine():
    """No rule_engine -> 'Keine' in contradictions section."""
    result = await build_embodied_context()
    # Contradictions section should show "Keine" when no rule_engine
    assert "Keine" in result


def test_comfort_prompt_in_german():
    assert "Deutsch" in COMFORT_PROMPT or "deutsch" in COMFORT_PROMPT or "Licht" in COMFORT_PROMPT


def test_security_prompt_has_safety_rules():
    assert "NIEMALS" in SECURITY_PROMPT or "niemals" in SECURITY_PROMPT
    assert "Bestätigung" in SECURITY_PROMPT or "bestätigung" in SECURITY_PROMPT.lower()


def test_supervisor_prompt_has_routing():
    prompt = build_supervisor_prompt()
    assert "comfort" in prompt or "Komfort" in prompt
    assert "security" in prompt or "Sicherheit" in prompt


def test_router_prompt_has_json_schema():
    assert "intent" in ROUTER_PROMPT
    assert "safety_critical" in ROUTER_PROMPT
    assert "JSON" in ROUTER_PROMPT


async def test_build_embodied_context_all_sections():
    """Verify the built context contains all expected section headers from the template."""
    result = await build_embodied_context(
        user="TestUser",
        intent="Licht an",
        weather="15°C, bewölkt",
    )
    # All template fields should be populated
    assert "TestUser" in result
    assert "Licht an" in result


async def test_build_embodied_context_with_memory_layer():
    """build_embodied_context() with memory_layer includes memory sections."""
    from unittest.mock import MagicMock, AsyncMock
    from src.memory.layer import MemoryLayer

    mock_layer = MagicMock(spec=MemoryLayer)
    mock_layer.build_context = AsyncMock(
        return_value="Präferenzen von Max:\n- bevorzugt: 60% Helligkeit"
    )

    result = await build_embodied_context(
        user="Max",
        intent="Licht",
        memory_layer=mock_layer,
    )
    assert "Langzeitgedächtnis" in result or "Präferenzen" in result
    mock_layer.build_context.assert_awaited_once_with(user="Max", intent="Licht")


async def test_build_embodied_context_without_memory_layer_shows_unavailable():
    """build_embodied_context() without memory_layer shows 'Kein Langzeitgedächtnis'."""
    result = await build_embodied_context(user="Max", intent="test")
    assert "Langzeitgedächtnis" in result


async def test_build_embodied_context_no_memory_parameter():
    """Old memory= parameter is no longer accepted — only memory_layer= is valid."""
    import inspect

    sig = inspect.signature(build_embodied_context)
    assert "memory_layer" in sig.parameters
    assert "memory" not in sig.parameters


def test_router_prompt_has_examples():
    """ROUTER_PROMPT contains a Beispiele: section with exactly 5 examples."""
    assert "Beispiele:" in ROUTER_PROMPT
    parts = ROUTER_PROMPT.split("Beispiele:")
    assert len(parts) == 2, "Expected exactly one 'Beispiele:' section"
    examples_section = parts[1]
    count = examples_section.count("Benutzer:")
    assert count == 17, f"Expected 17 'Benutzer:' examples, got {count}"


# ─── Tests: route-aware context filtering ──────────────────────────────────


async def test_build_embodied_context_energy_agent_filters():
    """energy agent: energiezustand section populated, veraenderungen/widersprueche blanked."""
    from unittest.mock import AsyncMock, MagicMock

    awareness = MagicMock()
    awareness.format_recent = AsyncMock(return_value="Wohnzimmer Licht: 0 → 1")
    result = await build_embodied_context(
        awareness=awareness,
        user="Max",
        intent="Verbrauch",
        agent="energy",
    )
    # Energy section present (no hal, so "Nicht verfügbar" which is default)
    # But veraenderungen and widersprueche should be blanked to defaults
    assert "Keine Veränderungen" in result
    assert "Wohnzimmer Licht" not in result  # blanked
    assert "Keine" in result  # contradictions blanked


async def test_build_embodied_context_supervisor_gets_all():
    """supervisor agent (None sections): all data passes through unchanged."""
    from unittest.mock import AsyncMock, MagicMock

    awareness = MagicMock()
    awareness.format_recent = AsyncMock(return_value="Küche Licht: 0 → 1")
    result = await build_embodied_context(
        awareness=awareness,
        user="Max",
        intent="test",
        agent="supervisor",
    )
    assert "Küche Licht" in result


async def test_build_embodied_context_comfort_skips_energy():
    """comfort agent: energiezustand blanked to default."""
    from unittest.mock import MagicMock

    from unittest.mock import AsyncMock

    hal = MagicMock()
    hal.get_full_state_summary = AsyncMock(return_value="Verbrauch: 3.2 kW")

    result = await build_embodied_context(
        hal=hal,
        user="Max",
        intent="Licht",
        agent="comfort",
    )
    # energiezustand not in comfort sections, should be blanked
    assert "3.2 kW" not in result
    assert "Nicht verfügbar" in result


async def test_build_embodied_context_unknown_agent_gets_all():
    """Unknown agent name: behaves same as no agent (all sections populated)."""
    from unittest.mock import AsyncMock, MagicMock

    awareness = MagicMock()
    awareness.format_recent = AsyncMock(return_value="Schlafzimmer Licht: 0 → 1")
    result = await build_embodied_context(
        awareness=awareness,
        user="Max",
        intent="test",
        agent="unknown_agent",
    )
    # unknown agent not in AGENT_CONTEXT_SECTIONS, so all data passes through
    assert "Schlafzimmer Licht" in result


async def test_build_embodied_context_weather_in_output():
    """build_embodied_context() with weather parameter includes Wetter in tageszeit line."""
    result = await build_embodied_context(weather="12 C, klar, windstill")
    assert "Wetter: 12" in result


async def test_build_embodied_context_with_topology():
    """build_embodied_context() with topology includes Raumübersicht."""
    from unittest.mock import MagicMock

    topology = MagicMock()
    topology.format_topology_for_prompt.return_value = (
        "Raumübersicht:\n\nErdgeschoss:\n  Wohnzimmer (living); Fenster: south/west"
    )

    result = await build_embodied_context(topology=topology, user="Max", intent="test")
    assert "Raumübersicht" in result
    assert "Wohnzimmer" in result
    topology.format_topology_for_prompt.assert_called_once()


async def test_build_embodied_context_without_topology():
    """build_embodied_context() without topology shows fallback."""
    result = await build_embodied_context(user="Max", intent="test")
    assert "Keine Raumübersicht verfügbar" in result


async def test_build_embodied_context_all_agents_get_raumuebersicht():
    """All agent types receive raumuebersicht (not blanked)."""
    from unittest.mock import MagicMock

    topology = MagicMock()
    topology.format_topology_for_prompt.return_value = "Raumübersicht:\nTestRaum"

    for agent_name in ["comfort", "security", "energy", "schedule", "supervisor"]:
        result = await build_embodied_context(
            topology=topology, user="Max", intent="test", agent=agent_name
        )
        assert "TestRaum" in result, f"Agent '{agent_name}' should get raumuebersicht"


def test_memory_routing_keys_match_agent_context_sections():
    """MEMORY_ROUTING has entries for all agents in AGENT_CONTEXT_SECTIONS."""
    from src.agents.prompts import MEMORY_ROUTING, AGENT_CONTEXT_SECTIONS

    for agent_name in AGENT_CONTEXT_SECTIONS:
        assert agent_name in MEMORY_ROUTING, f"Missing MEMORY_ROUTING for {agent_name}"


def test_memory_routing_supervisor_is_self_contained():
    """Supervisor memory routing block does not reference 'siehe' or 'oben'."""
    from src.agents.prompts import MEMORY_ROUTING

    block = MEMORY_ROUTING["supervisor"]
    assert "siehe" not in block.lower()
    assert "oben" not in block.lower()


def test_memory_routing_supervisor_has_all_tools():
    """Supervisor memory routing references all supervisor memory tools."""
    from src.agents.prompts import MEMORY_ROUTING

    block = MEMORY_ROUTING["supervisor"]
    assert "remember(" in block or "remember(fact)" in block
    assert "recall(" in block or "recall(query)" in block
    assert "describe_device(" in block
    assert "get_unknown_devices()" in block
    assert "remember_correction(" in block


def test_memory_routing_comfort_has_scene_tools():
    """Comfort memory routing references scene tools it actually has."""
    from src.agents.prompts import MEMORY_ROUTING

    block = MEMORY_ROUTING["comfort"]
    assert "create_scene" in block
    assert "recall_preference" in block
    assert "remember_correction" in block
    # Should NOT reference tools comfort doesn't have as positive instructions
    # ("NIEMALS mit remember()" is fine - it's a prohibition, not an instruction)
    assert "-> remember(" not in block
    assert ": remember(" not in block
    assert "describe_device" not in block


def test_memory_routing_security_only_corrections():
    """Security memory routing only references remember_correction."""
    from src.agents.prompts import MEMORY_ROUTING

    block = MEMORY_ROUTING["security"]
    assert "remember_correction" in block
    assert "create_scene" not in block
    assert "remember(" not in block
    assert "schedule_task" not in block


async def test_build_embodied_context_gedaechtnis_routing_per_agent():
    """Each agent gets its own memory routing block in embodied context."""
    from src.agents.prompts import MEMORY_ROUTING

    for agent_name in ["comfort", "security", "energy", "schedule", "supervisor"]:
        result = await build_embodied_context(user="Test", intent="test", agent=agent_name)
        # Each agent should see its own routing block, not the full supervisor one
        expected_fragment = MEMORY_ROUTING[agent_name][:40]
        assert expected_fragment in result, f"Agent '{agent_name}' missing its memory routing block"


async def test_build_embodied_context_comfort_no_remember_tool():
    """Comfort agent context should not mention remember() tool (it doesn't have it)."""
    result = await build_embodied_context(user="Test", intent="test", agent="comfort")
    # The comfort routing block should not contain remember() references
    # (recall_preference is fine, but not remember)
    assert "remember(fact)" not in result
    assert "describe_device(" not in result


def test_comfort_prompt_uses_recall_preference_not_recall():
    """Comfort prompt examples use recall_preference(topic=...), not recall(query=...)."""
    assert "recall(query=" not in COMFORT_PROMPT
    assert "recall_preference(" in COMFORT_PROMPT
    # Also check the instructional text does not say "recall aufrufen" without "_preference"
    assert "ZUERST recall aufrufen" not in COMFORT_PROMPT
    assert "ZUERST recall_preference aufrufen" in COMFORT_PROMPT
