from __future__ import annotations

# HAL package — Hardware Abstraction Layer
from .devices import (
    BlindAdapter,
    CameraAdapter,
    ClimateAdapter,
    ColoredLightAdapter,
    DoorStationDeviceAdapter,
    LightAdapter,
    PresenceSensorAdapter,
    SensorAdapter,
    WeatherSensorAdapter,
)
from .hal import HAL
from .rules import CustomRule, RuleConfig, RuleEngine, Violation
from .models import (
    DeviceId,
    DeviceInfo,
    DeviceState,
    DeviceType,
    SensorClass,
)
from .platform import Platform
from .platforms.gira_x1 import GiraX1Platform
from .protocols import ConfigSource, EnergySource, PresenceQuery, TopologyQuery, WeatherSource
from .state import StateStore

__all__ = [
    "BlindAdapter",
    "CameraAdapter",
    "ClimateAdapter",
    "ColoredLightAdapter",
    "DeviceId",
    "DeviceInfo",
    "DeviceState",
    "DeviceType",
    "ConfigSource",
    "CustomRule",
    "DoorStationDeviceAdapter",
    "EnergySource",
    "GiraX1Platform",
    "HAL",
    "PresenceQuery",
    "RuleConfig",
    "RuleEngine",
    "Violation",
    "LightAdapter",
    "Platform",
    "PresenceSensorAdapter",
    "SensorAdapter",
    "SensorClass",
    "StateStore",
    "TopologyQuery",
    "WeatherSensorAdapter",
    "WeatherSource",
]
