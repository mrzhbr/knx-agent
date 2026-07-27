"""Tests for HealthSnapshot model, SnapshotDelta, and compute_delta."""

from __future__ import annotations

from src.background.health_snapshot import HealthSnapshot, SnapshotDelta, compute_delta


def _make_snapshot(**kwargs) -> HealthSnapshot:
    """Build a HealthSnapshot with sensible defaults."""
    defaults = {
        "timestamp": "2026-04-12T03:00:00",
        "platform_health": {"gira_x1": True, "frigate": True},
        "memory_episodic_count": 100,
        "memory_semantic_edges": 50,
        "memory_procedural_prefs": 20,
        "corrections_count_7d": 3,
        "repeated_corrections": [],
        "active_routines": 5,
        "candidate_routines": 2,
        "routine_fires_success": 10,
        "routine_fires_failure": 1,
        "pattern_detector_hits": 1,
        "top_rooms": [],
        "top_devices": [],
        "anomalies_found": 0,
        "anomalies_high": 0,
        "anomalies_medium": 0,
        "anomalies_low": 0,
        "energy_insights": [],
        "behavioral_changes": [],
        "free_text_suggestion": "",
    }
    defaults.update(kwargs)
    return HealthSnapshot(**defaults)


# ─── HealthSnapshot construction ──────────────────────────────────────────────


def test_health_snapshot_construction():
    """HealthSnapshot can be constructed with valid data."""
    s = _make_snapshot()
    assert s.timestamp == "2026-04-12T03:00:00"
    assert s.memory_episodic_count == 100
    assert s.platform_health == {"gira_x1": True, "frigate": True}


def test_health_snapshot_with_full_data():
    """HealthSnapshot accepts all optional list fields."""
    s = _make_snapshot(
        repeated_corrections=[{"original": "Licht an", "correction": "Licht 50%", "count": 3}],
        top_rooms=[{"room": "Schlafzimmer", "count": 42}],
        top_devices=[{"device": "Deckenlampe", "count": 20}],
        energy_insights=["Kuehlschrank verbraucht mehr als ueblich"],
        behavioral_changes=["Rollladen oft um 22 Uhr"],
        free_text_suggestion="Rollladen automatisch schliessen",
    )
    assert len(s.repeated_corrections) == 1
    assert len(s.top_rooms) == 1
    assert s.free_text_suggestion == "Rollladen automatisch schliessen"


def test_health_snapshot_zero_counts():
    """HealthSnapshot handles all-zero counts."""
    s = _make_snapshot(
        memory_episodic_count=0,
        memory_semantic_edges=0,
        corrections_count_7d=0,
        anomalies_found=0,
    )
    assert s.memory_episodic_count == 0
    assert s.anomalies_found == 0


def test_health_snapshot_empty_lists():
    """HealthSnapshot handles empty lists gracefully."""
    s = _make_snapshot(
        top_rooms=[],
        top_devices=[],
        energy_insights=[],
        behavioral_changes=[],
        repeated_corrections=[],
    )
    assert s.top_rooms == []
    assert s.top_devices == []


# ─── JSON serialization ────────────────────────────────────────────────────────


def test_health_snapshot_serializes_to_json():
    """HealthSnapshot.model_dump_json() produces valid JSON."""
    import json

    s = _make_snapshot()
    j = s.model_dump_json()
    parsed = json.loads(j)
    assert parsed["timestamp"] == "2026-04-12T03:00:00"
    assert parsed["memory_episodic_count"] == 100


def test_health_snapshot_round_trips_json():
    """HealthSnapshot can be serialized and deserialized from JSON."""
    s = _make_snapshot(memory_episodic_count=42, anomalies_high=2)
    j = s.model_dump_json()
    s2 = HealthSnapshot.model_validate_json(j)
    assert s2.memory_episodic_count == 42
    assert s2.anomalies_high == 2
    assert s2.platform_health == {"gira_x1": True, "frigate": True}


# ─── SnapshotDelta ─────────────────────────────────────────────────────────────


def test_snapshot_delta_has_only_numeric_fields():
    """SnapshotDelta does not have list or dict fields from HealthSnapshot."""
    delta = SnapshotDelta()
    # Numeric fields present
    assert hasattr(delta, "memory_episodic_count")
    assert hasattr(delta, "anomalies_found")
    # List/dict fields NOT present
    assert not hasattr(delta, "top_rooms")
    assert not hasattr(delta, "top_devices")
    assert not hasattr(delta, "energy_insights")
    assert not hasattr(delta, "behavioral_changes")
    assert not hasattr(delta, "repeated_corrections")
    assert not hasattr(delta, "platform_health")
    assert not hasattr(delta, "timestamp")
    assert not hasattr(delta, "free_text_suggestion")


def test_snapshot_delta_defaults_to_none():
    """All SnapshotDelta fields default to None."""
    delta = SnapshotDelta()
    assert delta.memory_episodic_count is None
    assert delta.anomalies_found is None
    assert delta.active_routines is None


# ─── compute_delta ─────────────────────────────────────────────────────────────


def test_compute_delta_returns_none_with_no_previous():
    """compute_delta returns None when previous is None."""
    current = _make_snapshot()
    result = compute_delta(current, None)
    assert result is None


def test_compute_delta_computes_diffs():
    """compute_delta returns correct diffs for all numeric fields."""
    prev = _make_snapshot(
        memory_episodic_count=80,
        memory_semantic_edges=40,
        memory_procedural_prefs=15,
        corrections_count_7d=2,
        active_routines=4,
        candidate_routines=1,
        routine_fires_success=8,
        routine_fires_failure=0,
        pattern_detector_hits=0,
        anomalies_found=1,
        anomalies_high=1,
        anomalies_medium=0,
        anomalies_low=0,
    )
    curr = _make_snapshot(
        memory_episodic_count=100,
        memory_semantic_edges=50,
        memory_procedural_prefs=20,
        corrections_count_7d=3,
        active_routines=5,
        candidate_routines=2,
        routine_fires_success=10,
        routine_fires_failure=1,
        pattern_detector_hits=1,
        anomalies_found=0,
        anomalies_high=0,
        anomalies_medium=0,
        anomalies_low=0,
    )
    delta = compute_delta(curr, prev)
    assert delta is not None
    assert delta.memory_episodic_count == 20  # 100 - 80
    assert delta.memory_semantic_edges == 10  # 50 - 40
    assert delta.memory_procedural_prefs == 5  # 20 - 15
    assert delta.corrections_count_7d == 1  # 3 - 2
    assert delta.active_routines == 1  # 5 - 4
    assert delta.candidate_routines == 1  # 2 - 1
    assert delta.routine_fires_success == 2  # 10 - 8
    assert delta.routine_fires_failure == 1  # 1 - 0
    assert delta.pattern_detector_hits == 1  # 1 - 0
    assert delta.anomalies_found == -1  # 0 - 1


def test_compute_delta_negative_diffs():
    """compute_delta correctly computes negative diffs."""
    prev = _make_snapshot(memory_episodic_count=100)
    curr = _make_snapshot(memory_episodic_count=90)
    delta = compute_delta(curr, prev)
    assert delta is not None
    assert delta.memory_episodic_count == -10


def test_compute_delta_returns_snapshot_delta_type():
    """compute_delta returns a SnapshotDelta instance."""
    prev = _make_snapshot()
    curr = _make_snapshot()
    result = compute_delta(curr, prev)
    assert isinstance(result, SnapshotDelta)
