# OwnTracks Presence — Setup Guide

KNX Agent uses [OwnTracks](https://owntracks.org/) as its primary presence backend. OwnTracks is a free, open-source iOS/Android app that publishes geofence transition events to an MQTT broker the instant a phone enters or leaves a region. This is dramatically more reliable than the legacy ARP-scanning backend, which loses iPhones to WiFi power-save mode and produces frequent false "away" events.

## How it works

```
[OwnTracks app on phone] ──MQTT──▶ [Mosquitto broker] ──subscribe──▶ [KNX Agent OwnTracksAdapter]
                                                                             │
                                                                             ▼
                                                                     [PresencePlatform]
                                                                             │
                                                                             ▼
                                                              [HAL PRESENCE_SENSOR devices]
```

- **Primary signal**: `transition` events (`enter`/`leave`) on the home waypoint. These fire the moment the phone crosses the geofence boundary — typically within a few seconds — and are definitive.
- **Fallback signal**: raw `location` updates run through a haversine distance check against your configured home coordinates. Used as a safety net when the phone's OwnTracks app is temporarily unable to report a transition (e.g. app restart).
- **Safety net**: a periodic stale-check task marks a device as "away" if no MQTT message has been received within `owntracks_stale_timeout_minutes`. Runs independently of broker traffic — a fully silent broker still triggers the timeout.
- **Hysteresis**: once a device is marked home (via a transition event), location updates alone cannot flip it back to "away" unless the phone is clearly outside the geofence (`radius * 1.5`). This prevents GPS jitter from oscillating the state.

## Prerequisites

- A running MQTT broker reachable from the phone's network. KNX Agent's `docker-compose` ships Mosquitto on `localhost:1883` — use that if you're running the brain on the same LAN the phones connect to.
- Network connectivity from the phone to the broker. For away-from-home updates, use an authenticated TLS endpoint reachable only through a private VPN. Never expose plaintext MQTT directly to the public Internet.
- Your home coordinates configured in `config.yaml` under `home.latitude` / `home.longitude`.

## Setting up OwnTracks on the phone

1. **Install the app**: [iOS](https://apps.apple.com/app/owntracks/id692424691) / [Android](https://play.google.com/store/apps/details?id=org.owntracks.android).
2. **Open the app → Settings → Mode**: select **MQTT**.
3. **Connection**:
   - **Host**: your broker's hostname or IP (e.g. `mqtt.example.com` or the brain's Tailscale address)
   - **Port**: `1883` (or `8883` for TLS)
   - **Client ID**: leave the default
   - **Username / Password**: if your broker requires auth
   - **TLS**: enable for port 8883
4. **Identification**:
   - **Username**: this becomes the first half of the MQTT topic — e.g. `person1`
   - **Device ID**: this becomes the second half — e.g. `phone`
   - The resulting topic prefix for this device will be `owntracks/person1/phone`
5. **Reporting**:
   - **Mode**: *Significant changes* or *Move* (both work; *Move* uses more battery but is more responsive)
   - **Remote commands / Remote config**: optional
6. **Waypoints** (the critical step):
   - Go to **Settings → Waypoints → +**
   - **Description**: `Home` — **this exact string must match** `presence.owntracks_home_region` in `config.yaml` (default `Home`).
   - **Latitude / Longitude**: your home's coordinates. Easiest method: stand in your living room, tap **Use current location**.
   - **Radius**: `100`–`200` metres. Smaller is more precise but riskier near the boundary; 150 m is a sensible default.
   - Save. The app will now publish `transition` events whenever you cross this boundary.

## Configuring KNX Agent

Edit `config.yaml`:

```yaml
home:
  latitude: 52.5200    # example only; replace locally and never commit it
  longitude: 13.4050

mqtt:
  host: "localhost" # or your private broker address
  port: 1883
  username: "knx-agent"
  # password comes from MQTT_PASSWORD
  tls: false          # set true for every non-loopback connection

presence:
  backend: "owntracks"

  owntracks_tracked_users:
    # Map "OwnTracks-username/OwnTracks-deviceId" → stable internal id
    "person1/phone": "person1_phone"
    "person2/phone": "person2_phone"

  owntracks_topic_prefix: "owntracks"     # default, matches the app's default
  owntracks_home_region: "Home"           # must match the waypoint description
  owntracks_geofence_radius_m: 150        # must match the waypoint radius
  owntracks_stale_timeout_minutes: 30     # away if no update within 30 min
```

### Config rules worth knowing

- **`mqtt` and `home` must be set correctly** when `backend: "owntracks"`. The default home coordinates are `0.0 / 0.0`; KNX Agent refuses to start the OwnTracks backend until they are replaced. This prevents an unconfigured geofence from incorrectly reporting everyone away.
- **`owntracks_tracked_users` must be non-empty** under the owntracks backend; an empty mapping is also a fatal error (silent no-op presence is worse than crashing).
- **`owntracks_tracked_users` keys must match the MQTT topic tail exactly.** If the OwnTracks app publishes to `owntracks/person1/phone`, the key must be `person1/phone`. Messages from unmapped devices are silently ignored.
- **The internal id (value)** is whatever you want — it becomes the `DeviceId.native_id` and is what tools and the LLM see. Use something generic like `person1_phone`.
- **`backend`** is a strict literal: only `"owntracks"` and `"arp"` are accepted. Typos (e.g. `"owntrack"`) are rejected at config-load time by Pydantic.
- **`tracked_devices`** (the legacy ARP list) is **optional** under the owntracks backend. If you populate it with an entry whose key matches an internal id from `owntracks_tracked_users`, the `name` field is used as the device's display name. Otherwise the internal id is reused as the name.

## Verification

1. Restart KNX Agent: `python -m src`
2. Watch the log for:

   ```
   PresencePlatform started (backend=owntracks): tracking N devices
   OwnTracks MQTT connected to <host>:1883
   ```

3. On your phone, open OwnTracks → tap the cloud upload icon to force a publish.
4. You should see in the log:

   ```
   OwnTracks: person1_phone ist jetzt Zuhause
   ```

5. Walk out of the geofence. Within seconds of crossing the boundary OwnTracks will publish a `leave` transition and the log should show:

   ```
   OwnTracks: person1_phone ist jetzt Abwesend
   ```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Startup error requiring non-placeholder home coordinates | `home:` section missing or coordinates still `0.0 / 0.0` | Set real `latitude` / `longitude` under `home:` |
| Every device reported as "away" immediately after startup even though phones are publishing | MQTT broker unreachable (`mqtt.host`/`mqtt.port` wrong) or phone cannot reach the broker | Check the adapter logs for `OwnTracks MQTT connected to ...`; verify broker reachability from the phone's network |
| Brain crashes with `ValueError: OwnTracks backend requires owntracks_tracked_users` | You set `backend: "owntracks"` but the mapping is empty | Add at least one `"user/device": "internal_id"` entry |
| Brain crashes with `Unsupported presence backend: 'owntrack'` | Typo in the `backend` field | Use exactly `owntracks` or `arp` |
| Device never marked home even when physically at home | Waypoint description on the phone does not match `owntracks_home_region`, **or** topic key does not match `user/device` | Compare logs: enable `DEBUG` on `src.integrations.owntracks` and watch raw topics |
| Device flips home/away repeatedly while stationary | Geofence radius too small for GPS accuracy | Increase `owntracks_geofence_radius_m` to 200–300 m |
| All devices marked away after broker restart | MQTT reconnect takes ~5 s; stale timeout fires after `owntracks_stale_timeout_minutes` if broker stays down | Raise the stale timeout or fix the broker |
| Phone never publishes anything | OwnTracks app is in background and iOS has killed the MQTT connection | Enable *Significant changes* mode and keep the app "allowed in background" in iOS settings |

## Why not keep ARP?

ARP scanning polls the local router's ARP table for known MACs. Two hard problems:

1. **iPhones drop from ARP when the screen is off.** iOS aggressively parks the WiFi radio to save battery. Your phone can be sitting on your nightstand and show up as "away" for 5–10 minutes at a time.
2. **It only works at home.** ARP tells you "is this MAC currently on the LAN" — it has no way to tell the difference between "left the house" and "WiFi briefly dropped".

OwnTracks sidesteps both: the app owns the state transition and publishes the exact moment it happens, from anywhere with cellular or WiFi connectivity. The `ARPPresenceAdapter` is still available via `backend: "arp"` as a fallback but logs a deprecation warning at startup.

## Reference

- Source: `src/integrations/owntracks.py`
- Platform wrapper: `src/hal/platforms/presence.py`
- Config model: `src/config.py` (`PresenceConfig`)
- Tests: `tests/test_owntracks.py`, `tests/test_presence_platform.py`
- Upstream docs: <https://owntracks.org/booklet/>
