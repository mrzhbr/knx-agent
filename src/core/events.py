from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Event type constants
STATE_CHANGE = "state_change"
PRESENCE = "presence"
DETECTION = "detection"
WINDOW_CHANGE = "window_change"
DOOR_BELL = "door_bell"
CAMERA_EVENT = "camera_event"


@dataclass
class Event:
    """Generic event dispatched through the IntegrationBus."""

    type: str
    source: str
    data: dict
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DetectionEvent:
    """Structured event from camera/NVR when a person or car is detected. when a person or car is detected."""

    camera: str
    zones: list[str]
    confidence: float
    event_id: str
    timestamp: datetime
    has_snapshot: bool = False
    label: str = "person"
