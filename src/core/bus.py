from __future__ import annotations

import logging

from .types import Integration

logger = logging.getLogger(__name__)


class IntegrationBus:
    """Lifecycle manager for all integrations.

    This is NOT a routing bus. Adapters are injected directly into tools.
    This bus only manages: lifecycle (start/stop) and health checks.
    """

    def __init__(self) -> None:
        self._integrations: dict[str, Integration] = {}

    def register(self, integration: Integration) -> None:
        """Register an integration by name."""
        self._integrations[integration.name] = integration
        logger.debug("Registered integration: %s", integration.name)

    def unregister(self, name: str) -> None:
        """Unregister an integration by name."""
        self._integrations.pop(name, None)

    def get(self, name: str) -> Integration | None:
        """Get a registered integration by name."""
        return self._integrations.get(name)

    async def health(self) -> dict[str, bool]:
        """Return health status of all registered integrations."""
        results: dict[str, bool] = {}
        for name, integration in self._integrations.items():
            try:
                results[name] = await integration.health_check()
            except Exception as e:
                logger.warning("Health check failed for %s: %s", name, e)
                results[name] = False
        return results

    async def start_all(self) -> None:
        """Start all registered integrations in registration order."""
        for name, integration in self._integrations.items():
            try:
                logger.info("Starting integration: %s", name)
                await integration.start()
            except Exception as e:
                logger.error("Failed to start %s: %s", name, e, exc_info=True)

    async def stop_all(self) -> None:
        """Stop all registered integrations in reverse registration order."""
        for name, integration in reversed(list(self._integrations.items())):
            try:
                logger.info("Stopping integration: %s", name)
                await integration.stop()
            except Exception as e:
                logger.error("Failed to stop %s: %s", name, e, exc_info=True)
