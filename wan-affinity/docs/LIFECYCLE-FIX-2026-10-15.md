# Lifecycle cleanup and refresh latency fixes

Date: 2026-10-15. Target: RT-AX88U, Merlin `3004.388.12_2`, Linux `4.1.51`
aarch64. This follows [the earlier scoped results](STAGE2-3-RESULTS-2026-10-14.md).

**Both identified issues are fixed and passed scoped router checks.** This
removes those two blockers; it does not qualify a LAN-wide release, sustained
throughput/offload, reboot/firewall/upgrade lifecycle or the remaining policy
matrix. Nothing was installed persistently or pushed.

## Cleanup: complete lifecycle observations

The old reader saw only confirmed conntrack entries. A packet still waiting
in NFQUEUE is unconfirmed; a short-lived or rejected flow might disappear
before any confirmed-table observation. Keeping every missing provisional
entry forever protected held packets but prevented eventual cleanup.

The new worker reads **unconfirmed first, confirmed second**, then takes the
union by exact original five-tuple. If a live connection moves from the first
table to the second between reads, at least one read sees it. A tuple missing
from both complete reads can be retired. Neither elapsed time nor a lone
DESTROY event authorizes expiry.

The reader validates each process exit, every row and the independently
reported firmware row count. The installed firmware ignores source filtering and
extended output for its unconfirmed dump, so the worker filters explicitly and
accepts that table's firmware format without requiring an ID. Confirmed rows
still require the expected metadata. Duplicates from U-to-C migration retain
confirmed NAT attribution.

Admissions and result application remain serialized in the parent. Every
admission is timestamped when it is made, rather than when poll returned. A
result whose collection began at or before that admission cannot remove or
replace it, even if the older view is empty or contains an earlier tuple.
Partial, failed, oversized and stale observations retain state and inhibit
expiry. Confirmed-only legacy model inputs retain their conservative behavior.

## Latency: a bounded refresh worker

Only a separate process performs NVRAM reads, firmware route checks, optional
health-snapshot reads and conntrack collection. The parent continues servicing
NFQUEUE while that work runs. It consumes a bounded amount of nonblocking pipe
data per loop and applies only a complete, successfully exited worker result.
Tuple lookup and snapshot-identity merge now use hash indexes with full-key
collision checks; hashes are not connection identities.

The worker has a five-second deadline, its own process group, no copy of the
queue socket, close-on-exec IPC descriptors and Linux parent-death handling.
Failure leaves prior assignments in place and reports uncertainty. Once firmware
facts exceed the configured freshness limit (15 seconds by default), readiness
becomes UNKNOWN; missing refreshes do not imply two healthy WANs. Initial
activation requires a successful complete observation.

A stress test also exposed stdout/stderr interleaving when a larger firmware dump
filled stdio buffers. The reader now keeps rows and completion output separate;
completion uses an anonymous `/tmp` file in the worker. No dump or frequent
state is written to flash.

## Actual router checks

All loopback cases used a unique temporary chain and queue, fixed
`127.88.0.2 -> 127.88.0.1:58888` traffic, an independent cleanup guard and exact
cleanup. Test-only delay/loopback options exist only in the `WA_TESTING` build.
The normal binary does not expose them.

| Check | Observed result |
| --- | --- |
| Held first packet | Still present in the unconfirmed table after four seconds, absent from confirmed. Assignment retained across multiple refreshes, without a provisional-timeout deletion. |
| Never-confirmed terminal drops | Sixteen packets dropped after admission were absent from both tables; states and association returned to zero. |
| Held then terminally dropped | Held assignment remained until release; after release/drop the full view proved absence and cleanup completed. |
| Deliberately slow refresh | 360 admissions while refresh took about 1.56 seconds. All became observed without persistent uncertainty. |
| Round trips during slow refresh | 72 measured UDP echo round trips, three batches of 24. Batch p95 at most 0.204 ms; maximum 0.290 ms. This is loopback evidence, not WAN throughput. |
| Worker timeout | Packet admission continued while workers exceeded their deadline; failures were reported and prior state retained. |
| Extended missing refresh | Both firmware WAN readiness values became UNKNOWN after the freshness limit; the existing source assignment remained intact. |
| Helper killed during collection | Queue closed, no running collector remained, and the next test cleanup removed its exact objects. |

The production build was also exercised on the authorized macOS client, bound
to its home-LAN IPv4 address so its direct modem interface could not substitute
for router forwarding. Its original Primary pin was verified, temporarily
removed only for the scoped test, then restored. Both real WANs stayed enabled.
Private test health fixtures drove the policy; the production watchdog was
unchanged.

Four concurrent fresh HTTPS requests, two bound DNS queries and two actual
UDP/443 QUIC version negotiations passed in each of four states: firmware
Primary, failed Primary/backup selected, recovery held on backup, and recovered
Primary. The 16 HTTPS responses showed the expected public WAN address. A
separate HTTPS connection remained idle for 25 seconds across the new-flow
failover and then returned HTTP 204 on the same socket.

Across 93 successful observations, the old persistent
`unconfirmed-admission` error did not recur. Unobserved admissions repeatedly
returned to zero after refresh. Transient unobserved counts between admission
and observation remain normal. Final sample: 60 admissions, zero unobserved,
zero failed refreshes, no uncertainty, resident memory about 740 KiB.

The macOS-client run observed a maximum kernel-packet-timestamp-to-helper-read
delay of **0.296 ms**, compared with **73.159 ms** in the earlier run. Slow
refresh work itself still reached 104.33 ms, but it ran outside the admission
loop. These are scoped samples, not guaranteed latency bounds or sustained-load
qualification. The final cutoff-timestamp change was additionally exercised by
the complete loopback suite.

## Validation and restoration

- 134 C engine/parser/framing/lifecycle checks and 21 worker checks passed,
  including AddressSanitizer and UndefinedBehaviorSanitizer builds.
- 95 Python model/controller tests passed. The reference model now explicitly
  distinguishes full U-then-C observations from confirmed-only inputs.
- Static normal and test ARM builds passed `-Wall -Wextra -Werror`.
- The macOS client's original pin was restored; helpers, queues, test jumps,
  chains and temporary router directories were removed. No global conntrack
  flush, actual WAN transition, firewall rebuild or reboot was used.
- Before/after production scripts, routing rules/tables, GUI pins, modules and
  firewall rules are checked against the private backup. No public network
  dumps or credentials are included here.

Private evidence includes original source, binaries, hashes, firmware packet
captures/status, client results and restoration checks. Wider installation
remains disabled; the unrelated qualification gates in the prior report remain.

Subsequent [mode-matrix checks](MODE-MATRIX-2026-10-15.md) repeated sticky
failover/recovery using this final build and verified both modes forwarding
with firmware readiness alone. The production watchdog stayed running; actual
watchdog stop/restart and firmware-only outage/recovery remain deferred.
