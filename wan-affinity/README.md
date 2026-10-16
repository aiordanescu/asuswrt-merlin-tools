# WAN affinity for Asuswrt-Merlin

Per-source IPv4 WAN stickiness for Dual WAN **load-balance** mode. New TCP and
UDP connections from a LAN address use the same eligible WAN while that source
has connections belonging to that source mapping. Existing connections keep their marks.

The default is **OPNsense-style sticky connections**: `MODE=sticky`,
`IDLE_SECONDS=0`. OPNsense documents keeping a source's gateway while any state
referring to the mapping remains active; this project implements that policy on Linux/Asuswrt rather
than reproducing pf. [OPNsense reference](https://docs.opnsense.org/manual/firewall_settings.html#sticky-connections).

Tested hardware: **RT-AX88U, Merlin 3004.388.12_2, Linux 4.1.51, aarch64**.
Treat other firmware, router architectures, and IPv6 as unqualified. Read the
[qualification report](docs/QUALIFICATION-2026-10-15.md) before deployment.

## Behavior

- A new source chooses an eligible WAN using the firmware's `wans_lb_ratio`
  weights, currently read every two seconds. Weights apply to new source
  associations, not to individual connections or measured bandwidth.
- The association remains while its member TCP/UDP states exist, including quiet
  established connections and packets still awaiting confirmation. A complete
  unconfirmed-then-confirmed view must prove them gone before idle expiry.
  `IDLE_SECONDS=0` adds no extra idle timer; collection still takes time.
- If its WAN becomes unusable, **new connections** can select the other eligible
  WAN. Recovery does not move a sticky association back. Existing sessions are
  never flushed, rewritten, or promised to survive a WAN outage.
- Optional `MODE=affinity` uses a stable, weighted preference derived from the
  source IP and a router seed. It can return **new** connections to that WAN
  after recovery. This is a different policy from the default sticky mode.
- Mixed or unattributed pre-existing connections never block adoption. The next
  new connection selects one eligible WAN; subsequent new connections share it.
  Existing connections keep their WANs and NAT mappings. This applies to both modes.
- A restart restores verified same-boot mappings from a private RAM checkpoint.
  Without a valid checkpoint, an unambiguous surviving WAN can be adopted;
  mixed sources select a WAN on their next new connection. Older connections
  are tracked separately and do not prolong a newly created mapping's lifetime.
  See [adoption and restart behavior](docs/ADOPTION-2026-10-15.md).

Identity is the **LAN IPv4 address**, not the device MAC, user, application,
remote site, or a permanent DHCP identity. IPv6 and protocols other than TCP/UDP
continue through firmware routing.

## Optional WAN Watchdog integration

WAN Affinity chooses a WAN for each new connection. Its health source determines
how it knows which WANs are usable:

| Your setup | Setting | Health information |
| --- | --- | --- |
| **WAN Watchdog installed** | `HEALTH_SOURCE=watchdog` | WAN Watchdog's measured reachability, packet loss and recovery status |
| **Router readiness only** | `HEALTH_SOURCE=firmware` | Interface, address, gateway, state and route readiness; no active Internet probes |

Use `watchdog` for **WAN Watchdog** and `firmware` for **router readiness**.

WAN Watchdog continues to control household routing failover and recovery.
Those routing changes can override affinity's WAN choice and interrupt existing
sessions. The components are installed and operated independently.

The default `HEALTH_SOURCE=auto` discovers WAN Watchdog when available and
otherwise uses router readiness, retaining known exclusions. The older file
adapter is available through `legacy`. See [health source details](docs/HEALTH-SOCKET.md)
for fallback and recovery safeguards.

The watchdog integration uses the local health socket at
`/tmp/wan-watchdog/health.sock`. A stale or disconnected watchdog cannot prove
that an excluded WAN has recovered. Existing associations retain their WAN
when health is UNKNOWN; same-boot RAM checkpoints preserve exclusions across
helper restarts. When the running helper declines admission, it returns the
packet to firmware load balancing. Kernel queue bypass has a different path,
described under [operations and rollback](#operations-and-rollback).

The [0.4.3 maintenance notes](../MAINTENANCE-2026-10-15.md) cover lifecycle
repairs and the kernel fallback verification.

## Routing boundaries

The owned `WA_ADMIT` chain is inserted immediately before firmware `balance` in
mangle PREROUTING, for NEW, ORIGINAL-direction traffic from the selected LAN
scope on the firmware's `lan_ifname`. It queues only packets without a WAN mark.
The firmware routing tables and existing connection handling remain authoritative.
Confirmed conntrack entries and existing connection WAN marks bypass the queue
too: `NEW` also includes TCP retries and unreplied UDP, which must keep their
existing NAT mapping across a failover.

Manual GUI source/destination WAN pins, local/private/multicast destinations,
routed non-default prefixes in the main table, non-LAN sources, and packets
already carrying WAN marks bypass automatic assignment. Incoming connections
and VPN-originated packets do not match the LAN ORIGINAL-direction selector.

Unfamiliar policy-routing tables/actions conservatively exclude their complete
source/destination selectors. A mark-only full-tunnel VPN policy can consequently
bypass affinity for the entire LAN. Preserving VPN routing takes precedence
over coverage; arbitrary VPN clients, VPN Director edits and third-party policy
systems are not comprehensively qualified. Policy facts refresh asynchronously,
so GUI/policy edits are not an atomic transaction with packet admission.

Only the firmware WAN field `0xf0000000` changes; other mark bits are retained.
The pass-through sentinel uses that reserved field and is cleared on re-entry.
`NF_REPEAT` is required to reach the following masked CONNMARK save; `NF_ACCEPT`
was tested and found unsuitable for that packet path. Existing states are not
flushed on failover, recovery, stop, upgrade, or uninstall.

## Download and install

No Entware dependency or router firmware rebuild is required for the tested
static aarch64 binary. Download **`wan-affinity-aarch64.tar.gz`** and
**`SHA256SUMS`** from the
[prebuilt beta release](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/tag/wan-tools-2026.10.15-beta.5)
to your computer. This build targets **aarch64 Linux** and was tested on the
RT-AX88U / Merlin 3004.388.12_2. Compare the archive's checksum with its entry
in `SHA256SUMS` (on macOS):

```sh
shasum -a 256 wan-affinity-aarch64.tar.gz
```

On Linux use `sha256sum`. See
[release and verification details](https://github.com/aiordanescu/asuswrt-merlin-tools/blob/wan-tools-2026.10.15-beta.5/RELEASES.md).
Using an SSH alias named `router`, transfer the verified archive:

```sh
scp -O wan-affinity-aarch64.tar.gz router:/tmp/
```

On the router, extract into a new private staging directory and run the
installer there. Keep an existing staging directory for inspection rather than
overwriting it:

```sh
umask 077
mkdir /tmp/wan-affinity-stage
tar -xzf /tmp/wan-affinity-aarch64.tar.gz -C /tmp/wan-affinity-stage
cd /tmp/wan-affinity-stage
sh install.sh --install
```

This installs `/jffs/wan-affinity` and additive, asynchronous blocks in
`services-start` and `firewall-start`. It preserves other hook content,
including watchdog startup and TTL rules. **The example is disabled and scoped
to one client.** It does not enable WAN affinity automatically.

Edit `/jffs/wan-affinity/wan-affinity.conf`. Use plain, unquoted `KEY=VALUE`
assignments: the trusted root-owned file is sourced by the controller and
parsed by the C helper. For an intentional whole-LAN deployment with watchdog:

```ini
ENABLED=1
MODE=sticky
LAN_SCOPE=1
ALLOW_LAN_SCOPE=1
SUPERVISE=1
QUEUE_NUM=20880
QUEUE_MAXLEN=1024
IDLE_SECONDS=0
MAX_RECONCILE_SECONDS=15
PROVISIONAL_WARNING_SECONDS=2
RECONCILE_INTERVAL=2
AFFINITY_SEED=
HEALTH_SOURCE=watchdog
HEALTH_SOCKET=/tmp/wan-watchdog/health.sock
STATUS_DIR=/tmp/wan-affinity
```

For one-client qualification, use `LAN_SCOPE=0` and `CLIENT=<LAN IPv4>`.
The controller refuses a CIDR or the current SSH management client as that test
selector. GUI-pinned clients remain excluded; do not remove pins merely to
make a status counter increase. A separate LAN management connection and a
router-local timed disable guard are useful during initial deployment.

Validate before activation:

```sh
/jffs/wan-affinity/wan-affinity --config /jffs/wan-affinity/wan-affinity.conf --check-config
/jffs/wan-affinity/wan-affinity.sh start
/jffs/wan-affinity/wan-affinity.sh status
```

## Optional source build

To build on a development machine instead, run from the repository root:

```sh
make -C wan-affinity host-tests
python3 -m unittest discover -s wan-affinity/tests
make -C wan-affinity bundle ROUTER_CC='zig cc -target aarch64-linux-musl'
```

This creates `wan-affinity/build/wan-affinity-aarch64.tar.gz`. Transfer it and
follow the same installation/configuration steps above. See
[BUILDING.md](BUILDING.md) for the toolchain and diagnostic build details.

## Operations and rollback

```sh
/jffs/wan-affinity/wan-affinity.sh status
/jffs/wan-affinity/wan-affinity.sh reconcile
/jffs/wan-affinity/wan-affinity.sh restart
/jffs/wan-affinity/wan-affinity.sh stop
/jffs/wan-affinity/wan-affinity.sh disable
/jffs/wan-affinity/wan-affinity.sh uninstall
```

`start` is idempotent for an unchanged configuration. `reconcile`, used by both
hooks, repairs only owned rules and preserves a healthy helper's PID and
associations. A configuration change requires reconstruction and is applied by
`start` or the supervisor. `restart` deliberately reconstructs even when the
configuration is unchanged. `status` is informational and returns success even
when stopped; inspect the PID, jump count and status contents.

`SUPERVISE=1` checks every ten seconds, retries boot readiness, repairs lost
rules and restarts an exited helper. Two unchanged status observations request
a bounded restart of a stalled event loop. It can take roughly 10–20 seconds to
detect a stall, followed by shutdown/startup time. That is process recovery,
not a strict packet-latency guarantee during a hang.

`stop` removes the owned jump/chain before stopping the helper and remains
stopped across subsequent firewall hooks. `start` explicitly clears that RAM
stop marker; a reboot clears it too. **Use `disable` to persist across boots.**
Changing `ENABLED=0` is also honored by the next supervisor reconciliation.
`uninstall` additionally removes only the marked hook blocks and component
files. Ordinary rules, firmware routing and conntrack entries remain in place.

The kernel queue has bounded length, queue-full fail-open, and absent-listener
bypass. Packets already queued when a process dies can be lost and retransmitted.
When the running helper passes a packet back, firmware balancing can distribute
it across WANs. If no helper is listening, or the kernel queue is full, kernel
bypass accepts the packet without executing later rules in mangle PREROUTING,
including the firmware balance chain. Remaining routing policy then selects
the path; weighted balancing and sticky placement are not guaranteed. The
supervisor removes abandoned admission rules before attempting a restart.
`stop` and `disable` remove the admission jump to restore normal firmware
traversal. A blocked helper can hold queued packets until recovery/full-queue
bypass; fail-open is not an immediate deadline for every packet.

Upgrade an extracted bundle with `sh install.sh --upgrade`. It preserves the
configuration and keeps one previous binary/controller/config set. If startup
fails, it restores that set. Manual previous-version rollback:

```sh
sh /jffs/wan-affinity/rollback.sh --rollback
```

For an immediate return to firmware balancing, use `disable`. A first installation
has no previous affinity version to restore.

## Status and limits

`/tmp/wan-affinity/status` is an atomic diagnostic file, not the health IPC.
It includes source associations, mapping-member and legacy-state counts, enhanced-health
status, publisher sequence/instance, evidence age, queue/admission counts,
refresh failures and maximum observed packet timestamp delay. `awaiting_new`
counts sources waiting for their next new connection; it does not require an
idle period. Per-source `members` belong to the current mapping; `legacy` counts
older connections whose placement stays unchanged.

The helper bounds associations at 1,024 and tracked tuples at 4,096, plus
bounded local/policy selector lists. Incomplete, malformed or oversized views
are rejected. Uncertain observations retain state; stale firmware facts prevent
new eligibility. This is a home-router implementation, not a claim to support
300,000 simultaneous conntrack entries. Monitor `uncertain`, `last_error`,
`refresh_failed`, queue drops, CPU and memory when evaluating a busier network.

A bounded worker reads firmware facts and complete U-then-C conntrack views;
packet admission stays in the parent loop. RAM holds the frequent state and
bounded logs. `/tmp/wan-affinity/mappings.cache` is a bounded, root-only RAM
checkpoint, refreshed with valid observations and mapping changes. It is
validated against boot identity, mode/scope/seed, age (at most five minutes),
checksum and surviving connection identities. Invalid or missing checkpoints
fall back to fresh adoption; they never recreate connections. No frequent
mapping writes go to flash. Hardware acceleration is left enabled. A
[30-second primary-WAN download comparison](docs/THROUGHPUT-2026-10-15.md)
measured 638 Mb/s bypassing admission versus 632 Mb/s through affinity, with
increasing hardware hit counters in both runs and no queue drops. This
one-client Internet sample does not establish maximum throughput, upload or
aggregate dual-WAN capacity, or qualify every accelerated flow path.

See [BUILDING.md](BUILDING.md), [router test instructions](router-tests/README.md),
and [current qualification](docs/QUALIFICATION-2026-10-15.md). Older stage reports
in `docs/` record earlier gates and failures; they do not describe the current
installation instructions.

Source licensed under [MIT](../LICENSE). Static-runtime notices are included
in [LICENSES/](LICENSES/).
