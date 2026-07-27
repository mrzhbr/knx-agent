"""Frigate NVR adapter — subscribes to MQTT events and triggers actions.

Subscribes to frigate/events MQTT topic.
On "new" person detection: creates DetectionEvent, fires registered
event_handler callback. Optionally triggers entrance lights via GiraX1Adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from datetime import datetime
from typing import Callable, TYPE_CHECKING

import httpx  # type: ignore[import-not-found]

if TYPE_CHECKING:
    from src.integrations.gira_x1 import GiraX1Adapter

logger = logging.getLogger(__name__)


class FrigateAdapter:
    """Subscribes to Frigate MQTT events, replaces gira-security."""

    name: str = "frigate"

    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
        frigate_host: str,
        frigate_port: int,
        gira_x1: "GiraX1Adapter | None" = None,
        entrance_light_uids: list[str] | None = None,
        entrance_cameras: list[str] | None = None,
        event_handler: Callable | None = None,
        mqtt_username: str = "",
        mqtt_password: str = "",
        mqtt_tls: bool = False,
    ) -> None:
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._frigate_base = f"http://{frigate_host}:{frigate_port}"
        self._gira_x1 = gira_x1
        self._entrance_light_uids = entrance_light_uids or []
        self._entrance_cameras = set(entrance_cameras or [])
        self._event_handler = event_handler
        self._mqtt_username = mqtt_username
        self._mqtt_password = mqtt_password
        self._mqtt_tls = mqtt_tls
        self._mqtt_task: asyncio.Task | None = None
        self._connected: bool = False
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Connect to MQTT and subscribe to frigate/events."""
        self._client = httpx.AsyncClient(timeout=10.0)
        self._mqtt_task = asyncio.create_task(self._mqtt_loop())
        logger.info(
            "FrigateAdapter started, MQTT=%s:%d Frigate=%s",
            self._mqtt_host,
            self._mqtt_port,
            self._frigate_base,
        )

    async def stop(self) -> None:
        if self._mqtt_task:
            self._mqtt_task.cancel()
            try:
                await self._mqtt_task
            except asyncio.CancelledError as cancellation:
                del cancellation
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False
        logger.info("FrigateAdapter stopped")

    async def health_check(self) -> bool:
        """Check Frigate REST API availability."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(f"{self._frigate_base}/api/stats")
                return r.status_code == 200
        except Exception:
            return False

    async def get_snapshot(self, camera: str) -> bytes:
        """Fetch latest snapshot JPEG from Frigate for a given camera."""
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{self._frigate_base}/api/{camera}/latest.jpg")
            r.raise_for_status()
            return r.content

    async def get_events(self, hours: int = 24) -> list[dict]:
        """Query Frigate event history."""
        cutoff = time.time() - hours * 3600
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(
                    f"{self._frigate_base}/api/events",
                    params={"after": cutoff, "label": "person"},
                )
                r.raise_for_status()
                return r.json()
        except Exception as e:
            logger.error("Failed to fetch Frigate events: %s", e)
            return []

    async def _mqtt_loop(self) -> None:
        """Main MQTT subscription loop with reconnection."""
        while True:
            try:
                aiomqtt = __import__("aiomqtt")
                tls_context = ssl.create_default_context() if self._mqtt_tls else None
                async with aiomqtt.Client(
                    self._mqtt_host,
                    self._mqtt_port,
                    username=self._mqtt_username or None,
                    password=self._mqtt_password or None,
                    tls_context=tls_context,
                ) as mqtt:
                    self._connected = True
                    logger.info("MQTT connected to %s:%d", self._mqtt_host, self._mqtt_port)
                    await mqtt.subscribe("frigate/events")
                    async for message in mqtt.messages:
                        try:
                            payload = json.loads(message.payload)
                            await self._on_event(payload)
                        except json.JSONDecodeError as e:
                            logger.warning("Invalid Frigate event JSON: %s", e)
                        except Exception as e:
                            logger.error("Frigate event handling error: %s", e)
            except asyncio.CancelledError as cancellation:
                del cancellation
                break
            except Exception as e:
                self._connected = False
                logger.warning("MQTT connection lost: %s — reconnecting in 5s", e)
                await asyncio.sleep(5)

    async def _on_event(self, payload: dict) -> None:
        """Process a Frigate MQTT event."""
        event_type = payload.get("type")  # "new", "update", "end"
        after = payload.get("after", {})

        if event_type != "new":
            return

        label = after.get("label", "")
        if label not in ("person", "car"):
            return

        camera = after.get("camera", "unknown")
        zones = after.get("current_zones", [])
        confidence = after.get("top_score", 0.0)
        event_id = after.get("id", "")
        has_snapshot = after.get("has_snapshot", False)
        # frame_time is a Unix timestamp float
        frame_time_raw = after.get("frame_time", 0.0)
        try:
            timestamp = datetime.fromtimestamp(float(frame_time_raw))
        except Exception:
            timestamp = datetime.now()

        from src.core.events import DetectionEvent

        detection = DetectionEvent(
            camera=camera,
            zones=zones,
            confidence=confidence,
            event_id=event_id,
            timestamp=timestamp,
            has_snapshot=has_snapshot,
            label=label,
        )

        logger.info(
            "Frigate detection: camera=%s label=%s zones=%s confidence=%.2f",
            camera,
            label,
            zones,
            confidence,
        )

        # Trigger entrance lights only for deployment-configured cameras.
        if camera in self._entrance_cameras and self._gira_x1 and self._entrance_light_uids:
            asyncio.create_task(self._trigger_entrance_lights())

        # Fire registered handler
        if self._event_handler:
            try:
                await self._event_handler(detection)
            except Exception as e:
                logger.error("Event handler error: %s", e)

    async def _trigger_entrance_lights(self) -> None:
        """Turn on entrance lights when person detected."""
        if not self._gira_x1 or not self._entrance_light_uids:
            return
        uid_values = {uid: "1" for uid in self._entrance_light_uids}
        try:
            await self._gira_x1.set_values(uid_values)
            logger.info("Entrance lights triggered: %s", self._entrance_light_uids)
        except Exception as e:
            logger.error("Failed to trigger entrance lights: %s", e)
