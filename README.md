# KNX Agent

[![CI](https://github.com/mrzhbr/knx-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/mrzhbr/knx-agent/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Local-first, German-language AI home automation for KNX installations. Device
control can connect either through the Gira X1 REST API or directly through a
standard KNX/IP interface/router using XKNX. KNX Agent combines deterministic
offline commands, API-hosted reasoning agents, a hardware abstraction layer,
local memory, Matrix chat, and Frigate presence/security events.

> **Status:** advanced personal project, not a certified alarm or access-control
> system. Test with your hardware and read the security documentation before use.
>
> KNX is a registered trademark of KNX Association. This independent project is
> not affiliated with or endorsed by KNX Association or Gira.

## Highlights

- Vendor-neutral KNX/IP tunneling/routing plus the existing Gira X1 adapter.
- HAL/platform/adapter architecture across the integration domains.
- German text and local Whisper voice input over a private Matrix room.
- 30+ offline command patterns when model APIs are unavailable.
- Local episodic, semantic, and procedural memory.
- Request-local authorization and deterministic physical-action guardrails.
- Scheduled automation that cannot autonomously operate doors or alarms.
- More than 1,100 automated tests plus lint, formatting, typing, security, and
  dependency checks in CI.

## Architecture

```text
Matrix → authorization → router/supervisor → tools/guardrails → HAL
                                                        ├── KNX/IP or Gira X1
                                                        ├── Frigate
                                                        ├── Presence
                                                        ├── Mitsubishi AC
                                                        ├── Weather
                                                        ├── Door station
                                                        └── Calendar
```

See [DESIGN.md](DESIGN.md) for implementation details and
[THREAT_MODEL.md](THREAT_MODEL.md) for security boundaries.

### Architecture paper

The seminar paper [*Design Considerations for Integrating Agentic AI with the
KNX Building Automation Standard*](paper.pdf) explains the architectural
rationale: KNX retains independent actuation authority, while agent proposals
pass through a code-enforced policy boundary and safety-critical action classes
remain non-autonomous. It is a design rationale rather than a setup guide; its
longitudinal authority-graduation model also describes future design direction,
not only completed features.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- A standard KNX/IP interface/router or a Gira X1 for device control
- Optional: Docker, Matrix Synapse, Mosquitto, Frigate, Ollama embeddings, and
  whisper.cpp

## Quick start

```bash
git clone https://github.com/mrzhbr/knx-agent.git
cd knx-agent
uv sync --frozen --extra dev
cp config.yaml.example config.yaml
cp house_topology.yaml.example house_topology.yaml
cp frigate.yml.example frigate.yml
# Native KNX/IP only:
cp knx_devices.yaml.example knx_devices.yaml
```

Create a local `.env` from `.env.example` and set credentials yourself. Do not
commit it. At minimum, configure all three model slots, one private Matrix room,
and at least one admin profile in `config.yaml`.

Run validation:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src --ignore-missing-imports --no-strict-optional
uv run pytest
```

Start the application:

```bash
uv run python -m src
```

### Supporting services

The default Compose file binds published ports to loopback, requires MQTT
authentication, and uses pinned image versions. Generate the Mosquitto password
file locally before starting services:

```bash
mkdir -p secrets
# Run interactively; do not put the password in shell history or this repository.
docker run --rm -it -v "$PWD/secrets:/out" eclipse-mosquitto:2.0.22 \
  mosquitto_passwd -c /out/mosquitto_password_file knx-agent
docker compose up -d
```

Any LAN-facing override must be paired with firewall rules, MQTT TLS/topic ACLs,
and the controls in [docs/REMOTE_ACCESS.md](docs/REMOTE_ACCESS.md).

## Configuration and privacy

The following files are intentionally ignored because they contain deployment or
household details:

- `.env`
- `config.yaml`
- `frigate.yml`
- `house_topology.yaml`
- `data/`
- `secrets/`

Public `*.example` files contain safe placeholders. Camera media and embeddings
remain local. Training-data collection is disabled by default.

## Safety guarantees

- Unknown Matrix rooms and users are denied.
- Door opening requires a known admin and a short-lived explicit confirmation.
- Alarm actions cannot execute from scheduler provenance.
- Alarm arming fails closed when presence is unknown or a window is open.
- Limited profiles can use lights and blinds only.
- Models cannot grant themselves confirmation or change request provenance.

These controls reduce risk but do not turn this project into certified safety
equipment. Keep mechanical keys and vendor-native controls available.

## Documentation

- [Design](DESIGN.md)
- [Threat model](THREAT_MODEL.md)
- [Security policy](SECURITY.md)
- [Native KNX/IP setup](docs/KNX_IP.md)
- [OwnTracks setup](docs/OWNTRACKS.md)
- [Secure remote access](docs/REMOTE_ACCESS.md)
- [Contributing](CONTRIBUTING.md)
- [Public release checklist](OPEN_SOURCE_RELEASE.md)

## License

MIT — see [LICENSE](LICENSE).
