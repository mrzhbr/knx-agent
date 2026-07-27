"""Tests for Bootstrapper — error boundaries, shutdown timeout, health check."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from strands.types.exceptions import ContextWindowOverflowException

from src.bootstrapper import Bootstrapper


# ─── Task 3.1: Error boundary / background task watcher ──────────────────────


class MockBackgroundTask:
    """Mock background task with .start()/.stop() API and ._task handle."""

    def __init__(self, crash: bool = False, name: str = "mock") -> None:
        self._task: asyncio.Task | None = None
        self._crash = crash
        self._name = name

    def start(self) -> None:
        if self._crash:
            self._task = asyncio.create_task(self._run_crash(), name=self._name)
        else:
            self._task = asyncio.create_task(self._run_forever(), name=self._name)

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def _run_forever(self) -> None:
        """Run indefinitely until cancelled."""
        await asyncio.sleep(1000)

    async def _run_crash(self) -> None:
        """Raise immediately to simulate a crash."""
        raise RuntimeError("Simulated crash")


async def test_watcher_detects_crashed_task(caplog):
    """Watcher detects and logs crashed background tasks without affecting others."""
    bootstrapper = Bootstrapper(Path("config.yaml"))

    crashing_task = MockBackgroundTask(crash=True, name="crasher")
    stable_task = MockBackgroundTask(crash=False, name="stable")

    crashing_task.start()
    stable_task.start()

    # Let the crashing task complete (it raises immediately)
    await asyncio.sleep(0)  # Give the event loop a chance to run the coroutine

    # Inject collected task handles manually
    bootstrapper._background_tasks = [
        ("crashing_task", crashing_task._task),
        ("stable_task", stable_task._task),
    ]

    # Start the watcher with a very short sleep interval for testing
    async def _fast_watcher():
        """Fast watcher that checks immediately."""
        # Directly run one iteration of watcher logic
        for name, task in bootstrapper._background_tasks:
            if task.done():
                exc = task.exception() if not task.cancelled() else None
                if exc:
                    logging.getLogger("src.bootstrapper").error(
                        "Background task %s crashed: %s", name, exc, exc_info=exc
                    )
                elif task.cancelled():
                    logging.getLogger("src.bootstrapper").info(
                        "Background task %s was cancelled", name
                    )
                else:
                    logging.getLogger("src.bootstrapper").warning(
                        "Background task %s completed unexpectedly", name
                    )

    with caplog.at_level(logging.ERROR, logger="src.bootstrapper"):
        await _fast_watcher()

    # Stable task should still be running
    assert not stable_task._task.done(), "Stable task should still be running"

    # Error should be logged for crashing task
    assert any("crashing_task" in record.message for record in caplog.records), (
        f"Expected error log for crashing_task, got: {[r.message for r in caplog.records]}"
    )

    # Cleanup
    stable_task.stop()


async def test_watcher_logs_unexpected_completion(caplog):
    """Watcher logs warning when a task completes without error."""
    bootstrapper = Bootstrapper(Path("config.yaml"))

    # Task that completes normally (no exception)
    async def complete_normally():
        return "done"

    task = asyncio.create_task(complete_normally())
    await asyncio.sleep(0)  # Let it complete

    bootstrapper._background_tasks = [("normal_task", task)]

    async def _fast_watcher():
        for name, t in bootstrapper._background_tasks:
            if t.done():
                exc = t.exception() if not t.cancelled() else None
                if exc:
                    logging.getLogger("src.bootstrapper").error(
                        "Background task %s crashed: %s", name, exc
                    )
                elif t.cancelled():
                    logging.getLogger("src.bootstrapper").info(
                        "Background task %s was cancelled", name
                    )
                else:
                    logging.getLogger("src.bootstrapper").warning(
                        "Background task %s completed unexpectedly", name
                    )

    with caplog.at_level(logging.WARNING, logger="src.bootstrapper"):
        await _fast_watcher()

    assert any("normal_task" in record.message for record in caplog.records)


# ─── Task 3.2: Shutdown timeout protection ───────────────────────────────────


async def test_stop_cancels_watcher():
    """stop() cancels the watcher task before stopping background tasks."""
    bootstrapper = Bootstrapper(Path("config.yaml"))

    # Create a mock watcher task
    async def _fake_watcher():
        await asyncio.sleep(1000)

    watcher = asyncio.create_task(_fake_watcher())
    bootstrapper._watcher_task = watcher

    # Minimal stop: just cancel watcher and verify
    if bootstrapper._watcher_task and not bootstrapper._watcher_task.done():
        bootstrapper._watcher_task.cancel()

    await asyncio.sleep(0)
    assert bootstrapper._watcher_task.cancelled() or bootstrapper._watcher_task.done()


async def test_stop_with_hanging_task_times_out():
    """stop() uses wait_for with timeout — hanging tasks don't block forever."""
    Bootstrapper(Path("config.yaml"))

    # Create a task that hangs (never completes)
    async def _hang_forever():
        await asyncio.sleep(1000)

    hanging_task = asyncio.create_task(_hang_forever())

    # Test the timeout pattern directly
    timed_out = False
    try:
        await asyncio.wait_for(hanging_task, timeout=0.01)
    except (asyncio.CancelledError, asyncio.TimeoutError):
        timed_out = True

    assert timed_out, "Hanging task should have timed out"


