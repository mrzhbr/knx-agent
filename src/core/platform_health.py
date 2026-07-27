"""Platform health monitoring — polls all HAL platforms every 30 seconds."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from src.core.types import HealthState, HealthStatus

if TYPE_CHECKING:
    from src.hal import HAL

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 30


class PlatformHealthMonitor:
    """Polls platform health checks and stores current status.

    Follows the same lifecycle pattern as ProactiveMonitor:
    - start() creates the background task
    - stop() cancels the task gracefully
    - _run() contains the main monitoring loop
    """

    def __init__(self, hal: "HAL") -> None:
        self._hal = hal
        self._task: asyncio.Task | None = None
        self._statuses: dict[str, HealthStatus] = {}
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        """Start the health monitoring loop."""
        self._task = asyncio.create_task(self._run(), name="platform_health_monitor")

    def stop(self) -> None:
        """Stop the health monitoring loop."""
        self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("PlatformHealthMonitor stopped")

    async def _run(self) -> None:
        """Main monitoring loop."""
        logger.info("PlatformHealthMonitor running (%ds interval)", POLL_INTERVAL_SECONDS)

        await self._check_all_platforms()

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except TimeoutError:
                await self._check_all_platforms()

    async def _check_all_platforms(self) -> None:
        """Check health of all registered platforms."""
        if not self._hal:
            return

        for name, platform in self._hal.iter_platforms().items():
            try:
                is_healthy = await platform.health_check()
                new_state = HealthState.HEALTHY if is_healthy else HealthState.UNHEALTHY
                last_error = None if is_healthy else "Health check returned False"
            except Exception as e:
                new_state = HealthState.UNHEALTHY
                last_error = str(e)
                logger.warning("Health check failed for %s: %s", name, e)

            now = datetime.now()
            old_status = self._statuses.get(name)

            self._statuses[name] = HealthStatus(
                platform_name=name,
                state=new_state,
                last_checked=now,
                last_error=last_error,
            )

            if old_status and old_status.state != new_state:
                if new_state == HealthState.HEALTHY:
                    logger.info("Platform %s is now healthy", name)
                else:
                    logger.warning("Platform %s is now unhealthy: %s", name, last_error)

    def get_status(self, platform_name: str) -> HealthStatus | None:
        """Get health status for a specific platform."""
        return self._statuses.get(platform_name)

    def get_all_statuses(self) -> dict[str, HealthStatus]:
        """Get health status for all platforms."""
        return dict(self._statuses)

    def get_unhealthy_platforms(self) -> list[HealthStatus]:
        """Get list of all unhealthy platforms."""
        return [status for status in self._statuses.values() if status.state != HealthState.HEALTHY]

    def format_unhealthy_for_prompt(self) -> str | None:
        """Format unhealthy platforms for agent context (German)."""
        unhealthy = self.get_unhealthy_platforms()
        if not unhealthy:
            return None

        platforms = self._hal.iter_platforms()
        lines = []
        for status in unhealthy:
            platform = platforms.get(status.platform_name)
            if platform is not None:
                lines.append(platform.error_message)
            else:
                lines.append(f"⚠ {status.platform_name} ist offline.")

        return "\n".join(lines)
