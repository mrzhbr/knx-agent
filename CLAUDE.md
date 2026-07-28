# KNX Agent

## Overview

Local-first embodied AI home automation agent for KNX installations connected through native KNX/IP or the Gira X1 REST API. The agent uses first-person awareness of state changes, detections, and presence events and communicates via Matrix/Synapse in German.

## Tech Stack

- Python 3.12+, setuptools
- Strands Agents SDK — multi-agent supervisor pattern (per-slot OpenAI-compatible models)
- matrix-nio — Matrix bot client
- ChromaDB — episodic vector memory (local embeddings via Ollama nomic-embed-text)
- networkx + aiosqlite — semantic graph memory
- aiomqtt — MQTT for Frigate events
- httpx — async HTTP (Gira X1 REST, Frigate REST, Open-Meteo)
- aiohttp — HTTPS webhook server (Gira X1 value/service callbacks)
- XKNX / XKNXProject — native KNX/IP transport and optional ETS validation
- pydantic / pydantic-settings — config and validation
- Frigate NVR (Docker) — optional local camera event processing
- Mosquitto MQTT broker (Docker)
- Matrix Synapse (Docker)

## Architecture

```
User → Matrix Bot → Router → Supervisor → Specialist Agents → Tools → HAL → Platforms → Adapters
                                                                         ↓
                                                                    StateStore (SQLite)
```

Three-layer device control:

- **HAL** — multi-platform orchestrator, device lookup, state management
- **Platforms** (`src/hal/platforms/`) — translate native KNX and vendor concepts into HAL's unified device model
- **Adapters** (`src/integrations/`) — raw protocol clients (HTTP, MQTT, WebSocket)

8 platforms: KNX/IP, GiraX1, Frigate, Presence, Mitsubishi, Weather, DoorStation, Calendar

## Project Structure

```
src/
├── agents/        # Strands agents: supervisor, comfort, security, energy, schedule, router, offline
├── tools/         # Thin wrappers over HAL (lights, blinds, alarm, heating, topology, rules, etc.)
├── hal/           # Hardware Abstraction Layer: HAL, Platform, StateStore, DeviceAdapter, RuleEngine
│   └── platforms/ # Platform implementations: knx_ip, gira_x1, frigate, presence, mitsubishi, weather, door_station, calendar
├── integrations/  # Low-level protocol adapters: GiraX1Adapter, FrigateAdapter, WeatherAdapter, etc.
├── core/          # Integration bus, events, presence, guardrails, house awareness, topology, profiles
├── memory/        # Three-store memory: episodic (ChromaDB), semantic (graph), procedural (SQLite)
├── background/    # Proactive monitor (60s), pattern detector (hourly), reflection (nightly), scheduler
├── bot/           # Matrix bot (nio)
├── voice/         # Whisper STT via whisper.cpp
├── cli/           # CLI tools (setup-topology)
├── bootstrapper.py # 5-phase wiring: configure → wire_hal → wire_tools → wire_agents → start
├── main.py        # Entry point: creates Bootstrapper, runs lifecycle
├── config.py      # Pydantic settings (loads config.yaml + .env)
└── __main__.py    # python -m src (dispatches to main or setup-topology)
tests/             # automated unit and integration tests
data/              # ChromaDB store + SQLite DB (runtime, not committed)
```

## Where to Look

- Device control logic → `src/tools/`
- HAL device model and state → `src/hal/models.py`, `src/hal/state.py`
- Platform implementations → `src/hal/platforms/`
- Agent routing and model selection → `src/agents/router.py`, `src/agents/offline.py`
- Safety-critical guardrails → `src/core/guardrails.py`
- System prompts → `src/agents/prompts.py`
- State change stream → `src/core/awareness.py`
- Integration protocols → `src/core/types.py`
- HAL protocols (dependency inversion) → `src/hal/protocols.py`
- App wiring & startup → `src/bootstrapper.py`
- Pydantic config models → `src/config.py`
- Full design rationale → `DESIGN.md`

## Conventions

- All user-facing text in German
- Embodied first-person framing ("Mein Schlafzimmer", not "Das Schlafzimmer")
- Async everywhere (asyncio)
- Ruff for formatting/linting (line-length=100, target py312)
- pytest with asyncio_mode="auto"
- Tools never expose UIDs — room/device names only
- Config via pydantic-settings (config.yaml + .env)
- `from __future__ import annotations` in all source files
- Tool functions return German strings (not exceptions) for LLM consumption
- Module-level `set_context(ctx: ToolContext)` pattern for tool dependency injection
- Platform + Adapter split: Platform maps vendor → HAL types, Adapter is raw protocol client

## Anti-Patterns

- Never auto-arm alarm or auto-open door — always require user confirmation
- Never send camera images to external APIs — all vision stays local
- Never run LLMs locally — API only, regex fallback for offline
- Never let LLM bypass Agent Control guardrails
- Never expose platform-native IDs or KNX group addresses to users or LLM prompts

## Development

```bash
pip install -e ".[dev]"       # install with dev deps
ruff check src/ tests/        # lint
ruff format src/ tests/       # format
pytest                        # test (asyncio auto mode)
docker compose up -d          # Frigate + Mosquitto + Synapse
```

## Model Routing

All models configured per-slot in `config.yaml` under `models:` (router/specialists/reflection). Each slot has `base_url`, `api_key_env`, `model_id`. Factory: `create_model_for_slot(config, slot)`.

- Regex → free, instant, offline (~30 German commands)
- Router tier → classification, simple queries, pattern detection
- Specialists tier → complex commands, safety-critical actions, scheduling
- Reflection tier → nightly reflection (weekly analysis)
