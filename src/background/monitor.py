"""Proactive monitor — 60s timer that delegates rule evaluation to RuleEngine.

Runs every 60 seconds, calls rule_engine.evaluate() to get current violations,
then sends Matrix notifications for newly triggered rules (with 30-minute
throttle per rule+room pair).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config import BrainConfig
    from src.hal.rules import RuleEngine, Violation

logger = logging.getLogger(__name__)

# Notification suppression window: 30 minutes per (rule, room) pair
_SUPPRESS_MINUTES = 30


class ProactiveMonitor:
    """60-second timer that delegates rule evaluation to RuleEngine.

    Calls rule_engine.evaluate() each cycle (which handles both sync state
    rules and async time-dependent rules). Sends Matrix notifications for
    violations not recently notified. Never auto-acts.

    Usage::

        monitor = ProactiveMonitor(rule_engine=hal.rules, matrix=matrix, config=config)
        monitor.start()
        # ... later:
        monitor.stop()
    """

    def __init__(
        self,
        rule_engine: Any,
        matrix: Any,
        config: "BrainConfig",
    ) -> None:
        self._rule_engine: RuleEngine = rule_engine
        self._matrix = matrix
        self._config = config
        self._task: asyncio.Task | None = None
        # Throttle: (rule_name, room) -> last_notified datetime
        self._recently_notified: dict[tuple[str, str], datetime] = {}

    def start(self) -> None:
        """Start the 60s monitoring loop as an asyncio Task."""
        self._task = asyncio.create_task(self.run(), name="proactive_monitor")
        logger.info("ProactiveMonitor started")

    def stop(self) -> None:
        """Cancel the monitoring loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("ProactiveMonitor stopped")

    async def run(self) -> None:
        """Infinite loop: run all rules every 60 seconds."""
        logger.info("ProactiveMonitor running (60s interval)")
        while True:
            try:
                await asyncio.sleep(60)
                await self._run_rules()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("ProactiveMonitor error: %s", e)

    async def _run_rules(self) -> None:
        """Evaluate all rules via RuleEngine and send notifications for violations."""
        # Must use await rule_engine.evaluate() (async), NOT get_current_violations() (sync).
        # The async evaluate() ensures time-dependent rules (Rule 3, Rule 6) are
        # re-evaluated every 60 seconds. The reactive _evaluate_sync() via on_change
        # handles the instant state rules.
        violations: list[Violation] = await self._rule_engine.evaluate()
        for violation in violations:
            await self._notify_if_needed(violation.rule, violation.room, violation.message)

    def _is_suppressed(self, rule_name: str, room: str) -> bool:
        """Return True if this (rule, room) was notified within _SUPPRESS_MINUTES."""
        key = (rule_name, room)
        last = self._recently_notified.get(key)
        if last is None:
            return False
        elapsed = (datetime.now() - last).total_seconds() / 60
        return elapsed < _SUPPRESS_MINUTES

    async def _notify_if_needed(self, rule_name: str, room: str, message: str) -> None:
        """Send Matrix notification if not recently suppressed."""
        if self._is_suppressed(rule_name, room):
            return
        self._recently_notified[(rule_name, room)] = datetime.now()
        try:
            await self._matrix.send(self._config.matrix.room_id, message)
            logger.info("ProactiveMonitor notified: [%s/%s] %s", rule_name, room, message)
        except Exception as e:
            logger.warning("ProactiveMonitor notification failed: %s", e)
