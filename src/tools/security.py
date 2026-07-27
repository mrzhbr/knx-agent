"""Security tools for the Strands security agent.

These @tool-decorated async functions handle alarm, door, and security queries.
Guarded tools are enforced by guardrails — not by LLM reasoning.

Dependencies injected via set_context().
"""

from __future__ import annotations

import logging
from typing import Any

from strands import tool  # type: ignore[import-not-found]

from src.core.guardrails import RequestSource, get_request_security_context, is_action_confirmed
from src.core.types import HealthState
from src.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Module-level context. Set via set_context() at startup.
_ctx: ToolContext | None = None


def set_context(ctx: ToolContext) -> None:
    """Set the tool context for all security tool functions."""
    global _ctx
    _ctx = ctx


def _authorize_admin_action(action: str, *, require_confirmation: bool = False) -> str | None:
    """Fail closed unless a known admin user owns this request."""
    request = get_request_security_context()
    if request is None or request.source is not RequestSource.USER:
        return "Sicherheitskritische Aktion verweigert: ungültiger Anfragekontext."
    if request.user_profile is None or getattr(request.user_profile, "trust_level", "") != "admin":
        return "Du hast keine Berechtigung für diese Aktion."
    if require_confirmation and not is_action_confirmed(action):
        return "Diese Aktion erfordert eine ausdrückliche Bestätigung."
    return None


@tool
async def alarm_control(action: str) -> str:
    """Alarm-Anlage steuern: scharf oder unscharf stellen.

    WICHTIG: Diese Aktion wird durch Sicherheits-Guardrails geschützt.
    Scharf stellen erfordert: niemand zuhause + alle Fenster geschlossen.

    Args:
        action: "arm" (scharf stellen) oder "disarm" (unscharf stellen)
    """
    from src.hal.models import SensorClass

    if _ctx is None:
        return "API nicht erreichbar."
    hal = _ctx.hal

    if action not in ("arm", "disarm"):
        return f"Unbekannte Aktion '{action}'. Erlaubt: arm, disarm."

    denial = _authorize_admin_action(f"alarm_{action}", require_confirmation=action == "disarm")
    if denial:
        return denial

    if action == "arm":
        presence = _ctx.presence_manager
        try:
            nobody_home = await presence.async_nobody_home()
        except Exception as exc:
            logger.warning("Alarm presence authorization failed: %s", exc)
            return "Alarm kann nicht scharf gestellt werden: Anwesenheit ist unbekannt."
        if not nobody_home:
            return "Alarm kann nicht scharf gestellt werden: Jemand ist zuhause."
        if not bool(getattr(presence, "window_state_known", False)):
            return "Alarm kann nicht scharf gestellt werden: Fensterstatus ist unbekannt."
        open_windows = list(getattr(presence, "open_windows", []))
        if open_windows:
            return f"Alarm kann nicht scharf gestellt werden: Fenster offen: {', '.join(open_windows)}."

    alarm_adapters = hal.get_sensors_by_class(SensorClass.ALARM_READINESS)
    if not alarm_adapters:
        return "Keine Alarm-Anlage gefunden."

    value = "1" if action == "arm" else "0"
    action_word = "scharf gestellt" if action == "arm" else "unscharf gestellt"

    try:
        for adapter in alarm_adapters:
            await hal.execute(adapter.device_id, "set_binary", value=value)
        return f"Alarm-Anlage {action_word}."
    except Exception as e:
        return f"Fehler beim Steuern der Alarm-Anlage: {e}"


@tool
async def door_open() -> str:
    """Haustür öffnen (T24M Türstation).

    WICHTIG: Diese Aktion erfordert explizite Bestätigung des Benutzers.
    Guardrails werden den Aufruf abfangen wenn keine Bestätigung vorliegt.
    """
    from src.hal.models import DeviceType

    if _ctx is None:
        return "Türsteuerung nicht verfügbar."

    denial = _authorize_admin_action("door_open", require_confirmation=True)
    if denial:
        return denial

    hal = _ctx.hal
    door_station: Any = hal.get_platform("door_station")

    # Health check (only if both platform and monitor are configured)
    if _ctx.platform_health is not None and door_station is not None:
        health = _ctx.platform_health.get_status("door_station")
        if health is not None and health.state != HealthState.HEALTHY:
            return door_station.error_message

    try:
        door_devices = hal.get_devices_by_type(DeviceType.DOOR_STATION)
        if door_devices:
            await hal.execute(door_devices[0].device_id, "open_door")
            return "Tür geöffnet."
        return "Keine Türsteuerung gefunden."
    except Exception as e:
        return f"Fehler beim Öffnen der Tür: {e}"


