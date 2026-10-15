# Native health service qualification — 2026-10-15

Target: RT-AX88U, Merlin 3004.388.12_2, Linux 4.1.51 aarch64. Native static
musl builds use Zig 0.15.2; portable tests use Apple clang 21 and Python 3.
The implementation preserves the shell reference byte for byte.

## Implementation and deployment

The persistent C watchdog owns source-bound concurrent ICMP, scheduling,
sample history, classification/recovery, routing intent and the version-1
multi-subscriber health service. The serialized shell actuator retains owned
routing, VPN/local exceptions and DDNS behavior. Affinity consumes health in
its admission loop, independent of delayed native-fact collection. Exclusions
and actual routing acknowledgements are distinct from persistent intent.

Guarded installation preserved the existing startup/firewall hooks and the
shared ownership ledger. Rollback restored the exact original shell and
healthy monitoring; reinstallation returned to the native service. Two
independent readers each received 66 snapshots spanning two publisher
instances and reconnected with fresh eligible evidence. A later logging-only
update uses nonblocking local datagrams and firmware-supplied timestamps.

The native watchdog is installed on the development router. This publication
contains the watchdog and its shared health library only. The consumer
prototype and its integration tests will be published separately; no
persistent consumer hooks or LAN-wide rules were enabled.

## Verification

| Check | Result |
| --- | --- |
| Original shell watchdog/DDNS Python suite | 65 passed |
| Native core and codec | 434 checks passed |
| Actual shell versus C classification replay | 1,000 rows identical |
| Native Python parity/actuator suite | 11 tests passed |
| Original combined publisher/consumer socket, restart, cache and delayed-merge test | 633 checks passed during integration development; consumer test excluded from this publication |
| Standalone publisher/client socket test from a watchdog-only checkout | 625 checks passed on the router at publication |
| Router raw-probe isolation/deadline/drain and send-error regression | 15 checks passed |
| Native core ASAN/UBSAN; clang core/codec analyzer | Passed; no findings |
| File-only whole-LAN/pinned recovery and emergency reversal | Passed on real clock |
| Final whole-LAN orchestration run | 274.63 seconds; recovery hold, failed restoration, monitor stall, hung worker and repair passed |
| Real actuator in isolated kernel namespace | Whole-LAN/pinned failover, restore and exact cleanup passed |
| Mac mini synthetic health matrix | 80 HTTPS/DNS/QUIC probes passed across sticky and affinity |
| Actual production-publisher stop/restart, both client modes | 48 probes passed across baseline, absence and fresh return |

The synthetic matrix confirmed failover, held recovery, a stalled publisher
and policy-specific return. Sticky retained WAN1; affinity returned future
flows to WAN0. Tests bound traffic to the mini's LAN address, used temporary
exact rules and independent cleanup, and validated conntrack NAT tuples.
The actual-publisher check used independent timed restart guards, preserved
forwarding while the publisher was absent, and verified fresh eligibility
after restart. The consumer test used a closed connection domain; its
production binary rejects that test option. No conntrack flush or reverse-path
filter setting changes were used. One earlier retained-GUI-pin diagnostic was
incompatible with strict reverse-path filtering and was discarded. A harness
cleanup-order error was corrected; its independent final cleanup succeeded.

## Naturally occurring WAN event

An unforced WAN0 outage/lease change interrupted an additional client test.
The monitor installed its real owned WAN1 failover overlay, updated probe
configuration for the new lease, retained the recovery hold after clean
measurements, and removed the overlay after recovery. The mini continued
HTTPS/DNS through WAN1 while held. This is useful observed evidence, not a
controlled outage benchmark. The interrupted synthetic run was invalidated.

Final integrity comparisons preserve pins and DDNS/startup/firewall hook
scripts. Firewall differences from the pre-event baseline are exactly the
firmware's WAN0 address/subnet substitutions. The old kernel snapshot was
never restored over the new lease. Temporary queue/rules were removed.

## Performance

Measurements include parent CPU and reaped command-child CPU, using the
router's actual 100 Hz process tick frequency. Percentages are of one core.
They are short healthy steady-state observations, not throughput benchmarks.

| Run | Duration | CPU |
| --- | --- | --- |
| Shell reference, alongside read-only shadow | 70.14 s | 11.11% |
| Native shadow, including fact inspection | 70.14 s | 3.38% |
| Complete active native service, including reconciliation/DDNS checks | 70.27 s | 10.35% |

The full-service CPU saving is modest; routing/configuration shell work still
dominates. The shadow number must not be presented as active-service cost.
The active parent used about 156–160 KiB RSS versus 1,508 KiB for the shell
parent; these figures exclude peak worker memory. Maximum observed normal
native dispatch delay was about 2–3 ms. Retaining two-second fact/repair checks
preserves the initial parity contract. Caching or reducing expensive shell
reconciliation is a separate optimization requiring missed-event/repair tests.

## Limits

Physical WAN outages were not manufactured. Reboot, firewall rebuild, firmware
upgrade, full throughput/offload, arbitrary VPN configurations and broader
affinity rollout remain separate qualification gates. The natural outage and
scoped checks do not imply that existing sessions survive a public-IP change.
IPv6 and inbound-service migration remain outside this implementation.

Consumer results in this report are historical integration evidence, not
commands that require unpublished code to build this watchdog. Publisher-only
socket qualification is independently reproducible from this checkout.
Publication checks also built the static router bundle from a staged checkout
with no affinity tree, ran the 65 original tests plus 11 native Python tests
and 434 core/codec checks, checked shell syntax and driver entry points, and
verified bundle licensing and repository-relative documentation links.

Source/build/operations: [NATIVE.md](NATIVE.md). Subscriber contract:
[PROTOCOL.md](../common/wan-health/PROTOCOL.md).
