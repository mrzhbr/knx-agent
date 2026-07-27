"""CLI command to inspect the latest health snapshot from brain.db.

Usage:
    python -m src health-snapshot [--db <path>] [--json]
"""

from __future__ import annotations

import asyncio
import json
import sys

from src.background.health_snapshot import HealthSnapshot, compute_delta
from src.memory.procedural import ProceduralMemory

_DEFAULT_DB = "data/brain.db"


def _format_snapshot(snapshot: HealthSnapshot, delta=None) -> str:
    """Format a HealthSnapshot as human-readable German text."""
    lines = [f"Gesundheitsbericht vom {snapshot.timestamp[:19]}"]
    lines.append("")

    # Platform health
    lines.append("Plattformen:")
    if snapshot.platform_health:
        for name, ok in snapshot.platform_health.items():
            status = "OK" if ok else "FEHLER"
            lines.append(f"  - {name}: {status}")
    else:
        lines.append("  - Keine Plattformen")

    # Memory
    lines.append("")
    lines.append("Gedaechtnis:")
    ep_str = str(snapshot.memory_episodic_count)
    if delta and delta.memory_episodic_count is not None:
        sign = "+" if delta.memory_episodic_count >= 0 else ""
        ep_str += f" ({sign}{delta.memory_episodic_count})"
    lines.append(f"  - Episodisch: {ep_str}")
    sem_str = str(snapshot.memory_semantic_edges)
    if delta and delta.memory_semantic_edges is not None:
        sign = "+" if delta.memory_semantic_edges >= 0 else ""
        sem_str += f" ({sign}{delta.memory_semantic_edges})"
    lines.append(f"  - Semantisch (Kanten): {sem_str}")
    pref_str = str(snapshot.memory_procedural_prefs)
    if delta and delta.memory_procedural_prefs is not None:
        sign = "+" if delta.memory_procedural_prefs >= 0 else ""
        pref_str += f" ({sign}{delta.memory_procedural_prefs})"
    lines.append(f"  - Praeferenzen: {pref_str}")

    # Corrections
    lines.append("")
    lines.append("Korrekturen (letzte 7 Tage):")
    lines.append(f"  - Anzahl: {snapshot.corrections_count_7d}")
    if snapshot.repeated_corrections:
        lines.append("  - Haeufigste:")
        for c in snapshot.repeated_corrections[:3]:
            lines.append(
                f"    - '{c.get('original', '?')}' → '{c.get('correction', '?')}' ({c.get('count', 0)}x)"
            )

    # Routines
    lines.append("")
    lines.append("Routinen:")
    lines.append(f"  - Aktiv (bestaetigt): {snapshot.active_routines}")
    lines.append(f"  - Kandidaten: {snapshot.candidate_routines}")
    lines.append(f"  - Neue Kandidaten (7 Tage): {snapshot.pattern_detector_hits}")
    total_fires = snapshot.routine_fires_success + snapshot.routine_fires_failure
    lines.append(
        f"  - Ausgefuehrt (7 Tage): {total_fires} ({snapshot.routine_fires_success} OK, {snapshot.routine_fires_failure} Fehler)"
    )

    # Anomalies
    lines.append("")
    lines.append("Anomalien:")
    lines.append(
        f"  - Gesamt: {snapshot.anomalies_found} (hoch: {snapshot.anomalies_high}, mittel: {snapshot.anomalies_medium}, niedrig: {snapshot.anomalies_low})"
    )

    # Insights
    if snapshot.energy_insights:
        lines.append("")
        lines.append("Energie-Einblicke:")
        for insight in snapshot.energy_insights[:3]:
            lines.append(f"  - {insight}")

    if snapshot.free_text_suggestion:
        lines.append("")
        lines.append(f"Vorschlag: {snapshot.free_text_suggestion}")

    return "\n".join(lines)


async def _run(db_path: str, as_json: bool = False) -> None:
    """Open ProceduralMemory, read latest snapshot(s), print formatted output."""
    memory = ProceduralMemory(db_path)
    try:
        await memory.open()
        rows = await memory.get_latest_health_snapshot(n=2)

        if not rows:
            print("Kein Snapshot vorhanden.")
            return

        snapshot = HealthSnapshot.model_validate_json(rows[0]["payload"])
        delta = None
        if len(rows) >= 2:
            previous = HealthSnapshot.model_validate_json(rows[1]["payload"])
            delta = compute_delta(snapshot, previous)

        if as_json:
            output = {"snapshot": json.loads(rows[0]["payload"])}
            if delta is not None:
                output["delta"] = json.loads(delta.model_dump_json())
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print(_format_snapshot(snapshot, delta))
    finally:
        await memory.close()


def main(args: list[str]) -> None:
    """Entry point for `python -m src health-snapshot`."""
    db_path = _DEFAULT_DB
    as_json = False

    i = 0
    while i < len(args):
        if args[i] == "--db" and i + 1 < len(args):
            db_path = args[i + 1]
            i += 2
        elif args[i] == "--json":
            as_json = True
            i += 1
        elif args[i] in ("--help", "-h"):
            print("Usage: python -m src health-snapshot [--db PATH] [--json]")
            print("")
            print("Options:")
            print("  --db PATH   Path to brain.db (default: data/brain.db)")
            print("  --json      Output raw JSON instead of formatted text")
            sys.exit(0)
        else:
            i += 1

    asyncio.run(_run(db_path, as_json))