@tool
async def get_security_status() -> str:
    """Aktuellen Sicherheitsstatus abfragen: Alarm, Fenster, Türen, letzte Ereignisse.

    Gibt einen Überblick über den Sicherheitsstatus des Hauses zurück.
    """
    from src.hal.models import SensorClass

    if _ctx is None:
        return "Sicherheitsstatus nicht verfügbar."
    hal = _ctx.hal
    camera_platform: Any = _ctx.hal.get_platform("frigate")

    lines: list[str] = ["=== Sicherheitsstatus ==="]

    # Alarm readiness sensors
    alarm_adapters = hal.get_sensors_by_class(SensorClass.ALARM_READINESS)
    for adapter in alarm_adapters:
        try:
            state = await adapter.read_state()
            val = state.state.get("Binary") or state.state.get("OnOff")
            status = "bereit" if str(val) == "1" else "nicht bereit"
            lines.append(f"Alarm {adapter.info.name}: {status}")
        except Exception:
            lines.append(f"Alarm {adapter.info.name}: Status unbekannt")

    # Window/door sensors
    window_adapters = hal.get_sensors_by_class(SensorClass.WINDOW)
    open_count = 0
    for adapter in window_adapters:
        try:
            state = await adapter.read_state()
            val = state.state.get("Binary") or state.state.get("is_on")
            if str(val) == "1":
                lines.append(f"  Offen: {adapter.info.name} ({adapter.info.room})")
                open_count += 1
        except Exception:
            pass

    if open_count == 0:
        lines.append("Alle Fenster/Türen: geschlossen")
    else:
        lines.append(f"Offene Fenster/Türen: {open_count}")

    # Recent camera events
    if camera_platform:
        try:
            recent_events = await camera_platform.get_events(hours=4)
            if recent_events:
                lines.append(f"Kamera-Ereignisse (letzte 4h): {len(recent_events)}")
            else:
                lines.append("Keine Kamera-Ereignisse in letzten 4h.")
        except Exception:
            lines.append("Kamera-Status: nicht verfügbar")

    return "\n".join(lines)


@tool
async def get_camera_events(hours: int = 24) -> str:
    """Kamera-Erkennungsereignisse abfragen.

    Gibt eine Zusammenfassung der erkannten Personen und Fahrzeuge zurück.

    Args:
        hours: Zeitraum in Stunden (Standard: 24)
    """
    if _ctx is None:
        return "Kamera-Erkennung nicht konfiguriert."

    camera_platform: Any = _ctx.hal.get_platform("frigate")
    if camera_platform is None:
        return "Kamera-Erkennung nicht konfiguriert."

    if _ctx.platform_health is not None:
        health = _ctx.platform_health.get_status("frigate")
        if health is not None and health.state != HealthState.HEALTHY:
            return camera_platform.error_message

    try:
        events = await camera_platform.get_events(hours=hours)
        if not events:
            return f"Keine Kamera-Ereignisse in den letzten {hours} Stunden."

        # Group by camera
        by_camera: dict[str, list[dict]] = {}
        for event in events:
            cam = event.get("camera", "unknown")
            by_camera.setdefault(cam, []).append(event)

        lines = [f"Kamera-Ereignisse (letzte {hours}h): {len(events)} gesamt"]
        for cam, cam_events in sorted(by_camera.items()):
            lines.append(f"  {cam}: {len(cam_events)} Ereignisse")

        return "\n".join(lines)
    except Exception as e:
        return f"Fehler beim Abrufen der Kamera-Ereignisse: {e}"
