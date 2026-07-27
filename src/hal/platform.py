from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from .models import DeviceId, DeviceInfo, DeviceState

if TYPE_CHECKING:
    from src.config import BrainConfig


class Platform(ABC):
    """Abstract base class that every platform backend must implement."""

    @classmethod
    def create_from_config(cls, config: BrainConfig) -> Platform | None:
        """Create a platform instance from config, or None if not configured."""
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique platform name identifier."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """German display name for user-facing messages."""
        ...

    @property
    @abstractmethod
    def error_message(self) -> str:
        """German error message when platform is unhealthy."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Initialize the platform connection."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Tear down the platform connection."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the platform is reachable."""
        ...

    @abstractmethod
    async def discover_devices(self) -> list[DeviceInfo]:
        """Return all devices available on this platform."""
        ...

    @abstractmethod
    async def read_state(self, device_id: DeviceId) -> DeviceState:
        """Read the current state of a device."""
        ...

    async def send_command(self, device_id: DeviceId, command: str, **params: Any) -> None:
        """Send a command to a device. Default raises NotImplementedError for read-only platforms."""
        raise NotImplementedError(f"Platform {self.name!r} is read-only")

    async def subscribe_events(self, callback: Callable[[DeviceState], Awaitable[None]]) -> None:
        """Subscribe to push state change events. Default: no-op.

        Platforms that support push events should call the callback whenever a
        device state changes. Platforms that only support polling may leave this
        as a no-op.
        """

    async def unsubscribe_events(self) -> None:
        """Unsubscribe from push events. Called during teardown. Default: no-op."""
