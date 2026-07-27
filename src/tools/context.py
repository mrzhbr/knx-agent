"""ToolContext — single frozen dependency container for all tool modules.

All @tool functions in comfort.py, security.py, energy.py, presence.py,
memory.py, topology.py, rules.py, and schedule.py receive their deps
from a single ToolContext instance set via each module's set_context().
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.hal import HAL
    from src.core.awareness import HouseAwareness, EnergyAwareness
    from src.core.presence import PresenceManager
    from src.hal.rules import RuleEngine
    from src.core.topology import HouseTopology
    from src.memory.layer import MemoryLayer
    from src.background.scheduler import ScheduleEngine
    from src.core.platform_health import PlatformHealthMonitor


@dataclass(frozen=True)
class ToolContext:
    """Single dependency container for all tool functions.

    Frozen to prevent mutation after initialization.
    All fields that may be absent at startup use None defaults.
    """

    hal: HAL
    presence_manager: PresenceManager
    energy_awareness: EnergyAwareness
    rule_engine: RuleEngine
    memory_layer: MemoryLayer
    schedule_engine: ScheduleEngine
    topology: HouseTopology | None = None
    awareness: HouseAwareness | None = None  # For build_embodied_context in prompts.py
    platform_health: PlatformHealthMonitor | None = None
    latitude: float = 0.0
    longitude: float = 0.0
    timezone: str = "Europe/Berlin"
