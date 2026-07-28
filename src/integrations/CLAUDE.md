# integrations/

## Purpose

Low-level protocol adapters for external systems. These are internal implementation details used by HAL platform classes — not public APIs. External code should interact with `src/hal/` (HAL and Platform classes), not with integration adapters directly.

## Key Files

- `gira_x1.py` — `GiraX1Adapter`: REST client for Gira X1; `get_value`/`set_value`/`set_values`; `register_callbacks`/`unregister_callbacks` for webhook registration; `get_uiconfig` for device discovery
- `knx_ip.py` — `KNXIPAdapter`: XKNX lifecycle, connection configuration, typed group reads/writes, and filtered telegram callbacks
- `knx_project.py` — synchronous XKNXProject parser used only for optional mapping validation
- `frigate.py` — `FrigateAdapter`: MQTT for Frigate detection events; REST for `get_snapshot`/`get_events`
- `arp_presence.py` — `ARPPresenceAdapter`: ARP table polling for phone MAC tracking (deprecated, use owntracks.py)
- `owntracks.py` — `OwnTracksAdapter`: MQTT subscription for OwnTracks geofence events; transition events (enter/leave) as primary signal; location-based fallback with haversine distance
- `door_station.py` — `DoorStationAdapter`: bell events via Gira callback; `trigger_door_relay` for door opening
- `weather.py` — `WeatherAdapter`: Open-Meteo API; `get_current`/`get_summary`; `WMO_CODES` weather code mapping
- `calendar.py` — `CalDAVAdapter`: CalDAV client for upcoming events; `_CACHE_TTL_SECONDS` TTL caching; `format_for_prompt`
- `mitsubishi_ac.py` — `MitsubishiACAdapter`: MELCloud API for Mitsubishi AC units; state polling with `_detect_changes`

## Conventions

- All adapters follow `__init__` → `start` → `stop` lifecycle (async)
- Adapters are owned by Platform classes in `src/hal/platforms/`
- Adapters are pure protocol translation — no HAL types, no device model knowledge
- HTTP clients use httpx async
- MQTT uses aiomqtt

## Dependencies

- Depends on: `core/` (bus, types), aiomqtt, httpx
- Depended on by: `hal/platforms/` (Platform classes own their adapter)
