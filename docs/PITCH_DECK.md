# KNX Agent

### The house that understands you

---

## The Problem

Smart homes today aren't smart. They're remote controls with extra steps.

- **Hundreds of devices, zero intelligence.** Residents manage scenes, schedules, and rules manually — or give up and use wall switches.
- **Every automation is hand-coded.** A new routine means a new rule. A family member with different preferences means another rule. It doesn't scale.
- **No memory, no context.** The system doesn't know who's asking, what happened five minutes ago, or that it's a cloudy Tuesday and the blinds should stay down.
- **Fragmented interfaces.** Lights in one app, cameras in another, heating in a third. Nobody opens four apps to go to bed.

System integrators spend weeks programming automations that clients forget how to use — and call back to change.

---

## The Solution

**KNX Agent** is an AI layer that sits on top of an existing KNX installation—through standard KNX/IP or a Gira X1—and turns it into a conversational, context-aware home.

Residents talk to their house in natural language — via text or voice message — and the house responds, acts, and learns.

> *"Mach das Wohnzimmer gemütlich."*
> The system knows it's 21:00, dims the lights to 40%, closes the blinds, and sets heating to 21°C — because it learned that's what this resident means by "gemütlich."

No app. No scene programming. No manual rules.

---

## What It Does

### Conversational Control

Residents send a message (text or voice) in German. The system understands intent, resolves devices by room and name, and executes — across lights, blinds, heating, AC, alarm, and door access.

### Learns Preferences

The system remembers that Person 1 likes the bedroom at 19°C and that Person 2 prefers blinds open in the morning. Preferences build up over time through natural conversation — no configuration screens.

### Proactive Awareness

The house monitors itself continuously:

- Heating running with a window open? It notifies — and offers to act.
- Lights on for two hours with nobody home? It flags it.
- Energy consumption spiking? It reports.

It **never acts without asking** on safety-critical operations.

### Family-Aware

Each family member has their own profile with trust levels and permissions. Children can control lights and blinds — but not the alarm or the front door. The system adapts its language and response style per person.

### Presence-Aware

The system knows who's home (network presence detection) and what's happening at the entrance (camera-based detection). It uses this context to make better decisions — and to answer "Ist jemand zu Hause?"

### Security with Guardrails

Alarm and door operations are protected by multiple safety checks:

- Can't arm the alarm while someone is still home
- Can't arm with windows open
- Door access always requires explicit confirmation
- The AI cannot override these rules — they're enforced at the system level, not in the prompt

### Spatial Intelligence

The system understands the physical layout: which rooms are adjacent, which floor they're on, and how the sun hits each room throughout the day. This enables decisions like adjusting blinds based on actual solar exposure.

### Schedules and Routines

Residents create schedules in natural language ("Mach morgen um 7 die Heizung an") and the system handles the rest. Over time, it detects recurring patterns and suggests routines — but never activates them without approval.

---

## How It Fits Into Existing Installations

```
┌─────────────────────────────────────────────┐
│              Resident (Matrix App)           │
│           text / voice message               │
└──────────────────┬──────────────────────────┘
                   │
          ┌────────▼────────┐
          │   KNX Agent    │
          │   (AI Layer)    │
          └───┬───┬───┬─────┘
              │   │   │
     ┌────────┘   │   └────────┐
     ▼            ▼            ▼
┌─────────┐ ┌─────────┐ ┌──────────┐
│ KNX/IP  │ │ Frigate  │ │ Other    │
│ or X1   │ │ (NVR)   │ │ Systems  │
└─────────┘ └─────────┘ └──────────┘
```

- **Non-invasive.** KNX Agent connects through a standard KNX/IP interface/router or the Gira X1 REST API. No rewiring, firmware changes, or KNX reprogramming is required.
- **Additive.** All existing automations, switches, and apps continue to work. KNX Agent is an additional interface, not a replacement.
- **Local-first.** The AI brain runs on a compact local server (mini PC or equivalent). Camera feeds, presence data, and device state never leave the premises. Only LLM inference calls go to the cloud — no images, no recordings.
- **Works with what's there.** Integrates with Frigate NVR for cameras, Mitsubishi AC units, CalDAV calendars, weather services, and standard MQTT devices.

---

## Why This Is Different

| Traditional Smart Home | KNX Agent |
|------------------------|------------|
| User programs rules and scenes | User says what they want |
| Static schedules | Learns and suggests routines |
| One interface per subsystem | One conversation for everything |
| No memory between interactions | Remembers preferences and context |
| Same response for everyone | Adapts to each family member |
| Reactive only | Proactively monitors and notifies |
| Manual troubleshooting | Self-aware — detects contradictions |

---

## Privacy and Safety by Design

- **Camera images never leave the local network.** All vision processing happens on-premise.
- **No cloud dependency for basic operation.** Offline regex handles ~30 common commands without internet.
- **Safety-critical actions are hardware-guarded.** The AI cannot bypass alarm or door guardrails — these are enforced in code, not by AI judgment.
- **Per-user permissions.** Configurable trust levels determine who can control what.
- **Full audit trail.** Every interaction, decision, and state change is logged locally.

---

## Deployment Model

| Component | Requirement |
|-----------|-------------|
| **KNX connection** | Standard KNX/IP interface/router or Gira X1 REST access |
| **Local Server** | Mini PC (e.g. AMD Ryzen, Intel NUC, or Apple Silicon), 16GB+ RAM |
| **Camera System** | Frigate NVR (optional, for presence + security) |
| **Chat Interface** | Matrix/Synapse (self-hosted, included in deployment) |
| **Internet** | Required for AI inference; basic control works offline |

**Deployment complexity:** One server, Docker Compose for supporting services, configuration via YAML. No custom hardware, no proprietary protocols.

---

## For the Integrator

### What changes for you

- **Less time programming scenes and rules.** The AI layer handles preference management and routine creation through conversation.
- **Fewer callback visits.** Residents adapt the system themselves by talking to it — no reprogramming needed for "can you make the lights a bit warmer in the evening."
- **Higher perceived value.** A home that responds to natural language is a fundamentally different product than one with an app full of sliders.

### What stays the same

- KNX programming and device commissioning remain unchanged
- ETS and optional Gira X1 project configuration remain untouched
- All physical controls, wall switches, and existing apps continue to work
- You retain full control over the electrical installation

### New revenue opportunity

- AI layer deployment and configuration as a premium service
- Ongoing support and customization contracts
- Differentiation from competitors offering standard Gira installations

---

## Current Capabilities at a Glance

- Multi-room device control through native KNX/IP or Gira X1
- Integrations for cameras, AC, weather, calendars, presence, and MQTT
- Natural language control in German (text + voice)
- Three-layer memory (learns preferences, detects patterns, tracks state)
- Four-tier intelligence (offline → fast AI → specialist AI → full orchestration)
- Proactive monitoring with smart notifications
- Family profiles with per-person permissions
- Spatial awareness (room topology, solar exposure)
- More than 1,100 automated tests

---

## Next Steps

**Interested in a live demo or pilot installation?**

KNX Agent supports native KNX/IP and Gira X1 deployments. Native KNX/IP is currently awaiting broader physical-gateway validation.

Let's talk.
