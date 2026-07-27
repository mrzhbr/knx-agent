# background/

## Purpose
Autonomous background tasks running on timers without user interaction: proactive monitoring, pattern detection, nightly reflection, and schedule execution.

## Key Files
- `monitor.py` — `ProactiveMonitor`: 60s loop checking for actionable conditions (open windows + heating, lights left on, energy waste); `_SUPPRESS_MINUTES` prevents notification spam; `_notify_if_needed` sends alerts via Matrix bot
- `patterns.py` — `PatternDetector`: hourly analysis; `_gather_presence_patterns` finds recurring behaviors; `_call_router_tier` classifies candidate routines; thresholds: `_MIN_EVENTS`, `_MIN_CONFIDENCE`, `_MIN_OCCURRENCES`
- `reflection.py` — `ReflectionAgent`: nightly (sleeps until target time via `_sleep_until`); `_should_reflect`/`_check_sufficient_data`; `_write_health_snapshot`; `_route_anomalies`/`_format_anomaly_notification`; `_call_reflection_tier` for weekly analysis; `_DAILY_WINDOW_DAYS` lookback
- `scheduler.py` — `ScheduleEngine`: `_tick` evaluates pending schedules; `_evaluate_schedule`/`_execute_schedule`; `execute_routine`; `on_presence_event` for presence-triggered routines; `_is_security_action` flags actions needing confirmation; `add_one_shot` for one-time tasks

## Conventions
- All implement `start`/`stop` pattern with asyncio tasks
- Monitor rules are pure Python — no LLM involved
- Pattern detector only stores candidates; user must approve activation
- Reflection outputs structured data, never auto-applies changes
- Security actions never auto-execute (always require confirmation)

## Dependencies
- Depends on: `memory/` (episodic, procedural), `core/` (awareness, bus, events), `bot/` (notifications), `agents/` (reflection uses reflection tier)
- Depended on by: nothing (runs independently as asyncio tasks from main startup)