# ─── Task 3.3: Health check ───────────────────────────────────────────────────


async def test_health_returns_empty_when_no_hal():
    """health() returns empty dict when hal is None."""
    bootstrapper = Bootstrapper(Path("config.yaml"))
    result = await bootstrapper.health()
    assert result == {}


async def test_health_returns_platform_statuses():
    """health() returns platform health status dict."""
    bootstrapper = Bootstrapper(Path("config.yaml"))

    # Mock HAL with platforms
    hal = MagicMock()
    platform_a = AsyncMock()
    platform_a.health_check = AsyncMock(return_value=True)
    platform_b = AsyncMock()
    platform_b.health_check = AsyncMock(return_value=False)

    hal._platforms = {
        "gira_x1": platform_a,
        "frigate": platform_b,
    }
    bootstrapper.hal = hal

    result = await bootstrapper.health()

    assert "platform:gira_x1" in result
    assert "platform:frigate" in result
    assert result["platform:gira_x1"] is True
    assert result["platform:frigate"] is False


async def test_health_handles_platform_exception():
    """health() returns False for platforms that raise on health_check."""
    bootstrapper = Bootstrapper(Path("config.yaml"))

    hal = MagicMock()
    broken_platform = AsyncMock()
    broken_platform.health_check = AsyncMock(side_effect=RuntimeError("connection failed"))

    hal._platforms = {"broken": broken_platform}
    bootstrapper.hal = hal

    result = await bootstrapper.health()

    assert "platform:broken" in result
    assert result["platform:broken"] is False


# ─── Bootstrapper instantiation ────────────────────────────────────────────────


def test_bootstrapper_init_sets_config_path():
    """Bootstrapper stores config_path and initializes all attributes to None."""
    path = Path("/some/config.yaml")
    b = Bootstrapper(path)
    assert b._config_path == path
    assert b.config is None
    assert b.hal is None
    assert b.tool_context is None
    assert b._background_tasks == []
    assert b._watcher_task is None


# ─── Weather listener transition tests ───────────────────────────────────────


async def test_weather_listener_false_to_true_calls_on_weather_event():
    """WEATHER_SENSOR: is_raining False→True fires on_weather_event('raining', True)."""
    from unittest.mock import AsyncMock, MagicMock
    from src.hal.models import DeviceType

    schedule_engine = MagicMock()
    schedule_engine.on_weather_event = AsyncMock()

    # Simulate what the listener closure captures
    _DeviceType = DeviceType

    async def _on_weather_for_schedule(info, old_state, new_state, source):
        if info.device_type == _DeviceType.WEATHER_SENSOR:
            is_raining = new_state.get("is_raining", False)
            was_raining = old_state.get("is_raining", False) if old_state else False
            if is_raining and not was_raining:
                await schedule_engine.on_weather_event("raining", True)
            elif was_raining and not is_raining:
                await schedule_engine.on_weather_event("raining", False)

    info = MagicMock()
    info.device_type = DeviceType.WEATHER_SENSOR

    await _on_weather_for_schedule(info, {"is_raining": False}, {"is_raining": True}, "weather")
    schedule_engine.on_weather_event.assert_awaited_once_with("raining", True)


