"""KNX Agent — Main entry point.

All wiring logic is in src/bootstrapper.py (Bootstrapper class).
main() orchestrates the lifecycle phases and handles signal-based shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


async def main() -> None:
    """Main application entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    logger.info("KNX Agent starting...")
    start_time = datetime.now()

    config_path = Path(__file__).parent.parent / "config.yaml"

    from src.bootstrapper import Bootstrapper

    bootstrapper = Bootstrapper(config_path)
    await bootstrapper.configure()
    await bootstrapper.wire_hal()
    await bootstrapper.wire_tools()
    await bootstrapper.wire_agents()
    await bootstrapper.start()

    # Signal handling
    shutdown_event = asyncio.Event()

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received (%s)", signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info(
        "KNX Agent running (startup took %.1fs)",
        (datetime.now() - start_time).total_seconds(),
    )
    await shutdown_event.wait()

    await bootstrapper.stop()
