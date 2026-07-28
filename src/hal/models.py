from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field  # type: ignore[import-not-found]


class DeviceType(StrEnum):
    LIGHT = "light"
    DIMMABLE_LIGHT = "dimmable_light"
    COLORED_LIGHT = "colored_light"
    SWITCH = "switch"
    BLIND = "blind"
    CLIMATE = "climate"
    HEATING = "heating"
    BINARY_SENSOR = "binary_sensor"
    TEMPERATURE_SENSOR = "temperature_sensor"
    SCENE = "scene"
    TRIGGER = "trigger"
    CAMERA = "camera"
    PRESENCE_SENSOR = "presence_sensor"
    WEATHER_SENSOR = "weather_sensor"
    DOOR_STATION = "door_station"
    CALENDAR_SENSOR = "calendar_sensor"


class SensorClass(StrEnum):
    WINDOW = "window"
    DOOR = "door"
    MOTION = "motion"
    ALARM_READINESS = "alarm_readiness"
    TEMPERATURE = "temperature"
    GENERIC = "generic"
    PRESENCE = "presence"


class DeviceId(BaseModel):
    model_config = ConfigDict(frozen=True)

    platform: str
    native_id: str

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.native_id}"

    def __hash__(self) -> int:
        return hash(self.key)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DeviceId):
            return self.key == other.key
        return NotImplemented


class DeviceInfo(BaseModel):
    id: DeviceId
    name: str
    device_type: DeviceType
    room: str
    floor: str = ""
    sensor_class: SensorClass = SensorClass.GENERIC
    capabilities: set[str] = Field(default_factory=set)
    is_group: bool = False
    platform_data: dict = Field(default_factory=dict)


class DeviceState(BaseModel):
    device_id: DeviceId
    state: dict[str, str | float | bool | None]
    last_updated: datetime
    source: str = "external"