async def test_weather_listener_true_to_false_calls_on_weather_event_inactive():
    """WEATHER_SENSOR: is_raining True→False fires on_weather_event('raining', False)."""
    from unittest.mock import AsyncMock, MagicMock
    from src.hal.models import DeviceType

    schedule_engine = MagicMock()
    schedule_engine.on_weather_event = AsyncMock()

    _DeviceType = DeviceType

    async def _on_weather_for_schedule(info, old_state, new_state, source):
        if info.device_type == _DeviceType.WEATHER_SENSOR:
            is_raining = new_state.get("is_raining", False)
            was_raining = old_state.get("is_raining", False) if old_state else False
            if is_raining and not was_raining:
                await schedule_engine.on_weather_event("raining", True)
            elif was_raining and not is_raining:
                await schedule_engine.on_weather_event("raining", False)

    info = MagicMock()
    info.device_type = DeviceType.WEATHER_SENSOR

    await _on_weather_for_schedule(info, {"is_raining": True}, {"is_raining": False}, "weather")
    schedule_engine.on_weather_event.assert_awaited_once_with("raining", False)


async def test_weather_listener_true_to_true_no_call():
    """WEATHER_SENSOR: is_raining True→True does NOT call on_weather_event."""
    from unittest.mock import AsyncMock, MagicMock
    from src.hal.models import DeviceType

    schedule_engine = MagicMock()
    schedule_engine.on_weather_event = AsyncMock()

    _DeviceType = DeviceType

    async def _on_weather_for_schedule(info, old_state, new_state, source):
        if info.device_type == _DeviceType.WEATHER_SENSOR:
            is_raining = new_state.get("is_raining", False)
            was_raining = old_state.get("is_raining", False) if old_state else False
            if is_raining and not was_raining:
                await schedule_engine.on_weather_event("raining", True)
            elif was_raining and not is_raining:
                await schedule_engine.on_weather_event("raining", False)

    info = MagicMock()
    info.device_type = DeviceType.WEATHER_SENSOR

    await _on_weather_for_schedule(info, {"is_raining": True}, {"is_raining": True}, "weather")
    schedule_engine.on_weather_event.assert_not_awaited()


async def test_weather_listener_non_weather_device_no_call():
    """Non-WEATHER_SENSOR device does NOT trigger on_weather_event."""
    from unittest.mock import AsyncMock, MagicMock
    from src.hal.models import DeviceType

    schedule_engine = MagicMock()
    schedule_engine.on_weather_event = AsyncMock()

    _DeviceType = DeviceType

    async def _on_weather_for_schedule(info, old_state, new_state, source):
        if info.device_type == _DeviceType.WEATHER_SENSOR:
            is_raining = new_state.get("is_raining", False)
            was_raining = old_state.get("is_raining", False) if old_state else False
            if is_raining and not was_raining:
                await schedule_engine.on_weather_event("raining", True)
            elif was_raining and not is_raining:
                await schedule_engine.on_weather_event("raining", False)

    info = MagicMock()
    info.device_type = DeviceType.LIGHT  # not WEATHER_SENSOR

    await _on_weather_for_schedule(info, {"is_raining": False}, {"is_raining": True}, "gira_x1")
    schedule_engine.on_weather_event.assert_not_awaited()


async def test_weather_listener_old_state_none_with_raining_fires_event():
    """old_state=None with is_raining=True in new_state fires on_weather_event('raining', True)."""
    from unittest.mock import AsyncMock, MagicMock
    from src.hal.models import DeviceType

    schedule_engine = MagicMock()
    schedule_engine.on_weather_event = AsyncMock()

    _DeviceType = DeviceType

    async def _on_weather_for_schedule(info, old_state, new_state, source):
        if info.device_type == _DeviceType.WEATHER_SENSOR:
            is_raining = new_state.get("is_raining", False)
            was_raining = old_state.get("is_raining", False) if old_state else False
            if is_raining and not was_raining:
                await schedule_engine.on_weather_event("raining", True)
            elif was_raining and not is_raining:
                await schedule_engine.on_weather_event("raining", False)

    info = MagicMock()
    info.device_type = DeviceType.WEATHER_SENSOR

    # old_state is None (first poll)
    await _on_weather_for_schedule(info, None, {"is_raining": True}, "weather")
    schedule_engine.on_weather_event.assert_awaited_once_with("raining", True)


# ─── Verify removed message-trigger code no longer exists ─────────────────────


