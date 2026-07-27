"""Schedule tools for the Strands schedule agent.

These @tool-decorated async functions manage scheduled tasks and routines.
Dependencies are injected via set_context() at startup.

All scheduled commands are stored as 'llm_dispatch' — the scheduler routes
through the supervisor agent (with LLM access) at execution time.

Docstrings are in German — the LLM sees them as tool descriptions.
"""

from __future__ import annotations

import logging

from strands import tool

from src.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Module-level context. Set via set_context() at startup.
_ctx: ToolContext | None = None


def set_context(ctx: ToolContext) -> None:
    """Set the tool context for all schedule tool functions."""
    global _ctx
    _ctx = ctx


@tool
async def schedule_task(when: str, what: str) -> str:
    """Eine Aufgabe zu einem bestimmten Zeitpunkt planen.

    Speichert die Aufgabe als zeitgesteuerten Befehl. Der Befehl wird
    zum angegebenen Zeitpunkt automatisch ausgeführt.

    Args:
        when: Zeitangabe auf Deutsch, z.B. "in 30 Minuten", "um 22:00", "morgen 08:00"
        what: Beschreibung der Aufgabe, z.B. "Licht im Wohnzimmer ausschalten"
    """
    if _ctx is None:
        return "Planungsdienst nicht verfügbar."
    engine = _ctx.schedule_engine
    if engine is None:
        return "Planungsdienst nicht verfügbar."

    try:
        # All commands stored as llm_dispatch — the supervisor handles interpretation
        schedule_id = await engine.add_one_shot(
            description=what,
            when_str=when,
            action_type="llm_dispatch",
            action_params={"text": what},
        )
        return f"Geplant: {what} ({when}) (ID: {schedule_id})"
    except ValueError as e:
        return f"Zeitangabe nicht erkannt: {e}"
    except Exception as e:
        logger.error("schedule_task failed: %s", e)
        return f"Fehler beim Planen: {e}"


@tool
async def execute_routine(name: str) -> str:
    """Eine gespeicherte Routine ausführen.

    Sucht die Routine anhand des Namens (Teilübereinstimmung) und führt
    alle Aktionen der Routine aus.

    Args:
        name: Name der Routine, z.B. "Bettfertig", "Guten Morgen"
    """
    if _ctx is None:
        return "Planungsdienst nicht verfügbar."
    engine = _ctx.schedule_engine
    memory = _ctx.memory_layer

    if engine is None:
        return "Planungsdienst nicht verfügbar."

    if memory is not None:
        # Verify routine exists before executing
        routine = await memory.procedural.get_routine_by_name(name)
        if routine is None:
            return f"Routine '{name}' nicht gefunden."

    try:
        # execute_routine is a PUBLIC method (no underscore prefix)
        result = await engine.execute_routine(name)
        return result
    except Exception as e:
        logger.error("execute_routine failed: %s", e)
        return f"Fehler beim Ausführen der Routine '{name}': {e}"


@tool
async def list_schedules() -> str:
    """Aktive Zeitpläne und Routinen auflisten.

    Zeigt alle aktiven einmaligen Zeitpläne und aktivierten Routinen
    mit ihren IDs und Beschreibungen.
    """
    if _ctx is None:
        return "Speicher nicht verfügbar."
    memory = _ctx.memory_layer
    if memory is None:
        return "Speicher nicht verfügbar."

    try:
        schedules = await memory.procedural.get_active_schedules()
        routines = await memory.procedural.get_routines(enabled_only=True)

        lines: list[str] = []

        if schedules:
            lines.append("Zeitpläne:")
            for s in schedules:
                sid = s.get("id", "?")
                desc = s.get("description", "?")
                if s.get("cron_expr"):
                    lines.append(f"  [{sid}] {desc} (cron: {s['cron_expr']})")
                elif s.get("one_shot_at"):
                    fire_at = s["one_shot_at"][:16]  # Truncate to HH:MM
                    lines.append(f"  [{sid}] {desc} (einmalig: {fire_at})")
                else:
                    lines.append(f"  [{sid}] {desc}")
        else:
            lines.append("Keine aktiven Zeitpläne.")

        if routines:
            lines.append("Aktive Routinen:")
            for r in routines[:10]:
                rid = r.get("id", "?")
                rname = r.get("name", "?")
                trigger_type = r.get("trigger_type", "")
                cron_expr = r.get("cron_expr", "")
                trigger_value = r.get("trigger_value", "")
                if trigger_type == "time" and cron_expr:
                    # Show human-friendly time if trigger_value looks like HH:MM
                    import re as _re

                    if trigger_value and _re.match(r"^\d{1,2}:\d{2}$", trigger_value):
                        lines.append(f"  [{rid}] {rname} (Zeit: {trigger_value} taeglich)")
                    else:
                        lines.append(f"  [{rid}] {rname} (Zeit: {cron_expr})")
                else:
                    trigger = r.get("trigger", "")
                    lines.append(f"  [{rid}] {rname}" + (f" ({trigger})" if trigger else ""))
        else:
            lines.append("Keine aktiven Routinen.")

        return "\n".join(lines)

    except Exception as e:
        logger.error("list_schedules failed: %s", e)
        return f"Fehler beim Laden der Zeitpläne: {e}"


@tool
async def cancel_schedule(schedule_id: int) -> str:
    """Einen Zeitplan abbrechen.

    Deaktiviert einen geplanten Einzel-Zeitplan anhand seiner ID.
    Routinen werden hiermit nicht deaktiviert.

    Args:
        schedule_id: ID des Zeitplans (aus list_schedules)
    """
    if _ctx is None:
        return "Speicher nicht verfügbar."
    memory = _ctx.memory_layer
    if memory is None:
        return "Speicher nicht verfügbar."

    try:
        found = await memory.procedural.cancel_schedule(schedule_id)
        if found:
            return f"Zeitplan {schedule_id} abgebrochen."
        else:
            return f"Zeitplan {schedule_id} nicht gefunden."
    except Exception as e:
        logger.error("cancel_schedule failed: %s", e)
        return f"Fehler beim Abbrechen des Zeitplans: {e}"


