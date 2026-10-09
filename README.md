# Asuswrt-Merlin tools

Two router-side workarounds tested on an **ASUS RT-AX88U running
Asuswrt-Merlin 3004.388.12_2**.

| Project | What it fixes |
| --- | --- |
| [WAN watchdog](wan-watchdog/) | Moves GUI-pinned clients off a failed or severely degraded WAN, then waits for sustained recovery before restoring them. |
| [Namecheap DDNS](namecheap-ddns/) | Adds the selected WAN IP to updates and repairs full-hostname verification and stale-cache refresh behavior. |

Each folder contains the scripts and its installation, upgrade, monitoring,
removal, and rollback instructions. The tools can be used independently. When
using both, merge their startup commands into your existing `services-start`.

The watchdog is for **Dual WAN load-balance mode with source-IP routing rules**;
it does not switch every router/client flow. The DDNS workaround targets a
**Namecheap subdomain and a directly assigned public IPv4 WAN**. Other models,
firmware releases, IPv6 configurations, and boot/firmware-upgrade behavior have
not been validated.

Run the watchdog's 19 simulated state tests without accessing a router:

```sh
python3 wan-watchdog/tests/test_wan_watchdog.py
```

The scripts read configuration from the router at runtime. No DDNS credentials
are required in the repository. See the project READMEs for the observed bugs,
exact thresholds, tested behavior, and limitations.

Licensed under [MIT](LICENSE).
