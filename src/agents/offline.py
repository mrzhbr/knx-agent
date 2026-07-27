"""Offline regex fallback for ~30 common German home automation commands.

When there is no internet connection, or as a fast path for simple commands,
try_offline() tests all patterns and returns a (action_type, params) tuple.
execute_offline() then calls the appropriate adapter directly.

v2 note: There is no ToolRegistry. adapters is a dict[str, Any] injected from
IntegrationBus at startup, e.g.:
    {"gira": gira_adapter, "presence": presence_manager}

Pattern ordering matters: more specific patterns must come before generic ones.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)


# Pattern: (compiled_regex, action_type, param_extractor)
# param_extractor is called with the match object and returns a dict of params
_PATTERNS: list[tuple[re.Pattern, str, "Callable"]] = []


def _register(pattern: str, action_type: str, extractor=None):
    """Register a regex pattern with an action type."""
    if extractor is None:

        def default_extractor(match):
            del match
            return {}

        extractor = default_extractor

    _PATTERNS.append((re.compile(pattern, re.IGNORECASE), action_type, extractor))


def _extract_temperature(match: re.Match) -> dict[str, float]:
    try:
        return {"temperature": float(match.group(1).replace(",", "."))}
    except (IndexError, TypeError, ValueError):
        return {}


def _extract_minutes(match: re.Match) -> dict[str, int]:
    try:
        return {"minutes": int(match.group(1))}
    except (IndexError, TypeError, ValueError):
        return {}


# ─── Lights ────────────────────────────────────────────────────────────────
# Specific patterns first (alle lichter), then generic
_register(
    r"alle(?:\s+)licht(?:er)?\s+(?:an|ein|einschalten)",
    "lights_all_on",
    lambda m: {},
)
_register(
    r"alle(?:\s+)licht(?:er)?\s+(?:aus|ausschalten)",
    "lights_all_off",
    lambda m: {},
)
_register(
    r"licht(?:er)?\s+(?:an|ein|einschalten)(?:\s+(?:im?\s+)?(.+))?",
    "lights_on",
    lambda m: {"room": (m.group(1) or "").strip()},
)
_register(
    r"(\w+(?:\s+\w+)?)\s+licht(?:er)?\s+(?:an|ein)",
    "lights_on",
    lambda m: {"room": m.group(1).strip()},
)
_register(
    r"licht(?:er)?\s+(?:aus|ausschalten)(?:\s+(?:im?\s+)?(.+))?",
    "lights_off",
    lambda m: {"room": (m.group(1) or "").strip()},
)
_register(
    r"(\w+(?:\s+\w+)?)\s+licht(?:er)?\s+(?:aus|ausschalten)",
    "lights_off",
    lambda m: {"room": m.group(1).strip()},
)
_register(
    r"^alles\s+(?:aus|ausschalten|ausschalten)$",
    "all_off",
    lambda m: {},
)

# ─── Blinds ─────────────────────────────────────────────────────────────────
# Specific "alle" patterns MUST come before generic jalousie patterns
_register(
    r"alle\s+jalousie(?:n)?\s+hoch",
    "blinds_all_up",
    lambda m: {},
)
_register(
    r"alle\s+jalousie(?:n)?\s+runter",
    "blinds_all_down",
    lambda m: {},
)
_register(
    r"jalousie(?:n)?\s+hoch(?:\s+(?:im?\s+)?(.+))?",
    "blinds_up",
    lambda m: {"room": (m.group(1) or "").strip()},
)
_register(
    r"(.+)\s+jalousie(?:n)?\s+hoch",
    "blinds_up",
    lambda m: {"room": m.group(1).strip()},
)
_register(
    r"jalousie(?:n)?\s+runter(?:\s+(?:im?\s+)?(.+))?",
    "blinds_down",
    lambda m: {"room": (m.group(1) or "").strip()},
)
_register(
    r"(.+)\s+jalousie(?:n)?\s+runter",
    "blinds_down",
    lambda m: {"room": m.group(1).strip()},
)

# ─── Heating ─────────────────────────────────────────────────────────────────
_register(
    r"heizung\s+(?:auf\s+)?(\d+(?:[.,]\d+)?)\s*(?:grad|°c?)?",
    "heating_set",
    _extract_temperature,
)
_register(
    r"temperatur\s+(?:auf\s+)?(\d+(?:[.,]\d+)?)\s*(?:grad|°c?)?",
    "heating_set",
    _extract_temperature,
)

# ─── Named routine execution (must come BEFORE scene patterns) ──────────────
# Only match explicit execution: "routine X", "starte routine X", "routine X ausführen"
# Must NOT match creation requests like "erstelle eine routine..."
_register(
    r"(?:starte|aktiviere)\s+(?:die\s+)?routine\s+(\w+(?:\s+\w+)*)",
    "execute_routine",
    lambda m: {"name": m.group(1).strip()},
)
# Separable verb: "führe routine X aus"
_register(
    r"führ[e]?\s+(?:die\s+)?routine\s+(\w+(?:\s+\w+)*?)\s+aus",
    "execute_routine",
    lambda m: {"name": m.group(1).strip()},
)
# Bare "routine X" — anchored to start of line to avoid matching creation sentences
_register(
    r"^routine\s+(\w+(?:\s+\w+)*?)(?:\s+(?:starten|ausführen|aktivieren))?\s*$",
    "execute_routine",
    lambda m: {"name": m.group(1).strip()},
)


# ─── Presence ────────────────────────────────────────────────────────────────
_register(
    r"wer\s+ist\s+(?:zuhause|da|zu\s+hause)\??",
    "who_is_home",
    lambda m: {},
)
_register(
    r"ist\s+(?:jemand|wer)\s+(?:zuhause|da)\??",
    "who_is_home",
    lambda m: {},
)
_register(
    r"bin\s+(?:gleich\s+)?(?:zuhause|da|zu\s+hause)",
    "arriving_soon",
    lambda m: {},
)
_register(
    r"ich\s+(?:gehe|fahre|bin)\s+(?:jetzt\s+)?(?:weg|außer\s+haus|raus)",
    "leaving",
    lambda m: {},
)

# ─── Door ────────────────────────────────────────────────────────────────────
_register(
    r"t[üu]r\s+(?:aufmachen|öffnen|aufschliessen|aufschließen)",
    "door_open",
    lambda m: {},
)
_register(
    r"(?:mach|öffne|aufmachen)\s+(?:die\s+)?t[üu]r",
    "door_open",
    lambda m: {},
)

# ─── AC / Klimaanlage patterns ──────────────────────────────────────────────
# Specific before generic — order matters!
_register(
    r"klima(?:anlage)?\s+(?:an|ein)(?:\s+(?:im?\s+)?(.+))?",
    "ac_on",
    lambda m: {"room": m.group(1).strip() if m.group(1) else ""},
)
_register(
    r"klima(?:anlage)?\s+(?:aus|ausschalten)(?:\s+(?:im?\s+)?(.+))?",
    "ac_off",
    lambda m: {"room": m.group(1).strip() if m.group(1) else ""},
)
_register(
    r"klima(?:anlage)?\s+(?:auf\s+)?(\d+(?:[.,]\d+)?)\s*(?:grad|°c?)?",
    "ac_set_temp",
    _extract_temperature,
)
_register(
    r"k(?:ü|ue|u)hlen?\s+(?:im?\s+)?(.+)",
    "ac_cool",
    lambda m: {"room": m.group(1).strip()},
)
_register(
    r"(?:ac|a\.?c\.?)\s+(?:aus|off)",
    "ac_off",
    lambda m: {"room": ""},
)


# ─── Status queries ─────────────────────────────────────────────────────────
# Alarm BEFORE generic status (more specific)
_register(
    r"\balarm(?:\s+status)?\s*\??$",
    "alarm_status",
    lambda m: {},
)
_register(
    r"\bfenster(?:\s+offen)?\s*\??$",
    "window_status",
    lambda m: {},
)
# "Was ist an" — only match when the query is specifically about active devices
_register(
    r"^was\s+(?:ist\s+)?(?:gerade\s+)?(?:an|ein(?:geschaltet)?)\s*\??$",
    "what_is_on",
    lambda m: {},
)
# Generic status — must be last and anchored to avoid matching complex queries
_register(
    r"^(?:wie\s+ist\s+)?(?:der\s+)?(?:aktuell[e]?\s+)?status\s*(?:des?\s+hauses?)?\s*\??$",
    "house_status",
    lambda m: {},
)


# ─── Schedule patterns ──────────────────────────────────────────────────────
# Order matters: must come before try_offline. Specific before generic.
_register(
    r"timer\s+(\d+)\s*(?:minuten?|min)",
    "schedule_timer",
    _extract_minutes,
)
_register(
    r"erinnere?\s+(?:mich\s+)?(?:um\s+|in\s+)?(\d{1,2}):(\d{2})",
    "schedule_reminder",
    lambda m: {"time": f"{m.group(1)}:{m.group(2)}"},
)
_register(
    r"wecker\s+(\d{1,2}):(\d{2})",
    "schedule_alarm",
    lambda m: {"time": f"{m.group(1)}:{m.group(2)}"},
)


def try_offline(text: str) -> tuple[str, dict] | None:
    """Test text against all offline regex patterns.

    Returns (action_type, params) if a pattern matches, or None if no match.
    First match wins.
    """
    text = text.strip()
    for pattern, action_type, extractor in _PATTERNS:
        m = pattern.search(text)
        if m:
            try:
                params = extractor(m)
                logger.debug("Offline match: action=%s params=%s", action_type, params)
                return action_type, params
            except Exception as e:
                logger.warning("Offline extractor error for %s: %s", action_type, e)
                return action_type, {}
    return None


async def execute_offline(
    action_type: str,
    params: dict,
    adapters: dict,
) -> str:
    """Execute an offline action by calling the appropriate adapter directly.

    adapters dict should contain:
        "gira": Gira adapter instance (duck-typed)
        "presence": PresenceManager instance (optional)
    """
    gira = adapters.get("gira")
    presence = adapters.get("presence")

    if action_type in ("lights_on", "lights_off", "lights_all_off", "lights_all_on"):
        if gira is None:
            return "Gira X1 nicht verfügbar."
        room = params.get("room", "")
        return f"offline:{action_type}:room={room}"

    elif action_type in ("blinds_up", "blinds_down", "blinds_all_up", "blinds_all_down"):
        if gira is None:
            return "Gira X1 nicht verfügbar."
        room = params.get("room", "")
        return f"offline:{action_type}:room={room}"

    elif action_type == "heating_set":
        temperature = params.get("temperature", 20.0)
        if gira is None:
            return "Gira X1 nicht verfügbar."
        return f"offline:{action_type}:temperature={temperature}"

    elif action_type == "all_off":
        return "offline:all_off"

    elif action_type == "who_is_home":
        if presence is None:
            return "Anwesenheitserkennung nicht verfügbar."
        try:
            return presence.format_for_prompt()
        except Exception as e:
            return f"Fehler bei Anwesenheitsabfrage: {e}"

    elif action_type == "door_open":
        return "Türöffnung erfordert Bestätigung und aktive Verbindung."

    elif action_type in ("house_status", "what_is_on", "window_status", "alarm_status"):
        return f"offline:{action_type}"

    elif action_type in ("arriving_soon", "leaving"):
        return f"Verstanden. ({action_type})"

    elif action_type == "ac_on":
        room = params.get("room", "")
        return f"offline:ac_on:room={room}"

    elif action_type == "ac_off":
        room = params.get("room", "")
        return f"offline:ac_off:room={room}"

    elif action_type == "ac_set_temp":
        temperature = params.get("temperature", 22.0)
        return f"offline:ac_set_temp:temperature={temperature}"

    elif action_type == "ac_cool":
        room = params.get("room", "")
        return f"offline:ac_cool:room={room}"

    elif action_type in ("schedule_timer", "schedule_reminder", "schedule_alarm"):
        # Timer and reminders need online mode (llm_dispatch at execution time)
        return "Timer und Erinnerungen können nur online gesetzt werden."

    elif action_type == "execute_routine":
        # Routine execution works offline — dispatches pre-defined tool calls
        name = params.get("name", "")
        schedule_engine = adapters.get("schedule")
        if schedule_engine is None:
            return "Planungsdienst nicht verfügbar."
        try:
            import inspect

            if inspect.iscoroutinefunction(schedule_engine.execute_routine):
                return await schedule_engine.execute_routine(name)
            else:
                return str(schedule_engine.execute_routine(name))
        except Exception as e:
            return f"Fehler beim Ausführen der Routine '{name}': {e}"

    else:
        return f"Unbekannte Offline-Aktion: {action_type}"
