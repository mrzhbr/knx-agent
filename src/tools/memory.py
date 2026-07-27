"""Memory tools — Strands @tool functions for remember/recall/device knowledge.

Dependencies injected via set_context() at startup.

Uses _ctx.memory_layer (MemoryLayer) for episodic/semantic/procedural stores.
"""

from __future__ import annotations

import logging
from typing import Any

from strands import tool

from src.tools.context import ToolContext

# MemoryLayer type check (imported lazily to avoid circular imports)
_MemoryLayer_type: type | None = None


def _get_memory_layer_type() -> type | None:
    global _MemoryLayer_type
    if _MemoryLayer_type is None:
        try:
            from src.memory.layer import MemoryLayer

            _MemoryLayer_type = MemoryLayer
        except ImportError:
            pass
    return _MemoryLayer_type


logger = logging.getLogger(__name__)

# Module-level context. Set via set_context() at startup.
_ctx: ToolContext | None = None


def set_context(ctx: ToolContext) -> None:
    """Set the tool context for all memory tool functions."""
    global _ctx
    _ctx = ctx


def _is_memory_layer(memory: Any) -> bool:
    """Return True if memory is a MemoryLayer instance (using lazy isinstance check)."""
    ml_type = _get_memory_layer_type()
    if ml_type is None:
        return False
    return isinstance(memory, ml_type)


def _get_procedural(memory: Any) -> Any:
    """Extract ProceduralMemory from MemoryLayer or return directly."""
    if _is_memory_layer(memory):
        return memory.procedural
    return memory


def _get_episodic(memory: Any) -> Any | None:
    """Extract EpisodicMemory from MemoryLayer or return None."""
    if _is_memory_layer(memory):
        return memory.episodic
    return None


def _get_semantic(memory: Any) -> Any | None:
    """Extract SemanticMemory from MemoryLayer or return None."""
    if _is_memory_layer(memory):
        return memory.semantic
    return None


@tool
async def remember(fact: str) -> str:
    """Speichere eine Präferenz oder einen Fakt dauerhaft.

    Verwende dieses Tool, wenn der Benutzer eine Präferenz äußert oder etwas
    für die Zukunft gespeichert werden soll. Beispiele: 'Max mag das Licht
    im Büro bei 60%', 'Person 2 schläft gerne bei 18°C'.

    Args:
        fact: Der zu speichernde Fakt oder die Präferenz (deutsch).

    Returns:
        Bestätigung der Speicherung.
    """
    if _ctx is None:
        return "Gedächtnis nicht verfügbar."
    memory = _ctx.memory_layer
    if memory is None:
        return "Gedächtnis nicht verfügbar."
    try:
        procedural = _get_procedural(memory)
        await procedural.add_preference(fact, source="user")

        # Also add a semantic edge if MemoryLayer is available
        semantic = _get_semantic(memory)
        if semantic is not None:
            try:
                await semantic.add_edge("Benutzer", "stated", fact[:100], source="stated")
            except Exception as e:
                logger.debug("Semantic edge for remember failed: %s", e)

        return f"Gespeichert: {fact}"
    except Exception as e:
        logger.error("remember failed: %s", e)
        return f"Fehler beim Speichern: {e}"


@tool
async def recall(topic: str) -> str:
    """Suche in gespeicherten Präferenzen und Fakten.

    Durchsucht alle gespeicherten Präferenzen nach dem angegebenen Begriff.
    Wenn MemoryLayer verfügbar, wird zusätzlich eine Vektorsuche durchgeführt.

    Args:
        topic: Suchbegriff (z.B. 'Licht', 'Temperatur', 'Schlafzimmer').

    Returns:
        Liste der gefundenen Präferenzen oder Meldung wenn nichts gefunden.
    """
    if _ctx is None:
        return "Gedächtnis nicht verfügbar."
    memory = _ctx.memory_layer
    if memory is None:
        return "Gedächtnis nicht verfügbar."
    try:
        lines: list[str] = []

        # Procedural keyword search
        procedural = _get_procedural(memory)
        results = await procedural.search_preferences(topic)
        if results:
            lines.append(f"Gefunden ({len(results)}):")
            for r in results[:10]:
                lines.append(f"  - {r['fact']}")

        # Episodic vector search if available
        episodic = _get_episodic(memory)
        if episodic is not None:
            try:
                ep_results = await episodic.search(topic, n=3)
                if ep_results:
                    if not lines:
                        lines.append("Episodisch gefunden:")
                    else:
                        lines.append("Episodisch:")
                    for r in ep_results:
                        lines.append(f"  - {r['text'][:80]}")
            except Exception as e:
                logger.debug("Episodic recall search failed: %s", e)

        if not lines:
            return f"Keine gespeicherten Einträge für '{topic}'."
        return "\n".join(lines)
    except Exception as e:
        logger.error("recall failed: %s", e)
        return f"Fehler beim Abrufen: {e}"


