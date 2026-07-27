# memory/

## Purpose
Three-store memory architecture for different retrieval patterns. All local, no external APIs for storage or embeddings.

## Key Files
- `layer.py` — `MemoryLayer`: facade over all three stores; `build_context` assembles memory for prompts (similar events, preferences, corrections, routines); `log_interaction` saves to all stores; `_fetch_similar`/`_fetch_preferences`/`_fetch_routines`/`_fetch_corrections` internal helpers
- `episodic.py` — `EpisodicMemory`: ChromaDB vector store; embeddings via local Ollama nomic-embed-text; `add_event`/`search`/`add_correction`/`search_corrections`/`search_recent`; `format_similar`/`format_recent_corrections` for prompt context
- `semantic.py` — `SemanticMemory`: networkx graph + aiosqlite persistence; `add_edge`/`strengthen_edge` for relationships; `get_preferences`/`get_related`/`get_edges_by_predicate` for querying; `decay_edges` for temporal decay; `format_preferences` for prompt context
- `procedural.py` — `ProceduralMemory`: aiosqlite store; `add_preference`/`get_preferences`/`search_preferences`; `set_device_knowledge`/`get_device_knowledge`/`get_unknown_devices`/`seed_from_devices`; `store_candidate_routine`/`update_routine`/`get_routines`; `add_schedule`/`get_active_schedules`/`cancel_schedule`/`mark_schedule_run`/`delete_expired_one_shots`; `log_routine_execution`/`get_routine_executions`; `add_scene`/`get_scene_by_name`/`get_all_scenes`/`update_scene`/`delete_scene`

## Conventions
- All stores have `open`/`close` async lifecycle
- Embeddings are local only (Ollama), never sent to external APIs
- Semantic edges have weight/decay — strengthened by repetition, weakened by time
- Procedural stores both user-defined and auto-detected routines with confidence scores
- All DB operations via aiosqlite

## Dependencies
- Depends on: Ollama (local embeddings), ChromaDB, aiosqlite, networkx
- Depended on by: `agents/` (context assembly), `tools/memory.py`, `background/` (patterns, reflection)
