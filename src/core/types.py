from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable


@runtime_checkable
class Integration(Protocol):
    """Protocol for all integration adapters. Provides lifecycle management."""

    name: str

    async def start(self) -> None:
        """Start the integration (connect, register callbacks, etc.)."""
        ...

    async def stop(self) -> None:
        """Stop the integration (disconnect, unregister, cleanup)."""
        ...

    async def health_check(self) -> bool:
        """Return True if integration is healthy/connected."""
        ...


class HealthState(Enum):
    """Platform health states."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthStatus:
    """Health status for a single platform.

    Attributes:
        platform_name: Unique platform identifier (e.g., "frigate", "gira_x1")
        state: Current health state (HEALTHY, UNHEALTHY, UNKNOWN)
        last_checked: Timestamp of last health check
        last_error: Error message if unhealthy, None if healthy
    """

    platform_name: str
    state: HealthState
    last_checked: datetime
    last_error: str | None = None
