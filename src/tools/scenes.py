"""Scene tools for the Strands comfort agent.

These @tool-decorated async functions manage user-defined scenes.
Dependencies are injected via set_context() at startup.

Docstrings are in German — the LLM sees them as tool descriptions.
"""

from __future__ import annotations

import json
import logging

from strands import tool

from src.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Module-level context. Set via set_context() at startup.
_ctx: ToolContext | None = None


def set_context(ctx: ToolContext) -> None:
    """Set the tool context for all scene tool functions."""
    global _ctx
    _ctx = ctx


@tool
async def create_scene(name: str, description: str, actions: str) -> str:
    """Eine neue Szene erstellen und in der Datenbank speichern.

    Szenen sind benannte Ablaeufe, die jederzeit per execute_scene ausgefuehrt werden koennen.
    Die Aktionen muessen im JSON-Format angegeben werden (dasselbe Format wie execute_batch).

    Beispiel fuer gueltige Aktionen:
      [{"room": "Wohnzimmer", "action": "lights_off"},
       {"room": "Schlafzimmer", "action": "blinds_down"},
       {"room": "Flur", "action": "lights_on", "brightness": 20}]

    Unterstuetzte Aktionen: lights_on, lights_off, blinds_up, blinds_down, set_brightness,
    set_temperature, lights_on_all, lights_off_all, execute_batch

    Args:
        name: Name der Szene, z.B. "Bettfertig", "Filmabend", "Runterkommen"
        description: Kurze Beschreibung was die Szene tut
        actions: JSON-Array mit Aktionen im execute_batch-Format
    """
    if _ctx is None:
        return "Szenen-Dienst nicht verfuegbar."
    try:
        action_list = json.loads(actions)
    except (json.JSONDecodeError, ValueError):
        return (
            "Ungueltige Aktionen: JSON-Format erwartet. "
            'Beispiel: [{"room": "Wohnzimmer", "action": "lights_off"}]'
        )

    # Validate room/device references against HAL
    if isinstance(action_list, list):
        hal = _ctx.hal
        for i, action in enumerate(action_list):
            if not isinstance(action, dict):
                continue
            room = action.get("room", "")
            device_name = action.get("device_name", "")
            if room and not hal.has_room(room):
                return f"Aktion {i + 1}: Raum '{room}' nicht gefunden. Bitte korrekten Raumnamen verwenden (siehe list_rooms)."
            if room and device_name:
                result = hal.find_device(device_name, room)
                if isinstance(result, str):
                    return f"Aktion {i + 1}: {result} Bitte exakten Gerätenamen verwenden (siehe get_room_state)."

    try:
        scene_id = await _ctx.memory_layer.procedural.add_scene(name, description, actions)
        return f'Szene "{name}" gespeichert (ID: {scene_id}).'
    except Exception as e:
        logger.error("create_scene failed: %s", e)
        return f"Fehler beim Erstellen der Szene: {e}"


@tool
async def execute_scene(name: str) -> str:
    """Eine gespeicherte Szene ausfuehren.

    Laedt die Szene aus der Datenbank und fuehrt alle Aktionen aus.
    Bei unklarem Namen wird eine Liste der passenden Szenen zurueckgegeben.

    Args:
        name: Name der Szene, z.B. "Bettfertig", "Filmabend"
    """
    from src.tools.comfort import execute_batch

    if _ctx is None:
        return "Szenen-Dienst nicht verfuegbar."
    scenes = await _ctx.memory_layer.procedural.get_scene_by_name(name)
    if len(scenes) == 0:
        return f'Szene "{name}" nicht gefunden.'
    if len(scenes) > 1:
        names = ", ".join(s["name"] for s in scenes)
        return f"Mehrere Szenen gefunden: {names}. Bitte genauer angeben."
    scene = scenes[0]
    actions = scene["actions"]
    actions_json = json.dumps(actions, ensure_ascii=False)
    return await execute_batch(actions_json)


@tool
async def list_scenes() -> str:
    """Alle gespeicherten Szenen auflisten.

    Gibt eine Liste aller Szenen mit Namen und Beschreibungen zurueck.
    """
    if _ctx is None:
        return "Szenen-Dienst nicht verfuegbar."
    scenes = await _ctx.memory_layer.procedural.get_all_scenes()
    if not scenes:
        return "Keine Szenen gespeichert."
    lines = ["Verfuegbare Szenen:"]
    for scene in scenes:
        desc = scene.get("description") or ""
        lines.append(f"  - {scene['name']}: {desc}")
    return "\n".join(lines)


@tool
async def delete_scene(name: str) -> str:
    """Eine gespeicherte Szene loeschen.

    Bei unklarem Namen wird eine Liste der passenden Szenen zurueckgegeben.

    Args:
        name: Name der Szene, z.B. "Bettfertig"
    """
    if _ctx is None:
        return "Szenen-Dienst nicht verfuegbar."
    scenes = await _ctx.memory_layer.procedural.get_scene_by_name(name)
    if len(scenes) == 0:
        return f'Szene "{name}" nicht gefunden.'
    if len(scenes) > 1:
        names = ", ".join(s["name"] for s in scenes)
        return f"Mehrere Szenen gefunden: {names}. Bitte genauer angeben."
    scene = scenes[0]
    deleted = await _ctx.memory_layer.procedural.delete_scene(scene["id"])
    if deleted:
        return f'Szene "{scene["name"]}" geloescht.'
    return f'Szene "{scene["name"]}" konnte nicht geloescht werden.'
