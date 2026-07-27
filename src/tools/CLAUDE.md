# tools/

## Purpose
Thin wrappers over HAL that agents call as Strands tools. The LLM works with human-readable room/device names — tools resolve to HAL device IDs internally.

## Key Files
- `context.py` — `ToolContext`: frozen dataclass, single dependency container (hal, memory, presence, awareness, topology, config, energy_awareness, scheduler)
- `comfort.py` — `lights_control`, `blinds_control`, `heating_control`, `execute_batch`, `ac_control`, `get_ac_status`, `get_room_state`, `list_rooms`; `_AC_MODE_MAP` for AC modes
- `security.py` — `alarm_control`, `door_open`, `get_security_status`, `get_camera_events`
- `energy.py` — `get_energy_summary`, `get_weather`
- `schedule.py` — `schedule_task`, `execute_routine`, `list_schedules`, `cancel_schedule`
- `memory.py` — `remember`, `recall`, `recall_preference`, `remember_correction`, `describe_device`, `get_unknown_devices`
- `presence.py` — `who_is_home`, `get_window_states`
- `topology.py` — `get_room_info`, `get_nearby_rooms`, `get_rooms_by_floor`, `get_solar_exposure`
- `rules.py` — `configure_house_rule`, `list_house_rules`, `approve_custom_rule`
- `scenes.py` — `create_scene`, `execute_scene`, `list_scenes`, `delete_scene`

## Conventions
- Each module has `_ctx: ToolContext | None = None` and `def set_context(ctx: ToolContext) -> None`
- All 9 tool modules initialized via `Bootstrapper.wire_tools()` calling `set_context(tool_context)`
- Tools use room/device names, never UIDs (resolved via HAL)
- Return human-readable German strings (for LLM consumption)
- Safety-critical tools (`alarm_control`, `door_open`) enforced by GuardrailProvider

## Dependencies
- Depends on: `src/tools/context.py` (ToolContext), `src/hal/` (HAL), `src/memory/` (MemoryLayer)
- Depended on by: `agents/` (registered as agent tools), `bootstrapper.py`
