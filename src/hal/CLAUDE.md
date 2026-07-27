# hal/

## Purpose
Hardware Abstraction Layer — the central device control and state management system. All device interactions go through HAL. Platforms translate vendor protocols into HAL's unified device model.

## Key Files
- `hal.py` — `HAL`: add_platform, find_device/find_room/has_room, execute(device_id, command), populate_initial_state, device_count; `rules` attribute (RuleEngine); duck-typed `set_state_store()` injection for platforms that support it
- `state.py` — `StateStore`: persistent SQLite state cache; on_change listeners; event_history; TTL-based brain-action marking (`_brain_action_marks` dict with 2s window); in-memory `_states` cache with sync `get_state_sync()` for webhook hot path
- `models.py` — `DeviceId`, `DeviceInfo`, `DeviceState`, `DeviceType` (13 types), `SensorClass` (7 classes)
- `platform.py` — `Platform` ABC: start/stop, health_check, discover_devices, read_state, subscribe_events/unsubscribe_events, send_command (optional, default raises NotImplementedError for read-only platforms)
- `devices.py` — `DeviceAdapter` subclasses: `LightAdapter`, `ColoredLightAdapter`, `BlindAdapter`, `ClimateAdapter`, `SensorAdapter`, `CameraAdapter`, `PresenceSensorAdapter`, `WeatherSensorAdapter`, `DoorStationDeviceAdapter`
- `rules.py` — `RuleEngine`: 8 built-in rules (lights_nobody_home, blinds_late, etc.); custom rules from ReflectionAgent; set_topology/set_weather_source/set_energy injectors
- `protocols.py` — `ConfigSource`, `EnergySource`, `PresenceQuery`, `TopologyQuery`, `WeatherSource` — dependency-inversion protocols so HAL never imports from `src/core`
- `platforms/` — Platform implementations:
  - `gira_x1.py` — `GiraX1Platform`: owns GiraX1Adapter + `_device_registry` (private implementation detail, not a public interface); HTTPS webhook server (aiohttp) for value/service callbacks; 5-min polling fallback; table-driven command dispatcher; `set_state_store()` for StateStore injection
  - `frigate.py` — `FrigatePlatform`: owns FrigateAdapter; exposes get_events()
  - `presence.py` — `PresencePlatform`: owns ARPPresenceAdapter
  - `mitsubishi.py` — `MitsubishiPlatform`: owns MitsubishiACAdapter
  - `weather.py` — `WeatherPlatform`: owns WeatherAdapter; exposes get_summary()/get_current()
  - `door_station.py` — `DoorStationPlatform`: owns DoorStationAdapter
  - `calendar.py` — `CalendarPlatform`: owns CalDAVAdapter; exposes format_for_prompt()

## Conventions
- `Platform.start()` / `Platform.stop()` lifecycle is called by HAL
- `Platform.health_check()` returns bool
- All tool functions use `_ctx.hal` from ToolContext — never import platforms directly
- Platform classes are internal to HAL; external code interacts via `HAL` methods
- StateStore is shared across all platforms via HAL constructor
- Brain-action marking uses TTL (2s window) not discard-on-first-use — handles multi-UID commands like set_brightness

## Dependencies
- Depends on: `core/` (bus, presence, topology), `integrations/` (adapters)
- Depended on by: `tools/` (via ToolContext.hal), `background/` (monitor), `bootstrapper.py`
