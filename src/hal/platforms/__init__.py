"""HAL platform implementations.

Each platform bridges the HAL abstraction to a specific home automation backend.
"""

from __future__ import annotations

from src.hal.platform import Platform
from src.hal.platforms.calendar import CalendarPlatform
from src.hal.platforms.door_station import DoorStationPlatform
from src.hal.platforms.frigate import FrigatePlatform
from src.hal.platforms.gira_x1 import GiraX1Platform
from src.hal.platforms.mitsubishi import MitsubishiPlatform
from src.hal.platforms.presence import PresencePlatform
from src.hal.platforms.weather import WeatherPlatform

# Registry mapping platform name strings to their classes.
# Keys match each class's .name property return value.
# Order: gira_x1 first (device seeding dependency).
PLATFORM_REGISTRY: dict[str, type[Platform]] = {
    "gira_x1": GiraX1Platform,
    "frigate": FrigatePlatform,
    "presence": PresencePlatform,
    "mitsubishi_ac": MitsubishiPlatform,
    "weather": WeatherPlatform,
    "door_station": DoorStationPlatform,
    "calendar": CalendarPlatform,
}

__all__ = ["GiraX1Platform", "PLATFORM_REGISTRY"]
