# tests/

## Purpose
pytest test suite with asyncio auto mode. No conftest.py — each test file is self-contained with inline mocks.

## Key Files (66 test files)
- `test_hal.py`, `test_hal_gira_x1.py`, `test_hal_state.py`, `test_hal_devices.py`, `test_hal_models.py`, `test_hal_platform.py`, `test_hal_protocols.py`, `test_hal_rules.py` — HAL core + GiraX1Platform (webhook, dispatch, lifecycle, state)
- `test_platform_pipeline.py` — End-to-end platform pipeline tests
- `test_gira_x1.py` — GiraX1Adapter API interactions
- `test_guardrails.py` — GuardrailProvider rule evaluation, HookProvider protocol compliance
- `test_agent_comfort.py`, `test_agent_energy.py`, `test_agent_schedule.py`, `test_agent_security.py` — Agent tool wiring
- `test_supervisor.py` — Supervisor agent orchestration
- `test_router.py` — Intent routing classification
- `test_offline.py` — Regex offline fallback pattern matching
- `test_memory_layer.py`, `test_episodic_memory.py`, `test_semantic_memory.py`, `test_procedural_memory.py`, `test_procedural_schedule.py` — Memory stores
- `test_monitor.py`, `test_patterns.py`, `test_patterns_habits.py` — Background task logic
- `test_reflection.py`, `test_reflection_anomaly.py` — Nightly reflection
- `test_scheduler.py`, `test_tools_schedule.py` — Schedule engine
- `test_presence.py`, `test_tools_presence.py`, `test_presence_platform.py`, `test_arp_presence.py` — Presence tracking
- `test_frigate.py`, `test_frigate_platform.py` — Frigate NVR
- `test_weather.py`, `test_weather_platform.py` — Weather
- `test_door_station.py`, `test_door_station_platform.py` — Door station
- `test_calendar.py`, `test_calendar_platform.py` — Calendar
- `test_mitsubishi_ac.py`, `test_mitsubishi_platform.py` — Mitsubishi AC
- `test_matrix_bot.py`, `test_matrix_voice.py` — Matrix bot
- `test_tools_comfort.py`, `test_tools_energy.py`, `test_tools_memory.py`, `test_tools_security.py`, `test_tools_topology.py` — Tool wrappers
- `test_bootstrapper.py`, `test_main_context.py`, `test_main_schedule.py` — Bootstrapper and wiring
- `test_topology.py`, `test_device_registry.py` — Topology and device registry
- `test_awareness.py`, `test_bus.py`, `test_conversation.py`, `test_conversation_integration.py` — Core infrastructure
- `test_prompts.py`, `test_profiles.py`, `test_user_wiring.py` — Prompts and profiles
- `test_autonomy.py` — Autonomy/guardrail boundary tests
- `test_strands_compat.py` — Strands SDK compatibility
- `test_stt.py` — Whisper STT

## Conventions
- No shared conftest — each test creates its own mocks inline
- Mock classes defined per-file (e.g., `MockGira`, `MockRegistry`, `MockStateStore`)
- Tests use `from src.xxx import ...` (src-layout imports)
- asyncio_mode="auto" — async test functions run automatically
- Tests mock external services (Gira API, ChromaDB, Ollama), never hit real endpoints
