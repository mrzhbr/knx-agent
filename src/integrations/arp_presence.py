from __future__ import annotations

import asyncio
import inspect
import logging
import platform
import re
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import PresenceConfig

logger = logging.getLogger(__name__)


async def _parse_arp_table() -> dict[str, str]:
    """Parse `arp -a` output asynchronously. Returns {ip: mac} dict."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "arp",
            "-a",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        entries: dict[str, str] = {}
        # Format: hostname (ip) at mac [ether] on interface
        pattern = re.compile(r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-f:]+)", re.IGNORECASE)
        for match in pattern.finditer(stdout.decode()):
            ip, mac = match.group(1), match.group(2)
            entries[ip] = mac
        return entries
    except Exception as e:
        logger.warning("ARP parse failed: %s", e)
        return {}


class ARPPresenceAdapter:
    """Detects presence by pinging tracked IPs and checking ARP table.

    away_threshold=3: mark away after 3 consecutive misses (90s at 30s interval).
    """

    name: str = "arp_presence"

    def __init__(
        self,
        config: "PresenceConfig",
    ) -> None:
        self._config = config
        self._miss_count: dict[str, int] = {}
        self._presence: dict[str, bool] = {}
        self._change_callbacks: list[Callable] = []
        self._poll_task: asyncio.Task | None = None

    async def start(self) -> None:
        logger.warning(
            "ARPPresenceAdapter is deprecated. Consider switching to OwnTracks"
            " (backend: 'owntracks' in config)."
        )
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info(
            "ARPPresenceAdapter started, tracking %d devices", len(self._config.tracked_devices)
        )

    async def stop(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    async def health_check(self) -> bool:
        return True

    def on_change(self, callback: Callable) -> None:
        """Register callback for presence state changes."""
        self._change_callbacks.append(callback)

    def who_is_home(self) -> dict[str, bool]:
        """Returns {device_id: is_home} dict."""
        return dict(self._presence)

    def nobody_home(self) -> bool:
        """True if no tracked device is home."""
        return not any(self._presence.values())

    @property
    def open_windows(self) -> list[str]:
        """Always empty — ARP doesn't know about windows. PresenceManager fuses this."""
        return []

    def format_for_prompt(self) -> str:
        """German-language presence summary."""
        home = [dev_id for dev_id, present in self._presence.items() if present]
        away = [dev_id for dev_id, present in self._presence.items() if not present]
        parts = []
        if home:
            home_names = []
            for dev_id in home:
                dev = self._config.tracked_devices.get(dev_id)
                home_names.append(dev.name if dev else dev_id)
            parts.append(f"Zuhause: {', '.join(home_names)}")
        if away:
            away_names = []
            for dev_id in away:
                dev = self._config.tracked_devices.get(dev_id)
                away_names.append(dev.name if dev else dev_id)
            parts.append(f"Abwesend: {', '.join(away_names)}")
        return "\n".join(parts) if parts else "Keine Geräte konfiguriert."

    async def _poll_loop(self) -> None:
        while True:
            try:
                await self._poll_once()
                await asyncio.sleep(self._config.poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("ARP poll error: %s", e)
                await asyncio.sleep(self._config.poll_interval_seconds)

    async def _poll_once(self) -> None:
        """Single poll cycle: ping all tracked IPs concurrently, parse ARP, update presence."""
        # Ping all devices concurrently (also populates ARP table for sleeping devices)
        tracked_items = list(self._config.tracked_devices.items())
        ping_results = await asyncio.gather(
            *(self._ping(tracked.ip) for _, tracked in tracked_items)
        )

        # Parse ARP table as fallback (now populated by pings above)
        arp_table = await _parse_arp_table()

        for (dev_id, tracked), ping_ok in zip(tracked_items, ping_results):
            seen = ping_ok or tracked.ip in arp_table

            was_home = self._presence.get(dev_id, False)

            if seen:
                self._miss_count[dev_id] = 0
                if not was_home:
                    self._presence[dev_id] = True
                    logger.info("Presence: %s arrived home (ip=%s)", dev_id, tracked.ip)
                    await self._fire_change(dev_id, True)
            else:
                miss = self._miss_count.get(dev_id, 0) + 1
                self._miss_count[dev_id] = miss
                if was_home and miss >= self._config.away_threshold:
                    self._presence[dev_id] = False
                    logger.info("Presence: %s left home after %d misses", dev_id, miss)
                    await self._fire_change(dev_id, False)
                elif dev_id not in self._presence:
                    # First poll, not seen
                    self._presence[dev_id] = False

    async def _ping(self, ip: str) -> bool:
        """Ping an IP. Returns True if reachable.

        macOS ping: -W is waittime in milliseconds.
        Linux ping: -W is waittime in seconds.
        """
        timeout = "1000" if platform.system() == "Darwin" else "1"
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping",
                "-c",
                "1",
                "-W",
                timeout,
                ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=3)
            return proc.returncode == 0
        except Exception:
            return False

    async def _fire_change(self, device_id: str, is_home: bool) -> None:
        """Fire registered callbacks on presence change."""
        # Fire registered callbacks
        for cb in self._change_callbacks:
            try:
                if inspect.iscoroutinefunction(cb):
                    await cb(device_id, is_home)
                else:
                    cb(device_id, is_home)
            except Exception as e:
                logger.error("Presence callback error: %s", e)
