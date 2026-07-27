"""Health snapshot model for structured reflection cycle data.

Stores one snapshot per reflection cycle in SQLite (brain.db) as JSON payload.
SnapshotDelta captures numeric field changes vs the previous snapshot.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class HealthSnapshot(BaseModel):
    """Structured health snapshot from one reflection cycle."""

    # Timing
    timestamp: str

    # Platform health: platform name -> healthy (True) or unhealthy (False)
    platform_health: dict[str, bool]

    # Memory growth
    memory_episodic_count: int
    memory_semantic_edges: int
    memory_procedural_prefs: int

    # Correction tracking (last 7 days)
    corrections_count_7d: int
    repeated_corrections: list[dict]  # top 3 {original, correction, count}

    # Routines
    active_routines: int  # enabled=1 AND confirmed=1
    candidate_routines: int  # enabled=1 AND confirmed=0
    routine_fires_success: int
    routine_fires_failure: int
    pattern_detector_hits: int  # candidate routines created in last 7 days

    # Device/room activity (may be empty if room/device not populated)
    top_rooms: list[dict]  # [{room: str, count: int}, ...] top 5
    top_devices: list[dict]  # [{device: str, count: int}, ...] top 5

    # Anomalies
    anomalies_found: int
    anomalies_high: int
    anomalies_medium: int
    anomalies_low: int

    # Insights from reflection tier
    energy_insights: list[str]
    behavioral_changes: list[str]
    free_text_suggestion: str


class SnapshotDelta(BaseModel):
    """Numeric field deltas between current and previous HealthSnapshot.

    Only numeric (int/float) fields are diffed. List and dict fields are excluded.
    A None value means the delta was not computed (e.g. no previous snapshot).
    """

    memory_episodic_count: Optional[int] = None
    memory_semantic_edges: Optional[int] = None
    memory_procedural_prefs: Optional[int] = None
    corrections_count_7d: Optional[int] = None
    active_routines: Optional[int] = None
    candidate_routines: Optional[int] = None
    routine_fires_success: Optional[int] = None
    routine_fires_failure: Optional[int] = None
    pattern_detector_hits: Optional[int] = None
    anomalies_found: Optional[int] = None
    anomalies_high: Optional[int] = None
    anomalies_medium: Optional[int] = None
    anomalies_low: Optional[int] = None


def compute_delta(current: HealthSnapshot, previous: HealthSnapshot | None) -> SnapshotDelta | None:
    """Compute numeric field deltas between current and previous snapshot.

    Returns None if previous is None (no prior snapshot to compare against).
    Otherwise returns SnapshotDelta with current.field - previous.field for each
    numeric field.
    """
    if previous is None:
        return None

    return SnapshotDelta(
        memory_episodic_count=current.memory_episodic_count - previous.memory_episodic_count,
        memory_semantic_edges=current.memory_semantic_edges - previous.memory_semantic_edges,
        memory_procedural_prefs=current.memory_procedural_prefs - previous.memory_procedural_prefs,
        corrections_count_7d=current.corrections_count_7d - previous.corrections_count_7d,
        active_routines=current.active_routines - previous.active_routines,
        candidate_routines=current.candidate_routines - previous.candidate_routines,
        routine_fires_success=current.routine_fires_success - previous.routine_fires_success,
        routine_fires_failure=current.routine_fires_failure - previous.routine_fires_failure,
        pattern_detector_hits=current.pattern_detector_hits - previous.pattern_detector_hits,
        anomalies_found=current.anomalies_found - previous.anomalies_found,
        anomalies_high=current.anomalies_high - previous.anomalies_high,
        anomalies_medium=current.anomalies_medium - previous.anomalies_medium,
        anomalies_low=current.anomalies_low - previous.anomalies_low,
    )
