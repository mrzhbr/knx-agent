"""Tests for PatternDetector — hourly Haiku-based routine discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from src.background.patterns import PatternDetector


# ─── Fixtures ──────────────────────────────────────────────────────────────


def _make_config():
    config = MagicMock()
    config.models.router.model_id = "accounts/fireworks/models/gpt-oss-120b"
    config.models.router.base_url = "https://api.fireworks.ai/inference/v1"
    config.models.router.api_key_env = "FIREWORKS_API_KEY"
    return config


def _make_memory_layer(events=None, routines=None):
    """Build a mock MemoryLayer for PatternDetector tests."""
    from src.memory.layer import MemoryLayer

    layer = MagicMock(spec=MemoryLayer)
    layer.episodic = MagicMock()
    layer.episodic.search_recent = AsyncMock(return_value=events or [])
    layer.procedural = MagicMock()
    layer.procedural.store_candidate_routine = AsyncMock(return_value=1)
    return layer


def _make_fixture_events(count=25):
    """Create fixture events for pattern detection tests."""
    return [
        {
            "text": f"[Max] Licht Büro eingeschaltet (event {i})",
            "metadata": {"timestamp": "2026-03-15T08:00:00", "user": "Max"},
            "id": f"ev-{i}",
        }
        for i in range(count)
    ]


# ─── Tests: skip logic ────────────────────────────────────────────────────


async def test_analyze_skips_when_no_episodic():
    """_analyze() skips gracefully when episodic is None."""
    memory = _make_memory_layer()
    memory.episodic = None

    detector = PatternDetector(memory_layer=memory, config=_make_config())
    await detector._analyze()  # Must not raise


async def test_analyze_skips_when_insufficient_events():
    """_analyze() skips when fewer than 20 events."""
    memory = _make_memory_layer(events=_make_fixture_events(count=10))

    detector = PatternDetector(memory_layer=memory, config=_make_config())
    with patch.object(detector, "_call_router_tier", AsyncMock(return_value=[])) as mock_haiku:
        await detector._analyze()
        mock_haiku.assert_not_called()


# ─── Tests: pattern storage ───────────────────────────────────────────────


async def test_analyze_stores_high_confidence_patterns():
    """_analyze() stores patterns with confidence > 0.8 and occurrences >= 5."""
    events = _make_fixture_events(count=25)
    memory = _make_memory_layer(events=events)

    haiku_response = [
        {
            "pattern": "Licht Büro um 8 Uhr",
            "occurrences": 7,
            "typical_time": "08:00",
            "confidence": 0.9,
        },
        {
            "pattern": "Seltenes Muster",
            "occurrences": 2,
            "typical_time": "12:00",
            "confidence": 0.5,
        },  # Too low confidence + too few occurrences
    ]

    detector = PatternDetector(memory_layer=memory, config=_make_config())
    with patch.object(detector, "_call_router_tier", AsyncMock(return_value=haiku_response)):
        await detector._analyze()

    # Only the high-confidence pattern should be stored
    memory.procedural.store_candidate_routine.assert_awaited_once()
    call_kwargs = memory.procedural.store_candidate_routine.call_args[1]
    assert call_kwargs["name"] == "Licht Büro um 8 Uhr"
    assert call_kwargs["trigger"] == "08:00"
    assert call_kwargs["confidence"] == 0.9
    assert call_kwargs["actions"] == [{"description": "Licht Büro um 8 Uhr"}]


async def test_analyze_maps_haiku_fields_correctly():
    """_analyze() maps Haiku output fields to store_candidate_routine correctly."""
    events = _make_fixture_events(count=25)
    memory = _make_memory_layer(events=events)

    haiku_response = [
        {
            "pattern": "Rollläden runter um 22 Uhr",
            "occurrences": 6,
            "typical_time": "22:00",
            "confidence": 0.85,
        },
    ]

    detector = PatternDetector(memory_layer=memory, config=_make_config())
    with patch.object(detector, "_call_router_tier", AsyncMock(return_value=haiku_response)):
        await detector._analyze()

    call_kwargs = memory.procedural.store_candidate_routine.call_args[1]
    assert call_kwargs["name"] == "Rollläden runter um 22 Uhr"
    assert call_kwargs["description"] == "6 Beobachtungen um 22:00"
    assert call_kwargs["trigger"] == "22:00"
    assert call_kwargs["actions"] == [{"description": "Rollläden runter um 22 Uhr"}]
    assert call_kwargs["confidence"] == 0.85


async def test_analyze_skips_low_occurrence_patterns():
    """_analyze() skips patterns with fewer than 5 occurrences."""
    events = _make_fixture_events(count=25)
    memory = _make_memory_layer(events=events)

    haiku_response = [
        {
            "pattern": "Seltenes Muster",
            "occurrences": 3,
            "typical_time": "15:00",
            "confidence": 0.95,
        },  # High confidence but too few occurrences
    ]

    detector = PatternDetector(memory_layer=memory, config=_make_config())
    with patch.object(detector, "_call_router_tier", AsyncMock(return_value=haiku_response)):
        await detector._analyze()

    memory.procedural.store_candidate_routine.assert_not_awaited()


# ─── Tests: start/stop lifecycle ─────────────────────────────────────────


async def test_detector_start_creates_task():
    """start() creates an asyncio Task."""
    memory = _make_memory_layer()
    detector = PatternDetector(memory_layer=memory, config=_make_config())
    detector.start()
    assert detector._task is not None
    assert not detector._task.done()
    detector.stop()


async def test_detector_stop_cancels_task():
    """stop() cancels the pattern detection task."""
    import asyncio

    memory = _make_memory_layer()
    detector = PatternDetector(memory_layer=memory, config=_make_config())
    detector.start()
    task = detector._task
    detector.stop()
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()


# ─── Tests: haiku parse failure ───────────────────────────────────────────


async def test_analyze_handles_haiku_failure_gracefully():
    """_analyze() continues if Haiku call fails."""
    events = _make_fixture_events(count=25)
    memory = _make_memory_layer(events=events)

    detector = PatternDetector(memory_layer=memory, config=_make_config())
    with patch.object(detector, "_call_router_tier", AsyncMock(side_effect=Exception("API error"))):
        await detector._analyze()  # Must not raise
    memory.procedural.store_candidate_routine.assert_not_awaited()
