# agents/

## Purpose
Strands-based multi-agent system with supervisor pattern. Each specialist agent has its own model, system prompt, and tool set.

## Key Files
- `__init__.py` — `create_model_for_slot(config, slot, max_tokens)` factory (returns `OpenAIModel` via per-slot config); `create_specialist_agent(config, system_prompt, tools, model_slot, domain, hooks)` factory helper
- `supervisor.py` — `create_supervisor`: wraps specialists as tools via `_make_agent_tool`; `_AGENT_DESCRIPTIONS` maps agent names to descriptions
- `router.py` — `route` entry point: internet check → regex offline → router tier classification → `RouterResult` with agent list; `_DEFAULT_ROUTING` intent-to-agent map; `_classify_offline_intent`/`_agents_for_intent` helpers
- `offline.py` — Regex fallback for ~30 German commands; `_PATTERNS` dict maps regex → extractor; `try_offline`/`execute_offline` entry points
- `comfort.py` — `create_comfort_agent` — lights, blinds, heating, AC, batch commands via execute_batch (Specialists tier)
- `security.py` — `create_security_agent` — alarm, door, camera events (Specialists tier)
- `energy.py` — `create_energy_agent` — energy monitoring, weather (Router tier — intentionally cheaper model)
- `schedule.py` — `create_schedule_agent` — cron, routines, one-shots (Specialists tier)
- `prompts.py` — All system prompts: `EMBODIED_SYSTEM_PROMPT`, `COMFORT_PROMPT`, `SECURITY_PROMPT`, `ENERGY_PROMPT`, `SCHEDULE_PROMPT`, `ROUTER_PROMPT`, `REFLECTION_PROMPT`, `SUPERVISOR_PROMPT_TEMPLATE`; `build_supervisor_prompt`/`build_embodied_context` assemble dynamic context

## Conventions
- Specialist agents are factory functions `create_*_agent` returning Strands Agent
- Specialists registered as callable tools on the supervisor (not direct agent-to-agent)
- System prompts use embodied framing (agent IS the house)
- Router outputs JSON: `{intent, complexity, agents[], model, safety_critical}`
- Family profiles define trust levels and response styles per user
- Each model slot (router, specialists, reflection) is independently configured via `config.models.<slot>` with `base_url`, `api_key_env`, and `model_id`

## Dependencies
- Depends on: `tools/`, `memory/` (context assembly), `core/` (types, guardrails)
- Depended on by: `bot/` (entry point for user messages), `background/` (reflection uses reflection tier)
