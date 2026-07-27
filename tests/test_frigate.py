from __future__ import annotations

import pytest  # type: ignore[import-not-found]
from unittest.mock import AsyncMock, MagicMock

from src.core.events import DetectionEvent
from src.integrations.frigate import FrigateAdapter


def make_frigate_payload(camera="entrance", label="person", zones=None, score=0.85):
    """Create a realistic Frigate MQTT event payload."""
    return {
        "type": "new",
        "before": {},
        "after": {
            "id": "event123",
            "camera": camera,
            "label": label,
            "current_zones": zones or ["entrance_zone"],
            "top_score": score,
            "has_snapshot": True,
            "frame_time": 1710000000.0,
        },
    }


async def test_on_event_creates_detection_event():
    """Frigate MQTT event should create DetectionEvent and call handler."""
    received = []

    async def handler(detection: DetectionEvent):
        received.append(detection)

    adapter = FrigateAdapter(
        mqtt_host="localhost",
        mqtt_port=1883,
        frigate_host="localhost",
        frigate_port=5000,
        event_handler=handler,
    )

    payload = make_frigate_payload()
    await adapter._on_event(payload)

    assert len(received) == 1
    assert received[0].camera == "entrance"
    assert received[0].label == "person"
    assert received[0].confidence == pytest.approx(0.85)
    assert received[0].has_snapshot


async def test_on_event_calls_awareness():
    """Detection fires event_handler callback (awareness removed in Plan 03/04)."""
    event_handler_called = []

    async def mock_handler(event):
        event_handler_called.append(event)

    # awareness parameter no longer used; FrigatePlatform updates StateStore directly
    adapter = FrigateAdapter(
        mqtt_host="localhost",
        mqtt_port=1883,
        frigate_host="localhost",
        frigate_port=5000,
        event_handler=mock_handler,
    )
    payload = make_frigate_payload()
    await adapter._on_event(payload)

    # Event handler should be called
    assert len(event_handler_called) == 1


async def test_on_event_ignores_non_new():
    """Only 'new' event type should be processed."""
    received = []

    async def handler(d):
        received.append(d)

    adapter = FrigateAdapter(
        mqtt_host="localhost",
        mqtt_port=1883,
        frigate_host="localhost",
        frigate_port=5000,
        event_handler=handler,
    )

    payload = make_frigate_payload()
    payload["type"] = "update"
    await adapter._on_event(payload)

    assert len(received) == 0


async def test_on_event_ignores_non_person():
    """Non-person labels (that aren't car) should be ignored."""
    received = []

    async def handler(d):
        received.append(d)

    adapter = FrigateAdapter(
        mqtt_host="localhost",
        mqtt_port=1883,
        frigate_host="localhost",
        frigate_port=5000,
        event_handler=handler,
    )

    payload = make_frigate_payload(label="cat")
    await adapter._on_event(payload)

    assert len(received) == 0


async def test_entrance_lights_triggered():
    """Entrance camera detection should trigger entrance lights."""
    mock_gira = MagicMock()
    mock_gira.set_values = AsyncMock()

    adapter = FrigateAdapter(
        mqtt_host="localhost",
        mqtt_port=1883,
        frigate_host="localhost",
        frigate_port=5000,
        gira_x1=mock_gira,
        entrance_light_uids=["dp-example-light"],
        entrance_cameras=["entrance"],
    )

    payload = make_frigate_payload(camera="entrance")
    await adapter._on_event(payload)

    # Give the background task time to run
    await __import__("asyncio").sleep(0.01)
    mock_gira.set_values.assert_called_once()
    call_args = mock_gira.set_values.call_args[0][0]
    assert "dp-example-light" in call_args
