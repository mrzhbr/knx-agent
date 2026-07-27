# core/

## Purpose
Shared infrastructure: integration bus (lifecycle only), presence tracking, safety guardrails, conversation management, user profiles, house awareness (query layer over StateStore), and topology.

## Key Files
- `bus.py` — `IntegrationBus`: register/unregister/get integrations by name; start_all/stop_all lifecycle
- `types.py` — Protocol definitions: `Integration` (start/stop/health_check)
- `events.py` — Event constants (STATE_CHANGE, PRESENCE, etc.); dataclasses `Event`, `DetectionEvent`
- `awareness.py` — `HouseAwareness`: async query layer over StateStore; `format_recent`/`get_contradictions`/`get_recent_events` for prompt context. `EnergyAwareness`: `POWER_ESTIMATES_W` for per-device power; `estimated_watts`/`update_real_power`/`format_for_prompt`
- `guardrails.py` — `GuardrailProvider` (Strands HookProvider): `_on_before_tool_call`/`_on_before_invocation` hooks; `GuardrailRule`/`GuardrailContext` dataclasses; `set_request_user_profile` for per-request context
- `presence.py` — `PresenceManager`: `nobody_home`/`open_windows`/`has_open_windows`; camera zone tracking (`update_camera_zone`/`get_entrance_activity`); `format_for_prompt`
- `conversation.py` — `ConversationWindow`: sliding window of recent turns; `add`/`get_context`/`clear`
- `profiles.py` — `FamilyProfile` dataclass; `FAMILY_PROFILES` from config; `resolve_user` maps Matrix ID → profile; `load_profiles_from_config`/`format_profiles_for_prompt`
- `topology.py` — `HouseTopology`: room spatial graph (adjacency, open-to, floor, zone, solar exposure)

## Conventions
- `Integration` protocol: `start()`, `stop()`, `health_check()` — all async
- IntegrationBus is lifecycle-only; state flows through HAL's StateStore
- `HouseAwareness` is a query layer over HAL's StateStore, not an event emitter
- Guardrails hook into Strands lifecycle (BeforeToolCallEvent, BeforeInvocationEvent)

## Dependencies
- Depends on: nothing (foundational layer)
- Depended on by: everything (`agents/`, `tools/`, `integrations/`, `background/`, `bot/`, `hal/`)
