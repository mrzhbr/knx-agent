"""Tests for ProactiveMonitor — 60s timer + throttle + notify."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.background.monitor import ProactiveMonitor
from src.hal.rules import Violation


# ─── Fixtures ──────────────────────────────────────────────────────────────


def _make_config():
    """Create a minimal BrainConfig mock."""
    config = MagicMock()
    config.matrix.room_id = "!test:matrix.local"
    return config


def _make_rule_engine(violations: list[Violation] | None = None) -> MagicMock:
    """Create a mock RuleEngine that returns the given violations."""
    rule_engine = MagicMock()
    rule_engine.evaluate = AsyncMock(return_value=violations or [])
    return rule_engine


def _make_monitor(violations: list[Violation] | None = None):
    """Build a ProactiveMonitor with mocked dependencies."""
    rule_engine = _make_rule_engine(violations)
    matrix = MagicMock()
    matrix.send = AsyncMock()
    config = _make_config()
    return ProactiveMonitor(
        rule_engine=rule_engine,
        matrix=matrix,
        config=config,
    )


# ─── Tests ────────────────────────────────────────────────────────────────


async def test_monitor_forwards_violations_to_matrix():
    """rule_engine.evaluate returns violations -> matrix.send called."""
    violations = [
        Violation(
            rule="heating_window_conflict",
            room="schlafzimmer",
            severity="contradiction",
            message="Heizung in Schlafzimmer läuft, aber Fenster ist offen.",
        )
    ]
    monitor = _make_monitor(violations=violations)
    await monitor._run_rules()

    monitor._matrix.send.assert_awaited_once()
    call_args = monitor._matrix.send.call_args[0]
    assert "Schlafzimmer" in call_args[1] or "Heizung" in call_args[1]


async def test_monitor_throttles_same_rule_room():
    """Same violation sent twice -> only one Matrix notification (suppressed 2nd)."""
    violations = [
        Violation(
            rule="lights_nobody_home",
            room="wohnzimmer",
            severity="suggestion",
            message="Licht in Wohnzimmer ist an, niemand zuhause.",
        )
    ]
    monitor = _make_monitor(violations=violations)

    await monitor._run_rules()
    await monitor._run_rules()

    # Only one send despite two runs — second is throttled
    assert monitor._matrix.send.await_count == 1


async def test_monitor_different_rooms_not_throttled():
    """Same rule in different rooms -> both notifications sent."""
    violations = [
        Violation(
            rule="blinds_late",
            room="schlafzimmer",
            severity="suggestion",
            message="Jalousien in Schlafzimmer offen.",
        ),
        Violation(
            rule="blinds_late",
            room="wohnzimmer",
            severity="suggestion",
            message="Jalousien in Wohnzimmer offen.",
        ),
    ]
    monitor = _make_monitor(violations=violations)
    await monitor._run_rules()

    assert monitor._matrix.send.await_count == 2


async def test_monitor_no_violations_no_sends():
    """Empty violations list -> no matrix.send calls."""
    monitor = _make_monitor(violations=[])
    await monitor._run_rules()
    monitor._matrix.send.assert_not_awaited()


async def test_monitor_start_stop_lifecycle():
    """Start creates asyncio task, stop cancels it."""
    monitor = _make_monitor()
    monitor.start()
    assert monitor._task is not None
    assert not monitor._task.done()
    monitor.stop()
    # Task should be cancelled or finishing
    await asyncio.sleep(0)  # allow cancellation to propagate


async def test_monitor_different_rules_same_room_both_sent():
    """Different rules for same room -> both sent (throttle is per rule+room pair)."""
    violations = [
        Violation(
            rule="heating_window_conflict",
            room="kueche",
            severity="contradiction",
            message="Heizung in Kueche offen.",
        ),
        Violation(
            rule="ac_window_conflict",
            room="kueche",
            severity="contradiction",
            message="Klimaanlage in Kueche offen.",
        ),
    ]
    monitor = _make_monitor(violations=violations)
    await monitor._run_rules()
    assert monitor._matrix.send.await_count == 2


async def test_monitor_calls_async_evaluate():
    """_run_rules must call await rule_engine.evaluate(), not get_current_violations."""
    rule_engine = _make_rule_engine([])
    matrix = MagicMock()
    matrix.send = AsyncMock()
    config = _make_config()
    monitor = ProactiveMonitor(rule_engine=rule_engine, matrix=matrix, config=config)

    await monitor._run_rules()

    rule_engine.evaluate.assert_awaited_once()