@tool
async def recall_preference(topic: str) -> str:
    """Suche nach Benutzer-Präferenzen zu einem bestimmten Thema.

    Spezialisiertes Recall für Präferenzen — geeignet für den Comfort-Agenten,
    um Vorlieben zu Licht, Temperatur, oder Routinen nachzuschlagen.
    Wenn MemoryLayer verfügbar, wird auch der semantische Graph abgefragt.

    Args:
        topic: Thema der Präferenz (z.B. 'Licht Büro', 'Temperatur Schlafzimmer').

    Returns:
        Gefundene Präferenzen oder Hinweis, dass keine vorhanden sind.
    """
    if _ctx is None:
        return "Gedächtnis nicht verfügbar."
    memory = _ctx.memory_layer
    if memory is None:
        return "Gedächtnis nicht verfügbar."
    try:
        lines: list[str] = []

        # Semantic graph preferences if available
        semantic = _get_semantic(memory)
        if semantic is not None:
            try:
                all_prefs = await semantic.get_preferences("Benutzer")
                # Filter by topic keyword (case-insensitive)
                topic_lower = topic.lower()
                for predicate, targets in all_prefs.items():
                    matching = [
                        t
                        for t in targets
                        if topic_lower in t.lower() or topic_lower in predicate.lower()
                    ]
                    if matching:
                        lines.append(f"Semantisch ({predicate}): {', '.join(matching[:3])}")
            except Exception as e:
                logger.debug("Semantic preference lookup failed: %s", e)

        # Procedural keyword search fallback
        procedural = _get_procedural(memory)
        results = await procedural.search_preferences(topic)
        if results:
            if not lines:
                lines.append(f"Präferenz für '{topic}':")
            else:
                lines.append("Gespeichert:")
            for r in results[:5]:
                lines.append(f"  - {r['fact']}")

        if not lines:
            return f"Keine Präferenz für '{topic}' gespeichert."
        return "\n".join(lines)
    except Exception as e:
        logger.error("recall_preference failed: %s", e)
        return f"Fehler beim Abrufen der Präferenz: {e}"


@tool
async def remember_correction(original: str, correction: str) -> str:
    """Speichere eine Korrektur des Hausgehrins für das Reflexions-System.

    Wenn der Benutzer etwas korrigiert, was das Haus falsch gemacht hat,
    speichere dies als Korrektur für das nächtliche Lernprogramm.

    Args:
        original: Die ursprüngliche (fehlerhafte) Aktion oder Aussage.
        correction: Die korrekte Version oder gewünschte Aktion.

    Returns:
        Bestätigung der gespeicherten Korrektur.
    """
    if _ctx is None:
        return "Gedächtnis nicht verfügbar."
    memory = _ctx.memory_layer
    if memory is None:
        return "Gedächtnis nicht verfügbar."
    try:
        episodic = _get_episodic(memory)
        if episodic is not None:
            await episodic.add_correction(original, correction)
            return f"Korrektur gespeichert: '{original}' → '{correction}'"
        else:
            # Fallback: store as preference
            procedural = _get_procedural(memory)
            await procedural.add_preference(
                f"Korrektur: '{original}' soll '{correction}' sein", source="correction"
            )
            return f"Korrektur als Präferenz gespeichert: '{original}' → '{correction}'"
    except Exception as e:
        logger.error("remember_correction failed: %s", e)
        return f"Fehler beim Speichern der Korrektur: {e}"


@tool
async def describe_device(device_name: str, room: str, description: str) -> str:
    """Speichere eine Beschreibung für ein Gerät.

    Verwende dies, wenn du weißt, wofür ein Gerät ist oder wo es sich befindet.
    Hilft dabei, unbekannte Geräte im System zu verstehen.

    Args:
        device_name: Name des Geräts (wie es in der API heißt).
        room: Raum, in dem das Gerät ist (zur Eindeutigkeit).
        description: Freitextbeschreibung (Verwendungszweck, Ort, Hinweise).

    Returns:
        Bestätigung der gespeicherten Beschreibung.
    """
    if _ctx is None:
        return "Gedächtnis nicht verfügbar."
    memory = _ctx.memory_layer
    if memory is None:
        return "Gedächtnis nicht verfügbar."
    if not room:
        return f"Raum ist erforderlich zur Eindeutigkeit. In welchem Zimmer ist '{device_name}'?"
    hal = _ctx.hal
    uid = f"name:{device_name}:{room}"  # Fallback UID if no HAL
    if hal:
        result = hal.find_device(device_name, room)
        if not isinstance(result, str):
            uid = result.device_id.key
    try:
        procedural = _get_procedural(memory)
        await procedural.set_device_knowledge(
            uid=uid,
            room=room,
            device_name=device_name,
            description=description,
            confirmed=True,
        )
        return f"Gerät '{device_name}' ({room}) beschrieben: {description}"
    except Exception as e:
        logger.error("describe_device failed: %s", e)
        return f"Fehler beim Speichern der Gerätebeschreibung: {e}"


@tool
async def get_unknown_devices(count: int = 5) -> str:
    """Liste Geräte ohne Beschreibung auf.

    Gibt die ältesten unbeschriebenen Geräte aus dem Gerätewissen zurück.
    Hilft beim schrittweisen Aufbau des Gerätewissens.

    Args:
        count: Anzahl der aufzulistenden Geräte (Standard: 5).

    Returns:
        Liste unbeschriebener Geräte oder Meldung, dass alle bekannt sind.
    """
    if _ctx is None:
        return "Gedächtnis nicht verfügbar."
    memory = _ctx.memory_layer
    if memory is None:
        return "Gedächtnis nicht verfügbar."
    try:
        procedural = _get_procedural(memory)
        devices = await procedural.get_unknown_devices(limit=count)
        if not devices:
            return "Alle Geräte wurden beschrieben."
        lines = [f"Noch nicht beschrieben ({len(devices)}):"]
        for d in devices:
            room = d.get("room") or "?"
            name = d.get("device_name") or d.get("uid", "?")
            lines.append(f"  - {name} in {room}")
        return "\n".join(lines)
    except Exception as e:
        logger.error("get_unknown_devices failed: %s", e)
        return f"Fehler beim Abrufen unbeschriebener Geräte: {e}"
