"""ScheduleEngine — asyncio-based cron and one-shot task scheduler.

Runs a 60-second tick loop. Evaluates cron schedules using croniter.
Fires one-shot tasks at their scheduled time. Hooks into ARPPresenceAdapter
for presence-triggered routine execution.

All datetime comparisons use timezone-aware datetimes via zoneinfo.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Callable
from zoneinfo import ZoneInfo

try:
    from croniter import croniter as _croniter_cls
except ImportError:
    _croniter_cls = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from src.config import BrainConfig
    from src.memory.layer import MemoryLayer

logger = logging.getLogger(__name__)


def _matched_int(match: Any, group: int) -> int | None:
    """Convert a regex group without leaking parsing exceptions."""
    try:
        return int(match.group(group))
    except (IndexError, TypeError, ValueError):
        return None


def _parse_time_string(text: str, tz: ZoneInfo) -> datetime | None:
    """Parse German natural time strings to timezone-aware datetime.

    Supports:
    - "in X minuten" / "in X min"
    - "in X stunden"
    - "um HH:MM"
    - "morgen HH:MM" / "morgen um HH:MM"

    Returns None if parsing fails.
    """
    import re

    text = text.strip().lower()
    now = datetime.now(tz)

    # "in X minuten" / "in X min"
    m = re.match(r"in\s+(\d+)\s*(?:minuten?|min)", text)
    if m:
        minutes = _matched_int(m, 1)
        return now + timedelta(minutes=minutes) if minutes is not None else None

    # "in X stunden"
    m = re.match(r"in\s+(\d+)\s*stunden?", text)
    if m:
        hours = _matched_int(m, 1)
        return now + timedelta(hours=hours) if hours is not None else None

    # "um HH:MM" or just "HH:MM"
    m = re.match(r"(?:um\s+)?(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = _matched_int(m, 1), _matched_int(m, 2)
        if h is None or mi is None or h > 23 or mi > 59:
            return None
        target = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    # "morgen HH:MM" or "morgen um HH:MM"
    m = re.match(r"morgen\s+(?:um\s+)?(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = _matched_int(m, 1), _matched_int(m, 2)
        if h is None or mi is None or h > 23 or mi > 59:
            return None
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=h, minute=mi, second=0, microsecond=0)

    return None


class ScheduleEngine:
    """Asyncio-based schedule engine for cron and one-shot tasks.

    Tick loop evaluates schedules every 60 seconds. Presence hook fires
    routines tagged with trigger_type="presence".

    Usage::

        engine = ScheduleEngine(memory_layer, tool_registry, config=config)
        engine.start()
        # wire presence: arp_presence.on_change(engine.on_presence_event)
        # ...
        engine.stop()
    """

    def __init__(
        self,
        memory_layer: "MemoryLayer",
        tool_registry: dict[str, Callable],
        config: "BrainConfig",
        supervisor_fn: Callable | None = None,
        matrix: Any = None,
    ) -> None:
        self._memory = memory_layer
        self._tool_registry = tool_registry
        self._supervisor_fn = supervisor_fn
        self._matrix = matrix
        self._config = config
        self._task: asyncio.Task | None = None
        try:
            self._tz = ZoneInfo(config.home.timezone)
        except (KeyError, Exception) as e:
            logger.warning(
                "Invalid timezone '%s': %s — falling back to Europe/Berlin", config.home.timezone, e
            )
            self._tz = ZoneInfo("Europe/Berlin")
        self._weather_last_fired: dict[tuple[str, int], datetime] = {}
        self._time_last_fired: dict[int, datetime] = {}

    @property
    def supervisor_fn(self) -> Callable | None:
        return self._supervisor_fn

    @supervisor_fn.setter
    def supervisor_fn(self, fn: Callable | None) -> None:
        self._supervisor_fn = fn

    def start(self) -> None:
        """Start the 60-second schedule tick loop as an asyncio Task."""
        self._task = asyncio.create_task(self._run(), name="schedule_engine")
        logger.info("ScheduleEngine started")

    def stop(self) -> None:
        """Cancel the schedule tick loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("ScheduleEngine stopped")

    async def _run(self) -> None:
        """Infinite loop: tick every 60 seconds."""
        logger.info("ScheduleEngine running (60s tick)")
        while True:
            try:
                await asyncio.sleep(60)
                await self._tick()
            except asyncio.CancelledError as cancellation:
                raise cancellation
            except Exception as e:
                logger.error("ScheduleEngine tick error: %s", e)

    async def _tick(self) -> None:
        """Evaluate all active schedules against the current time."""
        now = datetime.now(self._tz)
        logger.debug("ScheduleEngine tick at %s", now.strftime("%H:%M"))

        try:
            schedules = await self._memory.procedural.get_active_schedules()
        except Exception as e:
            logger.warning("ScheduleEngine: could not load schedules: %s", e)
            return

        for sched in schedules:
            try:
                await self._evaluate_schedule(sched, now)
            except Exception as e:
                logger.warning(
                    "ScheduleEngine: error evaluating schedule %s: %s", sched.get("id"), e
                )

        try:
            await self._memory.procedural.delete_expired_one_shots()
        except Exception as e:
            logger.debug("ScheduleEngine: delete_expired_one_shots failed: %s", e)

        await self._evaluate_time_routines(now)

    async def _evaluate_time_routines(self, now: datetime) -> None:
        """Evaluate time-triggered routines for the current tick."""
        if _croniter_cls is None:
            return  # croniter not installed — skip silently
        try:
            routines = await self._memory.procedural.get_routines_by_trigger("time")
        except Exception as e:
            logger.warning("ScheduleEngine: could not load time routines: %s", e)
            return

        for routine in routines:
            try:
                routine_id: int = routine["id"]
                cron_expr = routine.get("cron_expr")
                if not cron_expr:
                    continue  # No cron expression stored — skip

                # Debounce: skip if fired within the last 90 seconds
                last_fired = self._time_last_fired.get(routine_id)
                if last_fired is not None:
                    elapsed = (now - last_fired).total_seconds()
                    if elapsed < 90:
                        continue

                # Check if this cron expression matches the current minute
                if not _croniter_cls.match(cron_expr, now):
                    continue

                logger.info(
                    "ScheduleEngine: time routine %d (%s) firing at %s",
                    routine_id,
                    routine.get("name", "?"),
                    now.strftime("%H:%M"),
                )
                await self._execute_automated_routine(routine_id)
                self._time_last_fired[routine_id] = now
            except Exception as e:
                logger.warning(
                    "ScheduleEngine: error evaluating time routine %s: %s",
                    routine.get("id"),
                    e,
                )

    async def _evaluate_schedule(self, sched: dict, now: datetime) -> None:
        """Check if a schedule should fire now and execute it."""
        sched_id = sched["id"]
        last_run = sched.get("last_run")
        cron_expr = sched.get("cron_expr")
        one_shot_at = sched.get("one_shot_at")

        if cron_expr:
            # Check if cron fires this minute (and hasn't already run this minute)
            if last_run:
                try:
                    last_run_dt = datetime.fromisoformat(last_run)
                    # Already ran within the current minute
                    if (now - last_run_dt).total_seconds() < 60:
                        return
                except ValueError:
                    pass
            try:
                if _croniter_cls is None:
                    logger.warning("croniter not installed — cron schedules disabled")
                    return
                if _croniter_cls.match(cron_expr, now):
                    logger.info("ScheduleEngine: cron schedule %d firing", sched_id)
                    await self._execute_schedule(sched)
            except Exception as e:
                logger.warning("ScheduleEngine: croniter error for schedule %s: %s", sched_id, e)

        elif one_shot_at:
            if last_run:
                return  # Already executed
            try:
                fire_at = datetime.fromisoformat(one_shot_at)
                # Make timezone-aware if naive
                if fire_at.tzinfo is None:
                    fire_at = fire_at.replace(tzinfo=self._tz)
                if now >= fire_at:
                    logger.info("ScheduleEngine: one-shot schedule %d firing", sched_id)
                    await self._execute_schedule(sched)
            except ValueError as e:
                logger.warning("ScheduleEngine: invalid one_shot_at for %s: %s", sched_id, e)

    async def _execute_automated_routine(self, routine_id: int) -> str:
        """Execute any trigger-driven routine with explicit automation provenance."""
        from src.core.guardrails import RequestSource, request_security_context

        with request_security_context(source=RequestSource.AUTOMATION):
            return await self.execute_routine(routine_id)

    async def _execute_schedule(self, sched: dict) -> None:
        """Execute one schedule under immutable automation provenance."""
        from src.core.guardrails import RequestSource, request_security_context

        with request_security_context(source=RequestSource.AUTOMATION):
            await self._execute_schedule_in_context(sched)

    async def _execute_schedule_in_context(self, sched: dict) -> None:
        """Implementation for a schedule after provenance has been bound."""
        # Set training contextvars for scheduled tasks that use LLM (Issue #34)
        import uuid as _uuid
        from src.training.context import set_request_id, set_user, set_agent_name, set_model_id

        _request_id = f"sched-{_uuid.uuid4().hex[:10]}"
        set_request_id(_request_id)
        set_user("system:scheduler")
        set_agent_name("scheduler")
        set_model_id("")

        action_type = sched.get("action_type", "")
        action_params_raw = sched.get("action_params", "{}")
        sched_id = sched["id"]

        try:
            action_params = json.loads(action_params_raw) if action_params_raw else {}
        except json.JSONDecodeError as decode_error:
            logger.warning("Schedule %s has invalid action parameters", sched_id)
            del decode_error
            action_params = {"text": action_params_raw}

        # Mark as run first to prevent duplicate execution
        await self._memory.procedural.mark_schedule_run(sched_id)

        if action_type in {"alarm_control", "door_open"}:
            logger.warning("Blocked security-critical scheduled action '%s'", action_type)
            return

        if action_type == "llm_dispatch":
            # Route through supervisor LLM
            text = action_params.get("text", str(action_params))
            if self._supervisor_fn is not None:
                try:
                    result = str(self._supervisor_fn(text))
                    logger.info("ScheduleEngine: llm_dispatch result: %s", result[:100])
                    if self._matrix and self._config.matrix.room_id:
                        await self._matrix.send(
                            self._config.matrix.room_id,
                            f"Zeitplan ausgeführt: {result}",
                        )
                except Exception as e:
                    logger.warning("ScheduleEngine: supervisor dispatch failed: %s", e)
            else:
                logger.warning("ScheduleEngine: llm_dispatch but no supervisor_fn set")
        elif sched.get("routine_id"):
            await self.execute_routine(sched["routine_id"])
        else:
            # Direct tool dispatch
            tool_fn = self._tool_registry.get(action_type)
            if tool_fn is None:
                logger.warning(
                    "ScheduleEngine: unknown tool '%s' in schedule %s", action_type, sched_id
                )
                return
            try:
                result = await tool_fn(**action_params)
                logger.info("ScheduleEngine: tool '%s' result: %s", action_type, str(result)[:100])
            except Exception as e:
                logger.warning("ScheduleEngine: tool '%s' failed: %s", action_type, e)

    async def execute_routine(self, routine_id_or_name: int | str) -> str:
        """Execute a routine by id or name. Public method (no underscore).

        Loads routine from procedural memory, iterates actions, calls tool functions.
        Returns combined results string.
        """
        # Set training contextvars for routine execution (Issue #34)
        import uuid as _uuid
        from src.training.context import set_request_id, set_user, set_agent_name, set_model_id

        _request_id = f"rtn-{_uuid.uuid4().hex[:10]}"
        set_request_id(_request_id)
        set_user("system:scheduler")
        set_agent_name("scheduler")
        set_model_id("")

        procedural = self._memory.procedural

        # Load the routine
        routine: dict | None = None
        if isinstance(routine_id_or_name, int):
            routines = await procedural.get_routines(enabled_only=False)
            routine = next((r for r in routines if r.get("id") == routine_id_or_name), None)
        else:
            routine = await procedural.get_routine_by_name(str(routine_id_or_name))

        if routine is None:
            return f"Routine '{routine_id_or_name}' nicht gefunden."

        routine_id = routine.get("id")
        routine_name = routine.get("name", str(routine_id_or_name))
        actions = routine.get("actions", [])
        autonomy_level = routine.get("autonomy_level", 0)

        # Security cap
        if self._is_security_action(actions):
            autonomy_level = 0

        # Level 0: suggest only
        if autonomy_level == 0:
            msg = f"Soll ich '{routine_name}' ausführen?"
            if self._matrix and self._config.matrix.room_id:
                try:
                    await self._matrix.send(self._config.matrix.room_id, msg)
                except Exception as e:
                    logger.warning("ScheduleEngine: Matrix send failed: %s", e)
            return msg

        # Execute the routine actions
        results: list[str] = []
        success = True
        for action in actions:
            try:
                tool_name = action.get("tool")
                args = action.get("args", {})
                description = action.get("description", "")

                if tool_name and tool_name in self._tool_registry:
                    result = await self._tool_registry[tool_name](**args)
                    results.append(str(result))
                elif tool_name:
                    logger.warning("ScheduleEngine: tool '%s' not in registry", tool_name)
                    results.append(f"Tool '{tool_name}' nicht verfügbar.")
                    success = False
                elif description and self._supervisor_fn:
                    # Re-route description through LLM
                    result = str(self._supervisor_fn(description))
                    results.append(result)
                else:
                    results.append(f"Aktion '{description}' übersprungen.")
            except Exception as e:
                logger.error("ScheduleEngine: action failed in routine '%s': %s", routine_name, e)
                results.append(f"Fehler: {e}")
                success = False

        combined = "; ".join(results) if results else "Keine Aktionen ausgeführt."

        # Log execution
        if routine_id is not None:
            try:
                await procedural.log_routine_execution(routine_id, success=success)
            except Exception as e:
                logger.debug("ScheduleEngine: log_routine_execution failed: %s", e)

        # Notify based on autonomy level
        # Level 1: execute + inform. Level 2+: silent (only log).
        if autonomy_level == 1 and self._matrix and self._config.matrix.room_id:
            try:
                await self._matrix.send(
                    self._config.matrix.room_id,
                    f"Routine '{routine_name}' ausgeführt: {combined}",
                )
            except Exception as e:
                logger.debug("ScheduleEngine: Matrix notification failed: %s", e)

        logger.info(
            "ScheduleEngine: routine '%s' executed (level=%d, success=%s)",
            routine_name,
            autonomy_level,
            success,
        )
        return combined

    def _is_security_action(self, actions: list[dict]) -> bool:
        """Check if any action in the list involves security-critical tools."""
        security_tools = {"alarm_control", "door_open"}
        for action in actions:
            tool_name = action.get("tool", "")
            if tool_name in security_tools:
                return True
            # Also check description for security keywords
            desc = action.get("description", "").lower()
            if any(kw in desc for kw in ("alarm", "tür öffnen", "door_open")):
                return True
        return False

    async def on_presence_event(self, person: str, is_home: bool) -> None:
        """Handle presence state changes. Fires presence-triggered routines.

        Called from ARPPresenceAdapter via on_change() callback.
        """
        trigger_value = "arriving" if is_home else "leaving"
        logger.info("ScheduleEngine: presence event — %s %s", person, trigger_value)

        try:
            routines = await self._memory.procedural.get_routines_by_trigger(
                "presence", trigger_value
            )
        except Exception as e:
            logger.warning("ScheduleEngine: could not load presence routines: %s", e)
            return

        for routine in routines:
            autonomy_level = routine.get("autonomy_level", 0)
            if autonomy_level >= 1:
                routine_id = routine.get("id")
                if routine_id is not None:
                    try:
                        await self._execute_automated_routine(routine_id)
                    except Exception as e:
                        logger.warning(
                            "ScheduleEngine: presence routine %s failed: %s", routine_id, e
                        )

    async def on_weather_event(self, condition: str, active: bool) -> None:
        """Handle weather condition changes. Fires weather-triggered routines.

        Called from StateStore on_change() listener when weather state transitions.
        Only executes routines when active=True (condition starts, not stops).
        Debounces: same routine won't re-fire within 30 minutes.
        """
        if not active:
            logger.debug("ScheduleEngine: weather condition '%s' inactive — skipping", condition)
            return

        logger.info("ScheduleEngine: weather event — %s active=%s", condition, active)

        try:
            routines = await self._memory.procedural.get_routines_by_trigger("weather", condition)
        except Exception as e:
            logger.warning("ScheduleEngine: could not load weather routines: %s", e)
            return

        now = datetime.now(self._tz)
        debounce_minutes = 30

        for routine in routines:
            autonomy_level = routine.get("autonomy_level", 0)
            routine_id = routine.get("id")
            if autonomy_level < 1 or routine_id is None:
                continue

            debounce_key = (condition, routine_id)
            last_fired = self._weather_last_fired.get(debounce_key)
            if last_fired and (now - last_fired) < timedelta(minutes=debounce_minutes):
                logger.debug(
                    "ScheduleEngine: weather routine %d debounced (last fired %s)",
                    routine_id,
                    last_fired.isoformat(),
                )
                continue

            try:
                await self._execute_automated_routine(routine_id)
                self._weather_last_fired[debounce_key] = now
            except Exception as e:
                logger.warning("ScheduleEngine: weather routine %s failed: %s", routine_id, e)

    async def add_one_shot(
        self,
        description: str,
        when_str: str,
        action_type: str,
        action_params: dict,
        created_by: str | None = None,
    ) -> int:
        """Parse natural time string and store a one-shot schedule.

        Supports: "in X minuten", "in X stunden", "um HH:MM", "morgen HH:MM".
        Returns schedule id.
        Raises ValueError if time string cannot be parsed.
        """
        fire_at = _parse_time_string(when_str, self._tz)
        if fire_at is None:
            raise ValueError(f"Konnte Zeitangabe nicht verstehen: '{when_str}'")

        fire_at_iso = fire_at.isoformat()
        sid = await self._memory.procedural.add_schedule(
            description=description,
            action_type=action_type,
            action_params=json.dumps(action_params),
            one_shot_at=fire_at_iso,
            created_by=created_by,
        )
        logger.info("ScheduleEngine: one-shot %d scheduled at %s", sid, fire_at_iso)
        return sid