@tool
async def disable_routine(name: str) -> str:
    """Eine Routine deaktivieren.

    Deaktiviert eine bestehende Routine per Name. Nuetzlich um zeitgesteuerte
    oder ereignisgesteuerte Routinen zu stoppen ohne sie zu loeschen.

    Args:
        name: Name der Routine (Teil des Namens genuegt, Gross-/Kleinschreibung egal)
    """
    if _ctx is None:
        return "Speicher nicht verfuegbar."
    memory = _ctx.memory_layer
    if memory is None:
        return "Speicher nicht verfuegbar."

    try:
        routine = await memory.procedural.get_routine_by_name(name)
        if routine is None:
            return f"Routine '{name}' nicht gefunden."
        routine_id = routine["id"]
        await memory.procedural.update_routine(routine_id, enabled=False)
        return f"Routine '{routine['name']}' (ID {routine_id}) deaktiviert."
    except Exception as e:
        logger.error("disable_routine failed: %s", e)
        return f"Fehler beim Deaktivieren der Routine: {e}"


@tool
async def create_triggered_routine(
    name: str,
    description: str,
    trigger_type: str,
    trigger_value: str,
    actions: str,
    autonomy_level: int = 0,
) -> str:
    """Eine ereignisgesteuerte Routine erstellen.

    Erstellt eine Routine die automatisch bei bestimmten Ereignissen ausgefuehrt wird.

    Args:
        name: Name der Routine (z.B. "Guten Morgen", "Markisen bei Regen einfahren")
        description: Beschreibung was die Routine tut
        trigger_type: Ausloser-Typ:
            - "weather": Wetter-Ereignis (z.B. "raining")
            - "presence": Anwesenheit (z.B. "arriving", "leaving")
            - "time": Zeitgesteuert mit Cron-Ausdruck (z.B. "19:05", "5 19 * * 1-5")
        trigger_value: Ausloser-Wert passend zum Typ:
            - weather: "raining"
            - presence: "arriving", "leaving"
            - time: "19:05" (taeglich um 19:05), "5 19 * * 1-5" (Werktage), "0 7 * * 6,0" (Wochenende)
        actions: JSON-String mit Aktionsliste. Jede Aktion hat "tool" und "args",
            oder "description" fuer LLM-Dispatch (z.B. Zusammenfassung generieren).
            Beispiele:
            - [{"tool": "blinds_control", "args": {"room": "Zentralfunktionen", "action": "up", "device_name": "Jalousie Alles", "duration": 1.5}}]
            - [{"description": "Gib eine kurze Zusammenfassung der Nacht"}]
        autonomy_level: 0=nur vorschlagen, 1=ausfuehren+informieren, 2=still ausfuehren
    """
    if _ctx is None:
        return "Planungsdienst nicht verfuegbar."

    import json

    try:
        action_list = json.loads(actions)
    except (json.JSONDecodeError, TypeError) as e:
        return f"Ungueltige JSON-Aktionen: {e}"

    if not isinstance(action_list, list):
        return "Aktionen muessen eine JSON-Liste sein."

    if trigger_type not in ("weather", "presence", "time"):
        return f"Unbekannter Trigger-Typ: {trigger_type}. Erlaubt: weather, presence, time."

    # Handle time trigger: validate and convert to cron expression
    cron_expr = None
    if trigger_type == "time":
        try:
            from croniter import croniter as _croniter_cls
        except ImportError:
            return "Zeitgesteuerte Routinen benoetigen das croniter-Paket (pip install croniter)."
        import re as _re

        if _re.match(r"^\d{1,2}:\d{2}$", trigger_value):
            # Simple HH:MM time — convert to daily cron
            parts = trigger_value.split(":")
            hour = int(parts[0])
            minute = int(parts[1])
            cron_expr = f"{minute} {hour} * * *"
        else:
            # Treat as full cron expression — validate it
            if not _croniter_cls.is_valid(trigger_value):
                return f"Ungueltiger Cron-Ausdruck: '{trigger_value}'. Beispiele: '5 19 * * *' (taeglich), '5 19 * * 1-5' (Werktage)."
            cron_expr = trigger_value

    # Validate device/room references in tool actions against HAL
    hal = _ctx.hal
    for i, action in enumerate(action_list):
        if not isinstance(action, dict) or "tool" not in action:
            continue
        args = action.get("args", {})
        room = args.get("room")
        device_name = args.get("device_name")
        if room and not hal.has_room(room):
            return f"Aktion {i + 1}: Raum '{room}' nicht gefunden. Bitte korrekten Raumnamen verwenden (siehe list_rooms)."
        if room and device_name:
            result = hal.find_device(device_name, room)
            if isinstance(result, str):
                return f"Aktion {i + 1}: {result} Bitte exakten Gerätenamen verwenden (siehe get_room_state)."

    procedural = _ctx.memory_layer.procedural
    try:
        routine_id = await procedural.store_candidate_routine(
            name=name,
            description=description,
            trigger=description,
            actions=action_list,
            trigger_type=trigger_type,
            trigger_value=trigger_value,
            autonomy_level=autonomy_level,
            cron_expr=cron_expr,
        )
        return f"Routine '{name}' erstellt (ID {routine_id}, Trigger: {trigger_type}={trigger_value}, Autonomie-Level {autonomy_level})."
    except Exception as e:
        return f"Fehler beim Erstellen der Routine: {e}"
