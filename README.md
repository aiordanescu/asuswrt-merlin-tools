# Asuswrt-Merlin tools

Router-side tools tested on an **ASUS RT-AX88U running
Asuswrt-Merlin 3004.388.12_2**.

| Project | What it fixes |
| --- | --- |
| [WAN watchdog](wan-watchdog/) | Loss-aware IPv4 failover with a native C monitor, retained shell routing/DDNS worker, and a versioned local health socket for independent subscribers. Whole-LAN and pinned modes preserve the existing recovery policy. |
| [WAN affinity](wan-affinity/) | Per-source IPv4 sticky WAN placement for new TCP/UDP connections, with OPNsense-style defaults and optional direct watchdog health subscription. |
| [Namecheap DDNS](namecheap-ddns/) | Adds the selected WAN IP to updates and repairs full-hostname verification and stale-cache refresh behavior. |

Each folder contains source and its installation, upgrade, monitoring,
removal, and rollback instructions. The tools can be used independently. When
using multiple tools, preserve their existing startup hooks. Affinity's
installer adds only its own marked blocks.

WAN Affinity discussion and user test reports are in its
[SNBForums thread](https://www.snbforums.com/threads/wan-affinity-sticky-connections-for-asuswrt-merlin-dual-wan-load-balancing.98093/).

The watchdog is for **Dual WAN load-balance mode**, with **whole-LAN IPv4
failover enabled by default**. It preserves local and recognized VPN routing
and provides a configurable pinned-only mode. The DDNS workaround targets a
**Namecheap subdomain and a directly assigned public IPv4 WAN**. Other models,
firmware releases, IPv6 configurations, and boot/firmware-upgrade behavior have
not been validated.

The watchdog now offers a [native monitor](wan-watchdog/NATIVE.md) alongside
its retained shell reference. Probes, sample history and deadlines run in one C
process; a bounded shell worker preserves routing ownership and DDNS behavior.
The [read-only health socket](common/wan-health/PROTOCOL.md) supports multiple
independent consumers and requires none to be installed. The 30-second loss
window and two-minute recovery policy are unchanged.

[Qualification results](wan-watchdog/QUALIFICATION-2026-10-15.md) include shell/C
parity, subscriber/restart tests, guarded deployment and rollback. Short
healthy-state measurements used about **10.35% of one core for the complete
native service versus 11.11% for the shell reference**. Shell reconciliation
still dominates; the larger shadow-mode saving is not the full service cost.
Build and guarded installation instructions are in
[the native guide](wan-watchdog/NATIVE.md); the watchdog README retains the
separate shell installation path. The [affinity component](wan-affinity/) is
the first consumer: it
selects WANs for new connections while the watchdog retains routing failover
and recovery authority. See its [qualification and limitations](wan-affinity/docs/QUALIFICATION-2026-10-15.md).

Run the watchdog's original 65 simulated state tests without accessing a router:

```sh
python3 -m unittest discover -s wan-watchdog/tests
make -C wan-watchdog host-tests
```

The scripts read configuration from the router at runtime. No DDNS credentials
are required in the repository. **Check WAN → DDNS → Interface**: Auto (the
firmware default) permits DDNS movement with the watchdog helper installed;
Primary/Secondary keep the hostname fixed. See its README for public IPv4 and
provider support before enabling Auto. See the project READMEs for the observed bugs,
exact thresholds, tested behavior, and limitations.

Licensed under [MIT](LICENSE).
