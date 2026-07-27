"""Rule management tools for the Strands comfort agent.

These @tool-decorated async functions allow the LLM to configure built-in
house rules, list rule status, and approve custom rules suggested by reflection.

Dependencies are injected via set_context() at startup.
Docstrings are in German — the LLM sees them as tool descriptions.
"""

from __future__ import annotations

import logging

from strands import tool

from src.hal.rules import BUILT_IN_RULES
from src.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Module-level context. Set via set_context() at startup.
_ctx: ToolContext | None = None


def set_context(ctx: ToolContext) -> None:
    """Set the tool context for all rule tool functions."""
    global _ctx
    _ctx = ctx


@tool
async def configure_house_rule(
    rule_name: str,
    enabled: bool | None = None,
    hours: float | None = None,
    after_hour: int | None = None,
    threshold_watts: float | None = None,
) -> str:
    """Aktiviert, deaktiviert oder konfiguriert eine Hausregel.

    Verfügbare Regeln: heating_window_conflict, ac_window_conflict,
    lights_nobody_home, blinds_late, energy_spike, solar_heating_conflict,
    ac_openplan_window, lights_openplan_nobody_home.

    Args:
        rule_name: Name der Regel (z.B. 'lights_nobody_home')
        enabled: True zum Aktivieren, False zum Deaktivieren (optional)
        hours: Stundenschwelle für lights_nobody_home (optional, Standard: 2.0)
        after_hour: Uhrzeit-Schwelle für blinds_late (optional, Standard: 23)
        threshold_watts: Watt-Schwelle für energy_spike (optional, Standard: 5000)
    """
    if _ctx is None:
        return "Fehler: Regelmotor nicht verfügbar."
    rule_engine = _ctx.rule_engine
    if rule_engine is None:
        return "Fehler: Regelmotor nicht verfügbar."
    try:
        await rule_engine.configure_rule(
            rule_name,
            enabled=enabled,
            hours=hours,
            after_hour=after_hour,
            threshold_watts=threshold_watts,
        )
        if enabled is True:
            return f"Regel '{rule_name}' wurde aktiviert."
        elif enabled is False:
            return f"Regel '{rule_name}' wurde deaktiviert."
        else:
            return f"Regel '{rule_name}' wurde konfiguriert."
    except Exception as e:
        logger.error("configure_house_rule failed for %s: %s", rule_name, e)
        return f"Fehler beim Konfigurieren der Regel '{rule_name}': {e}"


@tool
async def list_house_rules() -> str:
    """Listet alle Hausregeln mit ihrem aktuellen Status und Konfiguration auf.

    Zeigt eingebaute Regeln und benutzerdefinierte Regeln (vom Reflexions-Agenten
    vorgeschlagen). Für jede Regel werden Status (aktiv/inaktiv) und Parameter
    angezeigt.
    """
    if _ctx is None:
        return "Fehler: Regelmotor nicht verfügbar."
    rule_engine = _ctx.rule_engine
    if rule_engine is None:
        return "Fehler: Regelmotor nicht verfügbar."

    lines = ["=== Hausregeln ===", "", "Eingebaute Regeln:"]

    for rule_name in BUILT_IN_RULES:
        # Get config or default
        config = rule_engine._rule_configs.get(rule_name)
        if config is None:
            status = "aktiv"
            params_str = "(Standard)"
        else:
            status = "aktiv" if config.enabled else "inaktiv"
            if config.params:
                params_str = ", ".join(f"{k}={v}" for k, v in config.params.items())
            else:
                params_str = "(Standard)"
        lines.append(f"  - {rule_name}: {status} {params_str}")

    custom_rules = getattr(rule_engine, "_custom_rules", [])
    if custom_rules:
        lines.append("")
        lines.append("Benutzerdefinierte Regeln:")
        for rule in custom_rules:
            status = "aktiv" if rule.enabled and rule.approved else "ausstehend"
            if not rule.approved:
                status = "nicht genehmigt"
            lines.append(
                f"  - {rule.name}: {status} ({rule.state_key} {rule.operator} {rule.threshold})"
            )
    else:
        lines.append("")
        lines.append("Keine benutzerdefinierten Regeln vorhanden.")

    return "\n".join(lines)


@tool
async def approve_custom_rule(rule_name: str) -> str:
    """Genehmigt eine vom Reflexions-Agenten vorgeschlagene benutzerdefinierte Regel.

    Aktiviert die Regel für die laufende Auswertung und speichert die Genehmigung.

    Args:
        rule_name: Name der zu genehmigenden Regel
    """
    if _ctx is None:
        return "Fehler: Regelmotor nicht verfügbar."
    rule_engine = _ctx.rule_engine
    if rule_engine is None:
        return "Fehler: Regelmotor nicht verfügbar."
    try:
        result = await rule_engine.approve_custom_rule(rule_name)
        if result:
            return f"Regel '{rule_name}' wurde genehmigt und ist jetzt aktiv."
        else:
            return f"Regel '{rule_name}' nicht gefunden. Bitte den genauen Namen prüfen."
    except Exception as e:
        logger.error("approve_custom_rule failed for %s: %s", rule_name, e)
        return f"Fehler beim Genehmigen der Regel '{rule_name}': {e}"
