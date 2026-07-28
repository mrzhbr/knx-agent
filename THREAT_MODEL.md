# Threat Model

## Assets

- Physical control of lights, blinds, HVAC, alarms, and door relays
- Household presence, room topology, schedules, and conversations
- Camera events and media
- Device, KNX/IP Secure, Matrix, MQTT, calendar, and model-provider credentials
- Local memory and training data

## Trust boundaries

1. Matrix events cross from users into the application.
2. Model output crosses into tool selection but is never trusted for authorization.
3. Schedules and learned routines cross into autonomous execution.
4. MQTT and device callbacks cross from the IoT network into state and presence.
5. Native KNX telegrams and KNX/IP discovery cross from the automation network.
6. Vendor APIs and model providers cross the local-network/privacy boundary.

## Primary threats and controls

| Threat | Control |
|---|---|
| Unknown Matrix member controls the house | Exact room and principal allowlists; unknown users fail closed |
| Limited user invokes privileged tool | Request-local profile and deterministic tool rules |
| Model fabricates confirmation | Confirmation grants are created and consumed at the Matrix boundary |
| Scheduler opens a door or changes alarm | Automation provenance plus direct security-tool deny rules |
| Concurrent requests exchange identity | Immutable `ContextVar` request state and isolated call counters |
| Spoofed MQTT presence | Authenticated broker, tracked-topic allowlist, TLS off-host |
| Forged webhook changes state | Random callback tokens and host firewall/source restrictions |
| Device command silently fails | Disconnected transports and API errors are surfaced; configured status addresses provide authoritative feedback |
| Arbitrary KNX address becomes writable | Only explicitly mapped named HAL devices are exposed; no raw group-write tool |
| ETS import changes device semantics | Device map remains authoritative; ETS only validates addresses, links, and DPTs |
| Forged or malformed KNX telegram changes state | Configured-address filtering, typed decoding, unknown-on-failure semantics, isolated automation network |
| Credentials leak in Git/logs | Ignored local files, secret scanning, metadata-only request logs |
| Publicly exposed local services | Loopback Compose bindings and private VPN guidance |

## Residual risks

- Legacy KNX without KNX Secure has no semantic bus-level authorization; any
  connected writer may be able to actuate mapped group addresses.
- Native KNX/IP interoperability has automated coverage but still requires
  validation against representative physical gateways.
- A drained KNX/IP send queue confirms transport processing, not physical
  actuator execution. Command/status mappings are required where feedback matters.
- Some legacy devices support only plain HTTP or self-signed certificates.
- The Gira API requires tokens in request URLs. Exceptions and access logs must
  therefore be treated as sensitive and redacted.
- Matrix end-to-end encryption is not established by this client. Use a private,
  TLS-protected homeserver/room on a trusted network.
- Presence sensors can be stale or unavailable. Security decisions fail closed,
  which can reduce availability.
- This software is not certified alarm, access-control, or life-safety equipment.

## Out of scope

- Protecting a host that is already fully compromised
- Safety certification
- Public Internet exposure of device protocols
- Multi-tenant cloud isolation
