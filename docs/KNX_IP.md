# Native KNX/IP setup

KNX Agent can connect directly to a standard KNX/IP interface or router through
[XKNX](https://xknx.io/). A Gira X1 is not required when the `knx_ip` platform is
selected.

> The native platform is validated with automated protocol-boundary tests but
> has not yet been verified against physical KNX/IP hardware. Keep native KNX
> controls available and begin with non-critical devices.

## Requirements

- A KNX/IP interface or router reachable from the KNX Agent host
- A reviewed `knx_devices.yaml` mapping
- Optionally, an ETS `.knxproj` export for validation
- Optionally, an ETS `.knxkeys` export for KNX/IP Secure or Data Secure

KNX Agent does not commission KNX devices. Existing switches, scenes, and KNX
logic continue to operate independently when the agent is stopped.

## Enable the platform

Copy the example mapping:

```bash
cp knx_devices.yaml.example knx_devices.yaml
```

Replace `gira_x1` with `knx_ip` in `config.yaml`'s `platforms` list. Do not select
both primary control platforms in the same process.

```yaml
platforms:
  - knx_ip
  - presence
  - weather
  - calendar

knx_ip:
  connection_type: automatic
  device_map: knx_devices.yaml
```

Relative mapping, project, and keyfile paths are resolved from the directory
containing `config.yaml`.

## Connection modes

| Mode | Configuration |
|---|---|
| Automatic | `connection_type: automatic`; XKNX discovers an interface/router |
| UDP tunneling | `connection_type: tunneling` plus `gateway_ip` |
| TCP tunneling | `connection_type: tunneling_tcp` plus `gateway_ip` |
| Routing | `connection_type: routing`, optionally `local_ip` |

The default KNXnet/IP port is 3671. `local_ip` is useful on hosts with several
network interfaces. `individual_address` can select a particular tunnel or
client address where supported.

Commands fail immediately while disconnected; they are not retained and replayed
later. A successful write means XKNX processed the outgoing telegram, not that a
physical actuator confirmed execution. Configure status addresses wherever
feedback matters. Tunnel reconnects are handled by XKNX, and failed initial
connections are retried by the platform while the rest of KNX Agent remains
available.

## Authoritative device mapping

`knx_devices.yaml` is the only source that defines logical devices and writable
capabilities. Stable keys become internal HAL device IDs; tools and models see
names and rooms rather than group addresses.

```yaml
schema_version: 1

devices:
  living_ceiling:
    name: Living-room ceiling
    room: Living room
    floor: Ground floor
    type: dimmable_light
    addresses:
      switch: 1/0/1
      switch_state: 1/1/1
      brightness: 1/0/2
      brightness_state: 1/1/2
```

Use the same address for command and state only when that reflects the KNX
installation. Without a configured state role, KNX Agent does not infer state
from its own outgoing command.

### Supported device roles

| Type | Required roles | Optional roles |
|---|---|---|
| `light`, `switch` | `switch` | `switch_state` |
| `dimmable_light` | `switch`, `brightness` | corresponding `_state` roles |
| `blind` | `move`, `stop` | `move_state`, `position`, `position_state` |
| `heating` | `setpoint` | `setpoint_state`, `temperature_state`, switch roles |
| `temperature_sensor` | `temperature_state` | — |
| `binary_sensor` | `value_state` | `sensor_class` metadata |
| `scene` | `scene`, plus `scene_number` from 1–64 | `scene_state` |
| `trigger` | `trigger` | `trigger_state` |

Standard roles have conventional DPT defaults. Explicit `dpts` entries override
those defaults. Binary sensors should normally specify their exact subtype, such
as `1.019` for a window/door status, unless ETS validation supplies it.

## Optional ETS validation

Configure a project export when desired:

```yaml
knx_ip:
  project_file: private-installation.knxproj
  device_map: knx_devices.yaml
```

The ETS project is supplementary. It may:

- verify that mapped group addresses exist;
- verify communication-object links;
- detect DPT conflicts;
- supply a missing DPT when exactly one value is available.

It never creates devices, groups addresses, infers command/status roles, changes
names or rooms, or adds writable capabilities. A contradiction between the map
and ETS fails startup rather than changing behavior silently.

If the project is encrypted, set `KNX_PROJECT_PASSWORD` locally. Do not put the
password in `config.yaml`.

## KNX Secure

Export a `.knxkeys` keyring from ETS and configure:

```yaml
knx_ip:
  connection_type: tunneling
  gateway_ip: 192.0.2.10
  secure: true
  secure_keyfile: secrets/installation.knxkeys
```

Set `KNX_KEYFILE_PASSWORD` in the local environment. Secure tunneling uses
KNX/IP Secure over TCP; secure routing uses KNX/IP Secure routing. The keyring
also provides Data Secure group keys to XKNX.

The keyring, ETS project, authoritative mapping, and environment file are ignored
by Git. Treat all four as sensitive installation material.

## State and safety

- Initial reads target only explicitly configured state addresses.
- Failed reads and undecodable telegrams remain unknown.
- Incoming telegrams update only mapped devices.
- Outgoing telegrams are typed; unsupported or missing DPTs are rejected.
- No arbitrary group-address write tool is exposed.
- Door, lock, and alarm actuation are not part of the initial native mapping.

## Local validation

```bash
uv sync --frozen --extra dev
uv run pytest tests/test_knx_ip_config_loading.py \
  tests/test_knx_ip_registry.py \
  tests/test_knx_ip_ets.py \
  tests/test_knx_ip_adapter.py \
  tests/test_knx_ip_platform.py
```

For first hardware use, start with one light or read-only sensor, verify status
feedback, then expand the authoritative mapping incrementally.