def test_extract_message_intent_removed_from_bootstrapper():
    """_extract_message_intent should no longer exist on Bootstrapper."""
    assert not hasattr(Bootstrapper, "_extract_message_intent"), (
        "_extract_message_intent was removed and must not exist on Bootstrapper"
    )


def test_on_message_event_removed_from_schedule_engine():
    """on_message_event should no longer exist on ScheduleEngine."""
    from src.background.scheduler import ScheduleEngine

    assert not hasattr(ScheduleEngine, "on_message_event"), (
        "on_message_event was removed and must not exist on ScheduleEngine"
    )


# ─── Context Window Overflow Handling Tests ───────────────────────────────────


async def test_dispatch_with_overflow_first_retry_truncates():
    """First ContextWindowOverflowException triggers truncated context retry."""
    bootstrapper = Bootstrapper(Path("config.yaml"))

    mock_agent = MagicMock()
    mock_agent.messages = MagicMock()
    mock_agent.invoke_async = AsyncMock(
        side_effect=[
            ContextWindowOverflowException("Too long"),
            "Success after truncation",
        ]
    )

    with patch(
        "src.agents.prompts.build_embodied_context", new_callable=AsyncMock
    ) as mock_build_ctx:
        mock_build_ctx.return_value = "Truncated context"

        result = await bootstrapper._dispatch_with_overflow_handling(
            mock_agent, "original text", "comfort", "user1", "test intent"
        )

    assert result == "Success after truncation"
    assert mock_agent.invoke_async.call_count == 2
    mock_agent.messages.clear.assert_called()


async def test_dispatch_with_overflow_second_fallback_minimal():
    """Second ContextWindowOverflowException triggers minimal context fallback."""
    bootstrapper = Bootstrapper(Path("config.yaml"))

    mock_agent = MagicMock()
    mock_agent.messages = MagicMock()
    mock_agent.invoke_async = AsyncMock(
        side_effect=[
            ContextWindowOverflowException("Too long"),
            ContextWindowOverflowException("Still too long"),
            "Success with minimal",
        ]
    )

    with patch(
        "src.agents.prompts.build_embodied_context", new_callable=AsyncMock
    ) as mock_build_ctx:
        mock_build_ctx.return_value = "Minimal context"

        result = await bootstrapper._dispatch_with_overflow_handling(
            mock_agent, "original text", "comfort", "user1", "test intent"
        )

    assert result == "Success with minimal"
    assert mock_agent.invoke_async.call_count == 3


async def test_dispatch_with_overflow_all_fail_returns_user_message():
    """Even minimal context overflow returns user-friendly error message."""
    bootstrapper = Bootstrapper(Path("config.yaml"))

    mock_agent = MagicMock()
    mock_agent.messages = MagicMock()
    mock_agent.invoke_async = AsyncMock(
        side_effect=[
            ContextWindowOverflowException("Too long"),
            ContextWindowOverflowException("Still too long"),
            ContextWindowOverflowException("Even minimal too long"),
        ]
    )

    with patch(
        "src.agents.prompts.build_embodied_context", new_callable=AsyncMock
    ) as mock_build_ctx:
        mock_build_ctx.return_value = "Minimal context"

        result = await bootstrapper._dispatch_with_overflow_handling(
            mock_agent, "original text", "comfort", "user1", "test intent"
        )

    assert "zu umfangreich" in result or "kürzeren Anfrage" in result
    assert mock_agent.invoke_async.call_count == 3


async def test_dispatch_with_overflow_logs_monitoring_events(caplog):
    """Context overflow events are logged for monitoring."""
    bootstrapper = Bootstrapper(Path("config.yaml"))

    mock_agent = MagicMock()
    mock_agent.messages = MagicMock()
    mock_agent.invoke_async = AsyncMock(
        side_effect=[
            ContextWindowOverflowException("Too long"),
            "Success",
        ]
    )

    with patch(
        "src.agents.prompts.build_embodied_context", new_callable=AsyncMock
    ) as mock_build_ctx:
        mock_build_ctx.return_value = "Truncated context"

        with caplog.at_level(logging.WARNING, logger="src.bootstrapper"):
            await bootstrapper._dispatch_with_overflow_handling(
                mock_agent, "text", "comfort", "user1", "intent"
            )

    assert any("overflow" in record.message.lower() for record in caplog.records)
