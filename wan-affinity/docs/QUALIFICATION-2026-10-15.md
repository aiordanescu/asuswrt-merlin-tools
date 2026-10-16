# Qualification: 2026-10-15

Target: ASUS RT-AX88U, Asuswrt-Merlin 3004.388.12_2, Linux 4.1.51 aarch64.
Static production/test binaries built with Zig 0.15.2 (`aarch64-linux-musl`,
C11, `-O2 -Wall -Wextra -Werror`). This report separates the exercised cases
from untested hardware, physical boot and maximum-load claims.

## Policy

The requested default is `MODE=sticky`, `IDLE_SECONDS=0`: a source's association
lasts while relevant tracked TCP/UDP states exist, with no additional idle
retention. This follows the behavior described by
[OPNsense](https://docs.opnsense.org/manual/firewall_settings.html#sticky-connections),
implemented using Linux conntrack and NFQUEUE. Weights affect initial source
selection. Recovery does not return a sticky association to its original WAN.
Existing connection marks and NAT mappings are not rewritten.

## Automated checks

- Python reference model and controller: **100 tests**. Includes ownership,
  failure rollback, exact cleanup, hook preservation, configuration rejection,
  repeated reconciliation and missing-`cksum` router compatibility.
- Host C decision/parser/policy suite: **147 checks**. Includes concurrent source
  selection, mark preservation, full/partial lifecycle views, held admissions,
  restart adoption, conservative policy-route exclusions, live unpin adoption
  and retention of intentional mixed-WAN failover decisions.
- Worker suite: **21 checks**, including complete IPC, collection failures,
  deadlines and nonblocking cancellation.
- Real-router socket/consumer suite: **633 checks**, including freshness,
  sequence/instance/boot, identity matching, exclusions and health merge behavior.

## Forwarding and health

The final one-client socket matrix passed **80 HTTPS/DNS/UDP-443 probes** across
sticky and affinity modes. It used a private synthetic publisher and a closed
source-port test domain. Both physical WANs stayed enabled and healthy.

Each mode covered healthy selection, failure, recovery hold, a stopped publisher
and fresh recovery. Sticky retained the backup; optional affinity returned new
connections to its preferred WAN. In addition, a confirmed **unreplied UDP**
connection kept its original WAN mark during the synthetic failure while new
connections failed over. `NEW` alone is insufficient: it also matches retries
and unreplied traffic of confirmed conntrack entries. The installed chain now
bypasses CONFIRMED entries and existing WAN connmarks before queueing.

An earlier guarded integration run actually stopped/restarted the production
watchdog publisher and passed 48 forwarding probes across both policies. See
[the watchdog qualification](../../wan-watchdog/QUALIFICATION-2026-10-15.md).
That earlier result is not a claim that we physically failed a WAN during this
rollout. Socket loss cannot prove recovery of an excluded WAN.

The one-client service test also transferred 30 MB through firmware balancing and 30 MB through
sticky admission on the same primary WAN. All three admitted HTTPS flows had
the expected connmark/NAT egress; hardware acceleration remained enabled.
These short transfers are connectivity/obvious-regression checks, not a
statistically controlled throughput or per-flow offload benchmark.

A subsequent [sustained download comparison](THROUGHPUT-2026-10-15.md) completed
30 seconds at **637.8 Mb/s bypassing admission versus 632.2 Mb/s with affinity**
on the same primary WAN. Identified bulk hardware hit counters increased
throughout both runs; all 60 fresh connection probes succeeded, with no queue
drops or helper restart. The original client pin, captured rules and hooks,
and service PIDs matched afterward. The report includes connection-timing
outliers and the limits of this single ordered Internet comparison.

## Kernel lifecycle and latency

The final loopback run used only the dedicated test addresses, UDP port, queue
58888 and owned OUTPUT chain. It verified:

- Never-confirmed dropped admissions are retired after complete U-then-C absence.
- A packet held for four seconds remains tracked beyond the former warning/TTL.
- Release followed by drop permits cleanup without a timer guessing liveness.
- During a roughly 1.57-second collector delay, 360 admissions and 72 echo probes
  completed; observed maximum echo round trip was **0.233 ms**.
- Worker timeouts make firmware readiness unknown while admission stays responsive.
- Killing the parent leaves neither an open queue in its worker nor a running
  orphan collector. The test queue is empty after cleanup.

Earlier isolated kernel tests exercised queue-full fail-open and absent-listener
bypass. Packets already queued when a listener dies can still be dropped.

## Controller, hooks and packaging

Real-binary service tests checked serialized concurrent reconciliation, owned-rule
loss and repair without changing the healthy helper PID, crash recovery,
SIGSTOP/stalled-heartbeat recovery, invalid configuration rejection, delayed
firmware-firewall readiness and manual-stop persistence. Only private objects and
one GUI-pinned client were used; no shared firewall rebuild was requested.

Persistent install qualification passed a disabled fresh install, a scoped
start, same-version upgrade preserving configuration, explicit previous-version
rollback, deliberately failed daemon startup with automatic restoration, persistent
disable and uninstall. Original hook bytes, firmware rules, pins, watchdog owner,
firewall rules and empty queue were verified afterward. The existing watchdog,
DDNS cron registration and TTL hook were retained.

The first LAN soak exposed a compatibility defect: this firmware lacks `cksum`.
The supervisor incorrectly treated that command failure as a stopped heartbeat.
Affinity was disabled while this was repaired. Configuration change detection
now uses available `md5sum` (with host-test `cksum` fallback); liveness reads the
C helper's monotonic heartbeat directly. A regression test additionally requires
a healthy helper to keep its PID across multiple supervisor intervals, before
checking intentional crash/stall recovery. This was a real rollout finding,
not a claim that the earlier short controller tests covered steady supervision.

## Guarded LAN rollout

The installed configuration uses sticky mode, zero extra idle retention,
whole-LAN scope, supervision and `HEALTH_SOURCE=watchdog`. A router-local timed
disable guard protects initial rollout independently of SSH. Existing GUI pins
are retained; the Mac mini's temporary test pin changes are separately restored.
Mixed pre-existing sources remain transitional under firmware balancing rather
than forcing a conflicting association.

The corrected service retained one helper PID throughout the final live check.
Nine Mac mini forwarding checks passed (four HTTPS public-address checks, two
DNS lookups, two UDP-443 version negotiations and one 10 MB download); all had
the expected primary-WAN connmark/NAT egress. Twenty surviving pre-existing
client connections retained their previous marks and NAT addresses. Its
original GUI pin was restored afterward.

Over a roughly 64-second sample, the parent plus reaped collection workers used
**3.92% of one core**, with **0.67% of one core** for the supervisor and its
reaped controller children. Parent resident memory was **788 KiB**; that is not
peak memory for all simultaneously running workers. These are short live
observations under this household's traffic, not a capacity estimate.
The final recorded status had no refresh failures or queue drops, roughly
104 ms maximum collection time, and **6.151 ms** maximum observed queued-packet
timestamp delay across the broader live run. Earlier scoped sub-millisecond
results are not a claim that every production admission stays below 1 ms.

## Limits

No physical router reboot, actual firmware upgrade, WAN cable/disconnection test,
maximum/aggregate throughput qualification, upload test, exhaustive inbound-service
or VPN Director matrix, IPv6 support claim, or other router/firmware certification.
Startup readiness, hook coexistence and loss of owned firewall objects were
emulated/tested without rebooting or rebuilding the household firewall.

The helper supports the default conntrack zone, 1,024 source associations and
4,096 tracked tuples. Full, malformed, stale or incomplete views retain
uncertainty and do not prove idle. Unknown policy-routing selectors may bypass
more traffic than necessary, including an entire LAN for a mark-only VPN rule.
Kernel fallback preserves firmware connectivity but cannot preserve the sticky
placement guarantee while the helper is unavailable or declines admission.

## Reproduction and evidence

See [router test instructions](../router-tests/README.md). Host suites do not
access the router. Every router harness requires an explicit opt-in flag;
installation tests refuse an existing affinity installation. Live harnesses
use private SSH multiplexing and independent timed cleanup/disable guards.

Private raw snapshots, addresses, client output and resource samples stay outside
this repository. The checked-in report contains aggregate observations; no
credentials or full NVRAM dumps are included. Prior stage reports remain as
historical records and are superseded by current operations in the README.
