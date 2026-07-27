"""Mobotix T24M door station adapter.

The T24M supports:
- HTTP API for door relay trigger (open door)
- Bell webhook: T24M sends HTTP POST to our endpoint on bell press

Bell events trigger Matrix notification with Frigate snapshot.
"""

from __future__ import annotations

import hmac
import logging
from typing import Callable

import httpx  # type: ignore[import-not-found]
from aiohttp import web

logger = logging.getLogger(__name__)


class DoorStationAdapter:
    """Adapter for Mobotix T24M door station.

    Methods:
    - start(): start aiohttp webhook listener
    - stop(): shutdown listener
    - trigger_door_relay(): HTTP POST to T24M to open door
    - health_check(): ping T24M
    """

    name: str = "door_station"

    def __init__(
        self,
        host: str = "",
        password: str = "",
        webhook_port: int = 8103,
        webhook_host: str = "127.0.0.1",
        webhook_token: str = "",
        bell_handler: Callable | None = None,
    ) -> None:
        self._host = host
        self._password = password
        self._webhook_port = webhook_port
        self._webhook_host = webhook_host
        self._webhook_token = webhook_token
        self._bell_handler = bell_handler
        self._runner: web.AppRunner | None = None
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Start webhook listener for bell events."""
        self._client = httpx.AsyncClient(timeout=5.0)

        app = web.Application()
        app.router.add_post("/bell", self._on_bell)
        app.router.add_get("/health", self._health_endpoint)

        runner = web.AppRunner(app)
        await runner.setup()
        self._runner = runner
        site = web.TCPSite(runner, self._webhook_host, self._webhook_port)
        await site.start()
        logger.info(
            "DoorStationAdapter webhook listener started on %s:%d",
            self._webhook_host,
            self._webhook_port,
        )

    async def stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        logger.info("DoorStationAdapter stopped")

    async def health_check(self) -> bool:
        """Check if T24M is reachable."""
        if self._client is None:
            return False
        try:
            url = f"http://{self._host}/control/rcontrol?action=getdevicestatus"
            if self._password:
                resp = await self._client.get(url, auth=httpx.BasicAuth("admin", self._password))
            else:
                resp = await self._client.get(url)
            return resp.status_code < 400
        except Exception as e:
            logger.debug("T24M health check failed: %s", e)
            return False

    async def trigger_door_relay(self) -> bool:
        """Trigger the T24M door relay to open the door.

        Uses the Mobotix HTTP API CGI interface.
        POST /control/rcontrol?action=triggerdoorbell (or similar)

        Returns True on success, False on failure.
        """
        if self._client is None:
            logger.error("DoorStationAdapter not started")
            return False

        try:
            # Mobotix T24M CGI API for door relay
            # The exact endpoint varies by firmware; common options:
            # /control/rcontrol?action=openDoor
            # /cgi-bin/donotdisturb.cgi?action=open
            url = f"http://{self._host}/control/rcontrol?action=openDoor"
            if self._password:
                resp = await self._client.post(url, auth=httpx.BasicAuth("admin", self._password))
            else:
                resp = await self._client.post(url)

            if resp.status_code < 400:
                logger.info("T24M door relay triggered successfully")
                return True
            else:
                logger.warning("T24M door relay trigger failed: %s", resp.status_code)
                return False
        except Exception as e:
            logger.error("T24M door relay error: %s", e)
            return False

    async def _on_bell(self, request: web.Request) -> web.Response:
        """Handle an authenticated bell webhook from T24M."""
        supplied_token = request.headers.get("X-Gira-Webhook-Token", "")
        if not supplied_token:
            supplied_token = request.query.get("token", "")
        if not self._webhook_token or not hmac.compare_digest(supplied_token, self._webhook_token):
            logger.warning("Rejected unauthenticated door bell webhook")
            return web.Response(status=403, text="Forbidden")

        logger.info("Authenticated door bell event received")
        if self._bell_handler:
            try:
                await self._bell_handler()
            except Exception as e:
                logger.error("Bell handler error: %s", e)

        return web.Response(status=200, text="OK")

    async def _health_endpoint(self, request: web.Request) -> web.Response:
        return web.Response(status=200, text="OK")
