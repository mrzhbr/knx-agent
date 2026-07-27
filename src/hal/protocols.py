"""HAL protocol definitions for optional runtime dependencies.

These protocols define the minimal interfaces that RuleEngine and other HAL
components depend on, without importing from src.core or other layers.

Architecture: HAL defines what it needs. Consumers implement it. This inverts
the dependency — HAL does not import from src.core.

Usage:
    from src.hal.protocols import PresenceQuery, TopologyQuery, WeatherSource, EnergySource, ConfigSource

    class PresenceManager:  # in src/core/presence.py
        def nobody_home(self) -> bool: ...  # satisfies PresenceQuery

    # At injection site:
    assert isinstance(presence_manager, PresenceQuery)  # runtime check via @runtime_checkable
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PresenceQuery(Protocol):
    """Protocol for presence detection: can the house determine if no one is home?

    Implemented by: src.core.presence.PresenceManager
    Used by: RuleEngine rules 3, 4, 8 (nobody_home guard)
    """

    def nobody_home(self) -> bool: ...


@runtime_checkable
class TopologyQuery(Protocol):
    """Protocol for spatial topology queries: open-plan room adjacency and solar exposure.

    Implemented by: src.core.topology.HouseTopology (or equivalent)
    Used by: RuleEngine rules 6, 7, 8 (open-plan and solar conflict detection)
    """

    def get_open_to(self, room_key: str) -> list[str] | None: ...

    def assess_solar_exposure(
        self, room_key: str, weather_data: dict, lat: float, lon: float, tz: str
    ) -> dict: ...


@runtime_checkable
class WeatherSource(Protocol):
    """Protocol for weather data fetching.

    Implemented by: src.integrations.weather.WeatherAdapter
    Used by: RuleEngine rule 6 (solar_heating_conflict — needs current weather)
    """

    async def get_current(self) -> dict[str, Any]: ...


@runtime_checkable
class EnergySource(Protocol):
    """Protocol for energy consumption estimation.

    Implemented by: src.core.awareness.EnergyAwareness
    Used by: RuleEngine rule 5 (energy_spike — needs estimated wattage)
    """

    @property
    def estimated_watts(self) -> float: ...


@runtime_checkable
class ConfigSource(Protocol):
    """Protocol for home location configuration.

    Provides home location data for solar calculations (rule 6).
    Implemented by: src.config.BrainConfig (has .home.latitude, .home.longitude, .home.timezone)
    Used by: RuleEngine rule 6 (solar_heating_conflict)
    """

    @property
    def home(self) -> Any: ...  # needs .latitude, .longitude, .timezone
