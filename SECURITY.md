# Security Policy

## Supported versions

Security fixes are provided for the latest commit on `main`. This project has
not yet declared a stable 1.x release.

## Reporting a vulnerability

Do not open a public issue for vulnerabilities involving credentials, household
presence, cameras, alarms, locks, doors, callbacks, or remote code execution.
Use GitHub's **Private vulnerability reporting** feature for this repository.
If that feature is unavailable, contact a maintainer privately through the
address listed on their GitHub profile.

Include affected versions, impact, reproduction steps, and suggested mitigations
when possible. Do not access devices, accounts, networks, or household data that
you do not own or have explicit permission to test.

## Deployment assumptions

KNX Agent must run on a trusted host behind a firewall. Supporting services are
loopback-only by default. Remote access must use a private VPN; public port
forwarding is unsupported. MQTT authentication is mandatory, and TLS is required
outside loopback. Legacy plain-HTTP devices require IoT VLAN isolation and strict
host firewall rules.

Real configuration, topology, camera streams, runtime databases, and credentials
must never be committed. Review [THREAT_MODEL.md](THREAT_MODEL.md) and
[docs/REMOTE_ACCESS.md](docs/REMOTE_ACCESS.md) before deployment.

## Known scoped advisory

`PYSEC-2026-311` affects ChromaDB's networked Python FastAPI server. KNX Agent
uses only the in-process `PersistentClient` and never starts or publishes that
server. CI carries a narrowly scoped audit exception while upstream has no
confirmed fixed release. Do not add a Chroma server deployment without removing
the exception and completing a new security review.

## Release security gates

A release should not be published unless lint, formatting, typing, tests,
coverage, secret scanning, and dependency auditing pass. Critical or high
findings require remediation or a documented maintainer decision.
