"""Comfort tools for the Strands comfort agent.

These @tool-decorated async functions control lights, blinds, and heating.
Dependencies are injected via set_context() at startup.

Docstrings are in German — the LLM sees them as tool descriptions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from strands import tool

from src.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Module-level context. Set via set_context() at startup.
_ctx: ToolContext | None = None


def set_context(ctx: ToolContext) -> None:
    """Set the tool context for all comfort tool functions."""
    global _ctx
    _ctx = ctx


@tool
async def lights_control(
    room: str,
    action: str,
    device_name: str = "",
    brightness: int = -1,
) -> str:
    """Licht in einem Raum steuern: ein- oder ausschalten, dimmen.

    Args:
        room: Name des Raums (z.B. "Wohnzimmer", "Schlafzimmer")
        action: Aktion — "on" (einschalten), "off" (ausschalten), "dim" (dimmen)
        device_name: Optionaler Gerätename wenn nur ein bestimmtes Licht gesteuert werden soll
        brightness: Helligkeit 0-100 bei action="dim" (-1 = nicht gesetzt)
    """
    from src.hal.models import DeviceType

    if _ctx is None:
        return "API nicht erreichbar."
    hal = _ctx.hal

    if not hal.has_room(room):
        return f"Raum '{room}' nicht gefunden."

    if device_name:
        result = hal.find_device(device_name, room)
        if isinstance(result, str):
            return result
        adapters = [result]
    else:
        adapters = [
            a
            for a in hal.find_room(room)
            if a.info.device_type
            in (
                DeviceType.DIMMABLE_LIGHT,
                DeviceType.SWITCH,
                DeviceType.LIGHT,
                DeviceType.COLORED_LIGHT,
            )
        ]
        if not adapters:
            return f"Keine Lichter in '{room}' gefunden."

    # Validate action upfront
    if action not in ("on", "off", "dim"):
        return f"Unbekannte Aktion '{action}'. Erlaubt: on, off, dim."
    if action == "dim" and brightness < 0:
        return "Helligkeit (0-100) für 'dim' angeben."
    if action == "dim":
        brightness = min(brightness, 100)

    # Build coroutines
    pairs: list[tuple[str, Any]] = []
    for adapter in adapters:
        if action == "on":
            coro = hal.execute(adapter.device_id, "turn_on")
        elif action == "off":
            coro = hal.execute(adapter.device_id, "turn_off")
        else:  # dim
            coro = hal.execute(adapter.device_id, "turn_on", brightness=brightness)
        pairs.append((adapter.info.name, coro))

    # Execute in parallel
    results = await asyncio.gather(*[coro for _, coro in pairs], return_exceptions=True)

    # Collect outcomes
    changed: list[str] = []
    failures: list[str] = []
    for dev_name, outcome in zip([n for n, _ in pairs], results):
        if isinstance(outcome, BaseException):
            logger.error("Failed to control %s: %s", dev_name, outcome)
            failures.append(f"{dev_name}: {outcome}")
        else:
            changed.append(dev_name)

    if not changed and not failures:
        return f"Keine Lichter zum Steuern in '{room}'."
    action_word = {
        "on": "eingeschaltet",
        "off": "ausgeschaltet",
        "dim": f"gedimmt auf {brightness}%",
    }.get(action, action)
    msg = f"{', '.join(changed)} in {room} {action_word}." if changed else ""
    if failures:
        msg += (" " if msg else "") + f"Fehler: {'; '.join(failures)}"
    return msg


@tool
async def blinds_control(
    room: str,
    action: str,
    device_name: str = "",
    duration: float = 0,
) -> str:
    """Jalousien in einem Raum steuern: hoch, runter, stoppen, oder teilweise bewegen.

    Args:
        room: Name des Raums (z.B. "Wohnzimmer", "Elternschlafzimmer")
        action: Aktion — "up" (hoch), "down" (runter), "stop" (stoppen)
        device_name: Optionaler Gerätename wenn nur eine bestimmte Jalousie gesteuert werden soll
        duration: Fahrtdauer in Sekunden (0 = komplett fahren, 0.5-2.0 = ein Stück)
    """
    from src.hal.models import DeviceType

    if _ctx is None:
        return "API nicht erreichbar."
    hal = _ctx.hal

    if not hal.has_room(room):
        return f"Raum '{room}' nicht gefunden."

    if device_name:
        result = hal.find_device(device_name, room)
        if isinstance(result, str):
            return result
        adapters = [result]
    else:
        adapters = [a for a in hal.find_room(room) if a.info.device_type == DeviceType.BLIND]
        if not adapters:
            return f"Keine Jalousien in '{room}' gefunden."

    # Clamp duration to safe range
    duration = max(0.0, min(duration, 10.0))

    changed: list[str] = []

    if action == "stop":
        stop_pairs: list[tuple[str, Any]] = [
            (adapter.info.name, hal.execute(adapter.device_id, "stop")) for adapter in adapters
        ]
        stop_results = await asyncio.gather(
            *[coro for _, coro in stop_pairs], return_exceptions=True
        )
        for dev_name, outcome in zip([n for n, _ in stop_pairs], stop_results):
            if not isinstance(outcome, BaseException):
                changed.append(dev_name)
    elif action in ("up", "down"):
        cmd = "open" if action == "up" else "close"
        move_pairs: list[tuple[str, Any]] = [
            (adapter.info.name, hal.execute(adapter.device_id, cmd)) for adapter in adapters
        ]
        move_results = await asyncio.gather(
            *[coro for _, coro in move_pairs], return_exceptions=True
        )
        for dev_name, outcome in zip([n for n, _ in move_pairs], move_results):
            if not isinstance(outcome, BaseException):
                changed.append(dev_name)

        # Phase 2: Wait once, then stop ALL blinds (if duration set)
        if duration > 0:
            await asyncio.sleep(duration)
            stop_pairs2: list[tuple[str, Any]] = [
                (adapter.info.name, hal.execute(adapter.device_id, "stop")) for adapter in adapters
            ]
            await asyncio.gather(*[coro for _, coro in stop_pairs2], return_exceptions=True)
    else:
        return f"Unbekannte Aktion '{action}'. Erlaubt: up, down, stop."

    if not changed:
        return f"Keine Jalousien gesteuert in '{room}'."
    action_word = {"up": "hoch", "down": "runter", "stop": "gestoppt"}.get(action, action)
    return f"Jalousien {action_word}: {', '.join(changed)} in {room}."


@tool
async def heating_control(
    room: str,
    temperature: float,
) -> str:
    """Heizung in einem Raum auf eine Zieltemperatur einstellen.

    Args:
        room: Name des Raums (z.B. "Wohnzimmer", "Schlafzimmer")
        temperature: Zieltemperatur in Grad Celsius (15–30)
    """
    from src.hal.models import DeviceType

    if _ctx is None:
        return "API nicht erreichbar."
    hal = _ctx.hal

    if temperature < 15 or temperature > 30:
        return f"Temperatur {temperature}°C außerhalb Bereich 15–30°C."

    if not hal.has_room(room):
        return f"Raum '{room}' nicht gefunden."

    adapters = [a for a in hal.find_room(room) if a.info.device_type == DeviceType.HEATING]
    if not adapters:
        # Fallback: any heating device in house
        adapters = hal.get_devices_by_type(DeviceType.HEATING)
    if not adapters:
        return "Kein Heizgerät gefunden."

    adapter = adapters[0]
    try:
        await hal.execute(adapter.device_id, "set_temperature", value=temperature)
        return f"Heizung in {room} auf {temperature}°C gesetzt."
    except Exception as e:
        return f"Fehler beim Einstellen der Heizung: {e}"


@tool
async def get_room_state(room: str) -> str:
    """Aktuellen Zustand aller Geräte in einem Raum abfragen.

    Args:
        room: Name des Raums (z.B. "Küche", "Schlafzimmer")
    """
    if _ctx is None:
        return "Gerätestatus nicht verfügbar."
    hal = _ctx.hal

    if not hal.has_room(room):
        return f"Raum '{room}' nicht gefunden."

    state = await hal.get_room_state_formatted(room)
    if not state:
        return f"Keine Statusdaten für '{room}' verfügbar."

    lines = [f"{room}:"]
    for device_name, dp_values in state.items():
        dp_str = ", ".join(f"{k}={v}" for k, v in dp_values.items())
        lines.append(f"  {device_name}: {dp_str}")
    return "\n".join(lines)


@tool
async def list_rooms(device_type: str = "") -> str:
    """Alle Räume und ihre Geräte auflisten.

    Args:
        device_type: Optional — nur Geräte dieses Typs zeigen.
            Mögliche Werte: "dimmable_light", "switch", "blind", "heating", "" (alle)
    """
    if _ctx is None:
        return "Geräteregister nicht verfügbar."
    hal = _ctx.hal

    rooms = hal.get_all_rooms()
    lines = []
    for room_name in sorted(rooms.keys()):
        infos = rooms[room_name]
        if device_type:
            from src.hal.models import DeviceType

            try:
                target = DeviceType(device_type)
            except ValueError:
                return f"Unbekannter Gerätetyp: '{device_type}'. Verfügbar: {', '.join(t.value for t in DeviceType)}"
            infos = [i for i in infos if i.device_type == target]
        if infos:
            names = ", ".join(i.name for i in infos)
            lines.append(f"{room_name}: {names}")

    if not lines:
        return "Keine Geräte gefunden."
    return "\n".join(lines)


@tool
async def execute_batch(commands: str) -> str:
    """Mehrere Geraetebefehle gleichzeitig ausfuehren.

    Fuer Szenen wie "Haus fuer die Nacht fertig machen" -- alle Befehle werden parallel gesendet.

    Args:
        commands: JSON-String mit einer Liste von Befehlen. Jeder Befehl ist ein Objekt mit:
            - room (str): Raumname (bei trigger_scene optional)
            - action (str): "lights_on", "lights_off", "lights_dim",
                "blinds_up", "blinds_down", "blinds_stop",
                "heating_set", "ac_on", "ac_off", "ac_set_temp", "ac_set_mode",
                "trigger_scene"
            - device_name (str, optional): Bestimmtes Geraet statt alle im Raum
                (bei trigger_scene: Name der Szene)
            - brightness (int, optional): Helligkeit 0-100 bei lights_dim
            - temperature (float, optional): Zieltemperatur bei heating_set / ac_set_temp
            - mode (str, optional): AC-Modus bei ac_set_mode
            - duration (float, optional): Fahrtdauer in Sekunden bei blinds_up/blinds_down
                (0 = komplett fahren, 0.5-2.0 = ein Stueck/ein bisschen)

            Beispiel: [{"room": "Wohnzimmer", "action": "lights_off"},
                       {"room": "Schlafzimmer", "action": "blinds_down"},
                       {"room": "Kueche", "action": "blinds_up", "duration": 1.0},
                       {"action": "trigger_scene", "device_name": "Abendszene"}]
    """
    import json as _json

    from src.hal.models import DeviceType

    if _ctx is None:
        return "API nicht erreichbar."
    hal = _ctx.hal

    # Parse commands JSON
    try:
        cmd_list = _json.loads(commands)
    except (_json.JSONDecodeError, TypeError) as e:
        return f"Ungueltige JSON-Eingabe: {e}"

    if not isinstance(cmd_list, list):
        return "Befehle muessen eine JSON-Liste sein."

    if not cmd_list:
        return "Keine Befehle angegeben."

    # Action -> DeviceType mapping
    _LIGHT_TYPES = (
        DeviceType.DIMMABLE_LIGHT,
        DeviceType.SWITCH,
        DeviceType.LIGHT,
        DeviceType.COLORED_LIGHT,
    )
    _ACTION_DEVICE_TYPES: dict[str, tuple] = {
        "lights_on": _LIGHT_TYPES,
        "lights_off": _LIGHT_TYPES,
        "lights_dim": _LIGHT_TYPES,
        "blinds_up": (DeviceType.BLIND,),
        "blinds_down": (DeviceType.BLIND,),
        "blinds_stop": (DeviceType.BLIND,),
        "heating_set": (DeviceType.HEATING,),
        "ac_on": (DeviceType.CLIMATE,),
        "ac_off": (DeviceType.CLIMATE,),
        "ac_set_temp": (DeviceType.CLIMATE,),
        "ac_set_mode": (DeviceType.CLIMATE,),
        "trigger_scene": (DeviceType.SCENE,),
    }
    _VALID_ACTIONS = set(_ACTION_DEVICE_TYPES.keys())

    # Build (device_name, coro) pairs
    pairs: list[tuple[str, Any]] = []
    skipped: list[str] = []
    # Blinds that need duration-based stop after initial move
    duration_blinds: list[tuple[float, list[tuple[str, Any]]]] = []

    for cmd in cmd_list:
        if not isinstance(cmd, dict):
            skipped.append("Ungueltige Befehlsstruktur (kein Objekt)")
            continue

        action = cmd.get("action", "")
        room = cmd.get("room", "")
        device_name = cmd.get("device_name", "")

        if action not in _VALID_ACTIONS:
            skipped.append(f"Unbekannte Aktion '{action}'")
            continue

        if action == "trigger_scene":
            # Look up SCENE device by device_name
            if not device_name:
                skipped.append("trigger_scene: device_name fehlt")
                continue
            scene_devices = hal.get_devices_by_type(DeviceType.SCENE)
            matching = [d for d in scene_devices if device_name.lower() in d.info.name.lower()]
            if not matching:
                skipped.append(f"Szene '{device_name}' nicht gefunden")
                continue
            adapter = matching[0]
            pairs.append((adapter.info.name, hal.execute(adapter.device_id, "trigger")))
            continue

        # Room-based commands
        if room and not hal.has_room(room):
            skipped.append(f"Raum '{room}' nicht gefunden")
            continue

        if not room:
            skipped.append(f"Aktion '{action}': room fehlt")
            continue

        device_types = _ACTION_DEVICE_TYPES[action]

        if device_name:
            found = hal.find_device(device_name, room)
            if isinstance(found, str):
                skipped.append(f"Geraet '{device_name}' nicht gefunden")
                continue
            adapters = [found]
        else:
            adapters = [a for a in hal.find_room(room) if a.info.device_type in device_types]
            if not adapters:
                skipped.append(f"Keine Geraete fuer '{action}' in '{room}'")
                continue

        # Translate action to HAL command
        for adapter in adapters:
            dev_label = f"{room} {adapter.info.name}"
            if action == "lights_on":
                pairs.append((dev_label, hal.execute(adapter.device_id, "turn_on")))
            elif action == "lights_off":
                pairs.append((dev_label, hal.execute(adapter.device_id, "turn_off")))
            elif action == "lights_dim":
                brightness = int(cmd.get("brightness", 50))
                brightness = max(0, min(brightness, 100))
                pairs.append(
                    (dev_label, hal.execute(adapter.device_id, "turn_on", brightness=brightness))
                )
            elif action in ("blinds_up", "blinds_down"):
                blind_cmd = "open" if action == "blinds_up" else "close"
                pairs.append((dev_label, hal.execute(adapter.device_id, blind_cmd)))
            elif action == "blinds_stop":
                pairs.append((dev_label, hal.execute(adapter.device_id, "stop")))
            elif action == "heating_set":
                temperature = float(cmd.get("temperature", 20))
                pairs.append(
                    (
                        dev_label,
                        hal.execute(adapter.device_id, "set_temperature", value=temperature),
                    )
                )
            elif action == "ac_on":
                pairs.append((dev_label, hal.execute(adapter.device_id, "turn_on")))
            elif action == "ac_off":
                pairs.append((dev_label, hal.execute(adapter.device_id, "turn_off")))
            elif action == "ac_set_temp":
                temperature = float(cmd.get("temperature", 22))
                pairs.append(
                    (
                        dev_label,
                        hal.execute(adapter.device_id, "set_temperature", value=temperature),
                    )
                )
            elif action == "ac_set_mode":
                mode = cmd.get("mode", "")
                pairs.append((dev_label, hal.execute(adapter.device_id, "set_mode", mode=mode)))

        # Track blinds that need stop-after-duration
        if action in ("blinds_up", "blinds_down"):
            duration = float(cmd.get("duration", 0))
            duration = max(0.0, min(duration, 10.0))
            if duration > 0:
                blind_ids = [(f"{room} {a.info.name}", a.device_id) for a in adapters]
                duration_blinds.append((duration, blind_ids))

    if not pairs:
        skip_msg = f" ({'; '.join(skipped)})" if skipped else ""
        return f"Keine ausfuehrbaren Befehle{skip_msg}."

    # Execute all in parallel
    results = await asyncio.gather(*[coro for _, coro in pairs], return_exceptions=True)

    # Phase 2: For duration-based blinds, wait then stop
    if duration_blinds:
        # Group by duration to minimize sleep calls
        by_duration: dict[float, list[tuple[str, Any]]] = {}
        for dur, blind_ids in duration_blinds:
            by_duration.setdefault(dur, []).extend(blind_ids)

        for dur, blind_ids in sorted(by_duration.items()):
            await asyncio.sleep(dur)
            stop_coros = [hal.execute(dev_id, "stop") for _, dev_id in blind_ids]
            await asyncio.gather(*stop_coros, return_exceptions=True)

    # Correlate results
    succeeded: list[str] = []
    errors: list[str] = []
    for (label, _), result in zip(pairs, results):
        if isinstance(result, BaseException):
            logger.error("Batch command failed for %s: %s", label, result)
            errors.append(f"{label}: {result}")
        else:
            succeeded.append(label)

    success_count = len(succeeded)
    error_count = len(errors)

    parts = []
    if succeeded:
        parts.append(
            f"{success_count} Befehle ausgefuehrt: {', '.join(succeeded[:10])}"
            + ("..." if len(succeeded) > 10 else "")
        )
    if errors:
        parts.append(f"{error_count} Fehler: {'; '.join(errors)}")
    if skipped:
        parts.append(f"{len(skipped)} uebersprungen: {'; '.join(skipped)}")

    return ". ".join(parts) + "."


# ─── AC comfort tools ──────────────────────────────────────────────────────

# German mode names → AC DriveMode enum names
_AC_MODE_MAP = {
    "kuehlen": "COOLER",
    "kühlen": "COOLER",
    "heizen": "HEATER",
    "auto": "AUTO",
    "trocknen": "DEHUM",
    "lueften": "FAN",
    "lüften": "FAN",
}


@tool
async def ac_control(
    room: str,
    action: str,
    temperature: float = -1,
    mode: str = "",
    fan_speed: str = "",
) -> str:
    """Klimaanlage in einem Raum steuern.

    Steuert die Mitsubishi-Klimaanlage: ein/aus, Temperatur setzen, Modus wechseln.

    Args:
        room: Raumname (z.B. "Wohnzimmer", "Schlafzimmer")
        action: Aktion — "on" (einschalten), "off" (ausschalten), "set" (Temperatur), "mode" (Modus)
        temperature: Zieltemperatur in °C bei action="set" (16–31°C), -1 = nicht gesetzt
        mode: Klimamodus bei action="mode": kuehlen, heizen, auto, trocknen, lueften
        fan_speed: Lüftergeschwindigkeit (optional, derzeit nicht genutzt)

    Returns:
        Deutsche Bestätigung der Aktion.
    """
    from src.hal.models import DeviceType

    if _ctx is None:
        return "Klimaanlage nicht verfügbar."
    hal = _ctx.hal

    # Prefer HAL-based AC control via CLIMATE devices
    climate_devices = hal.get_devices_by_type(DeviceType.CLIMATE)
    room_lower = room.lower()
    matching = [d for d in climate_devices if d.info.room.lower() == room_lower]

    if matching:
        adapter = matching[0]
        try:
            if action == "on":
                await hal.execute(adapter.device_id, "turn_on")
                return f"Klimaanlage {room} eingeschaltet."
            elif action == "off":
                await hal.execute(adapter.device_id, "turn_off")
                return f"Klimaanlage {room} ausgeschaltet."
            elif action == "set":
                if temperature < 16 or temperature > 31:
                    return (
                        f"Ungültige Temperatur: {temperature}°C (muss zwischen 16 und 31°C sein)."
                    )
                await hal.execute(adapter.device_id, "set_temperature", value=temperature)
                return f"Klimaanlage {room}: Temperatur auf {temperature}°C gesetzt."
            elif action == "mode":
                mode_lower = mode.lower().strip()
                drive_mode = _AC_MODE_MAP.get(mode_lower)
                if drive_mode is None:
                    valid = ", ".join(_AC_MODE_MAP.keys())
                    return f"Unbekannter Modus '{mode}'. Verfügbar: {valid}."
                await hal.execute(adapter.device_id, "set_mode", mode=drive_mode)
                return f"Klimaanlage {room}: Modus auf '{mode}' gesetzt."
            else:
                return f"Unbekannte Aktion '{action}'. Verwende: on, off, set, mode."
        except ValueError as e:
            return f"Fehler: {e}"
        except Exception as e:
            logger.error("ac_control (HAL) failed: %s", e)
            return f"Fehler bei Klimaanlagen-Steuerung: {e}"

    return "Klimaanlage nicht verfügbar."


@tool
async def get_ac_status(room: str = "") -> str:
    """Klimaanlagen-Status abfragen.

    Zeigt den aktuellen Status der Klimaanlage(n): An/Aus, Temperatur, Raumtemperatur, Verbrauch.

    Args:
        room: Raumname für einzelne Einheit, oder leer für alle Einheiten

    Returns:
        Deutscher Status-Text der Klimaanlage(n).
    """
    from src.hal.models import DeviceType

    if _ctx is None:
        return "Klimaanlage nicht verfügbar."
    hal = _ctx.hal

    climate_devices = hal.get_devices_by_type(DeviceType.CLIMATE)
    if room:
        room_lower = room.lower()
        climate_devices = [d for d in climate_devices if d.info.room.lower() == room_lower]

    if not climate_devices:
        target = f"Raum '{room}'" if room else "alle Räume"
        return f"Keine Klimaanlage für {target} gefunden."

    lines = []
    for adapter in climate_devices:
        try:
            state = await adapter.read_state()
            s = state.state
            power_str_raw = str(s.get("power", "off"))
            is_on = power_str_raw in ("on",) or "ON" in str(s.get("PowerOnOff", ""))
            power_str = "An" if is_on else "Aus"
            set_temp = s.get("temperature", "?")
            room_temp = s.get("room_temperature", "?")
            power_watt = s.get("power_watt", "?")
            lines.append(
                f"Klimaanlage {adapter.info.room}: {power_str}, "
                f"{set_temp}°C eingestellt, Raumtemperatur {room_temp}°C, "
                f"Verbrauch {power_watt}W"
            )
        except Exception as e:
            lines.append(f"Klimaanlage {adapter.info.room}: Status unbekannt ({e})")
    return "\n".join(lines) if lines else "Kein Status verfügbar."
