# Secure Remote Access

KNX Agent is designed for a trusted home network. Do **not** expose Matrix,
MQTT, Frigate, camera streams, webhooks, or device APIs directly to the public
Internet.

## Recommended architecture

Use a private mesh VPN such as Tailscale, Headscale, or WireGuard:

```text
Phone / operator
       │ encrypted private overlay
       ▼
KNX Agent host ── isolated IoT VLAN ── Gira X1 / cameras / door station
```

Recommended controls:

1. Bind service ports to loopback by default. The supplied Compose file does this.
2. Give every Matrix user an explicit profile and keep rooms private.
3. Require MQTT credentials and topic ACLs; use TLS outside loopback.
4. Permit webhook traffic only from the relevant device IPs at the host firewall.
5. Keep cameras and physical-security devices on an isolated IoT VLAN.
6. Use least-privilege VPN ACLs. Never use port forwarding as a shortcut.
7. Keep deployment-specific IP addresses, hostnames, topology, and credentials out
   of Git. Use ignored local configuration files.

## Example VPN policy

The exact syntax depends on the chosen VPN. At minimum:

- user devices may reach Matrix only;
- operator devices may reach administration endpoints;
- the brain host may reach required IoT devices;
- IoT devices may only reach their authenticated callback endpoints;
- customer/home networks must never be mutually reachable.

## Legacy protocol caveats

Some Gira and Mobotix hardware uses self-signed HTTPS, query-token APIs, or plain
HTTP. Treat network segmentation as mandatory compensating control. Configure a
CA bundle or certificate validation whenever the device supports it. If plain
HTTP is unavoidable, restrict it to a dedicated wired IoT network and never route
that traffic through untrusted networks.

See [SECURITY.md](../SECURITY.md) and [THREAT_MODEL.md](../THREAT_MODEL.md) before
deploying.
