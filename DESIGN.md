# KNX Agent — Design

## Goals

KNX Agent is a local-first, embodied home-automation agent for Gira X1. It
provides German natural-language control while keeping device state, camera
media, embeddings, and household memory on the home server.

The project optimizes for:

- deterministic authorization around physical actions;
- graceful operation when model providers or integrations are unavailable;
- vendor isolation through a hardware abstraction layer;
- local storage and data minimization;
- explicit user approval before learned automation becomes active.

## Architecture

```text
Matrix user
    │
    ▼
Fail-closed room + principal authorization
    │
    ▼
Offline router / model router
    │
    ▼
Supervisor ── specialist agents
    │
    ▼
Tool functions ── deterministic guardrails
    │
    ▼
HAL ── platforms ── protocol adapters ── local devices/services
    │
    ├── StateStore (SQLite)
    ├── RuleEngine
    └── awareness stream
```

### Layer responsibilities

- **Bot:** authenticated Matrix transport, voice download, and response delivery.
- **Agents:** intent classification, contextual reasoning, and tool selection.
- **Tools:** narrow German-language interfaces over the HAL.
- **Guardrails:** request-local provenance, role restrictions, confirmation
  grants, automation restrictions, and tool-call limits.
- **HAL:** unified device IDs, discovery, state, execution, and rules.
- **Platforms:** translate vendor concepts to HAL models.
- **Adapters:** own raw HTTP, MQTT, callback, and device protocols.
- **Memory:** episodic vector memory, semantic graph memory, and procedural
  routines/schedules.

## Security boundaries

The model is not an authorization boundary. Matrix events are accepted only when
both room and sender are configured. Unknown principals fail closed.

Security-sensitive actions are subject to deterministic policy:

| Action | Policy |
|---|---|
| Open a door | Known admin plus short-lived, single-use confirmation |
| Arm alarm | Known admin, user-originated request, nobody home, windows closed |
| Disarm alarm | Known admin plus explicit confirmation |
| Scheduled door/alarm action | Always denied |
| Limited profile | Lights and blinds only |

Request identity, provenance, confirmation grants, and call counters are stored
in task-local context. The scheduler binds automation provenance before invoking
an agent or tool. Security tools repeat critical checks so direct dispatch cannot
bypass a model hook.

See [THREAT_MODEL.md](THREAT_MODEL.md) for trust assumptions and residual risks.

## Routing

1. Deterministic German regular expressions handle common commands offline.
2. A low-cost router model classifies remaining requests.
3. Single-domain requests go directly to a specialist.
4. Multi-domain or ambiguous requests use the supervisor.
5. Context-overflow handling retries with progressively smaller context.

No local language model is required. Local Ollama is used only for embeddings.

## State and events

Platforms discover devices and publish `DeviceState` updates into `StateStore`.
StateStore persists the latest values in SQLite and notifies listeners. The HAL
marks brain-originated actions before protocol callbacks arrive, preserving the
origin of state changes.

Webhook paths use deployment-specific random tokens where supported. Polling is
the secure fallback when callbacks are not configured.

## Memory

- **Episodic:** ChromaDB with local embeddings for past events/interactions.
- **Semantic:** SQLite plus NetworkX for relationships and preferences.
- **Procedural:** SQLite routines, schedules, approvals, and execution history.

Runtime memory is ignored by Git. Training-data collection is disabled by
default and must remain local because it can contain household information.

## Scheduling and autonomy

Schedules are persisted in procedural memory and claimed before execution.
Every scheduled dispatch runs under automation provenance. Direct door and alarm
commands are rejected, and security-related routines are capped at suggestion
level. Learned routines require explicit approval before autonomous execution.

## Resilience

- Integration lifecycle start/stop is coordinated by a central bus.
- Background tasks are monitored and cancelled during shutdown.
- Platform health is propagated into prompts and user-facing tool responses.
- Device APIs use bounded timeouts and reconnection loops.
- Offline routing remains available during model-provider outages.

## Privacy

Camera images remain local. Public repository files contain only examples;
actual topology, camera configuration, credentials, hostnames, addresses, and
runtime databases are ignored. Operational logs avoid message and transcription
bodies at INFO level.

## Deployment

The default Compose stack binds published ports to loopback, requires MQTT
authentication, uses pinned image versions, and avoids privileged containers.
Remote access should use a private mesh VPN and an isolated IoT network.

See [README.md](README.md), [SECURITY.md](SECURITY.md), and
[docs/REMOTE_ACCESS.md](docs/REMOTE_ACCESS.md).
