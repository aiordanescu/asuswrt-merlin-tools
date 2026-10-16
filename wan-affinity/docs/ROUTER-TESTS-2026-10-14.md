# Authorized router kernel tests — 2026-10-14

## Result

The installed RT-AX88U kernel supports NFQUEUE admission, userspace verdict
marks, masked connection-mark persistence and queue-full fail-open without
Entware or a firmware rebuild. The final controlled loopback scenarios pass.
The Python association model now has 71 passing tests.

Full component gate 1 remains **PARTIAL / NOT DEPLOYABLE**. A host-driven test
bridge is not the production router daemon, and loopback UDP is not proof of
LAN-to-WAN forwarding, TCP/QUIC behavior, acceleration or throughput. No LAN
client was placed under the new policy.

## Scope and safeguards

The user explicitly authorized carefully scoped router testing after the
original read-only inspection. The tests created temporary `/tmp` files and
an exact loopback-only mangle OUTPUT jump, using UDP source `127.88.0.2`,
destination `127.88.0.1:58888`, queue 58888 and an otherwise unused owned chain.
No PREROUTING/LAN jump, WAN transition, IP policy rule, persistent hook or
router package install was performed. Existing GUI pins and WAN-watchdog
behavior were outside the test traffic path.

Before linking a test chain, an independent router-side 180-second cleanup
guard was armed and checked alive. A normal host-side finally block also ran
the same exact cleanup. Connection deletion was limited to the dedicated
loopback source/destination/UDP port. No global conntrack flush or blanket
firewall restore was used.

Local backups record the initial firewall rules, routing rules/tables,
modules/queue state and SHA256 hashes of five production scripts, plus test
logs and pre-fix prototype source. Baseline and post-test comparisons confirmed
the original firewall rules (ignoring changing counters), routing rules and
production-script contents. Queue bindings and owned test chains were gone.
Final checks also confirmed unchanged routing tables and loaded-module names;
all five owned router `/tmp` directories and remaining guard processes were
removed.
Both WANs reported zero probe loss and no active failover after the final run.

## Real packet results

| Check | Observed result |
| --- | --- |
| Bind an unused NFQUEUE and configure fail-open | Kernel ACKs succeeded; no package install needed |
| Four router-side producers, 96 concurrent first UDP flows from one source | All decisions carried WAN0 packet mark `0x80000042`; all 96 confirmed states carried `0x8000005a` |
| Apply WAN1 mark | Confirmed state carried `0x9000005a` |
| Preserve unrelated bits | Packet sentinel `0x42` and connection sentinel `0x5a` survived the masked operations |
| Confirmed but quiet UDP flows | Retained across a fresh real conntrack read after three seconds |
| All confirmed test flows explicitly removed | Empty validated read allowed source-association expiry |
| Real first packet held queued/unconfirmed for three seconds | Confirmed table was empty; corrected model retained the association, reported uncertainty and expired nothing |
| Resume the delayed packet | Connection mark appeared after release and ordinary reconciliation cleared uncertainty |
| No listener | Three new flows bypassed and were confirmed |
| Queue capacity eight, 64 packets sent without verdicts | Eight held; 56 bypassed and were confirmed |
| Kill the helper with eight packets pending | Queue disappeared; pending packets were lost; three subsequent new flows bypassed successfully |

The test engine ran on macOS and returned decisions over SSH to a small
statically linked aarch64 Linux C bridge on the router. The bridge spoke the
kernel netlink protocol directly. This proves that the required facilities
are present, not that helper packaging/lifecycle or a production C port is done.

## Two defects exposed and addressed

### NF_ACCEPT does not resume the next iptables rule

The first packet run delivered 96 packets but left their conntrack WAN bits
unset. Counters showed zero hits on the CONNMARK rule after NFQUEUE. NF_ACCEPT
resumed after the current Netfilter hook, skipping the remaining rules in the
same mangle table. Therefore the original documented queue-then-save sequence
was incorrect.

The positive diagnostic uses **NF_REPEAT plus a packet-WAN-mark guard**, so
the table is evaluated again, the marked packet is not queued again, and the
masked CONNMARK save executes. Its following connmark matches counted 97 WAN0
packets and one WAN1 packet. This correction is in the diagnostic recipe and
design notes; the production kernel adapter remains unimplemented. It must
handle re-entry, exclusions, ownership and firmware-rule ordering deliberately.

### A provisional timeout is not evidence that a connection ended

The original engine discarded an unconfirmed admission after two seconds. A
real packet held in NFQUEUE for three seconds was absent from the confirmed
conntrack dump, so that logic expired its still-needed source assignment.
The earlier model test described this as an abandoned connection without
actually supplying evidence of abandonment.

The model now retains provisional state until confirmation or explicit
terminal lifecycle evidence. Two seconds is only a warning threshold; an old
unconfirmed admission sets uncertainty rather than granting expiry. Tests
cover delayed confirmation and terminal-evidence cleanup. The same real-packet
case passed after the fix.

Production work must still bound resources and recover conservatively from
admissions that never produce a confirmation or terminal event. It must not
reintroduce time-based deletion merely to reclaim memory.

## Failure-mode implications

Queue-full fail-open and absent-listener bypass are separate settings; both
were tested. Neither preserves packets already held when a queue is destroyed.
Fail-open traffic may also lose the component's sticky guarantee while the
component is unhealthy. The production lifecycle must report this degraded
state and remove owned admission hooks safely when disabling/failing over to
firmware behavior. It must not claim uninterrupted stickiness through helper
overload/crash.

## Remaining gates

- Implement a real on-router serialized helper/event/dump adapter and lifecycle
  control. The current production adapter is still documentation-only.
- Test scoped LAN ingress and actual WAN selection for TCP, UDP and QUIC,
  including existing marks, firmware ports, GUI pins and VPN exclusions.
- Qualify both policy lifecycles, including persistent-affinity return to its
  preferred WAN after recovery; the kernel diagnostics exercised sticky admission.
- Validate conntrack identity/event-loss recovery and non-atomic dump boundaries
  against real concurrent traffic, not only the model's framed dump format.
- Validate pending-admission resource bounds and incomplete lifecycle evidence.
- Measure forwarding/offload behavior, admission rate and latency without
  globally disabling acceleration or disturbing existing sessions.
- Validate firewall rebuild/reboot/disable/uninstall ownership and rollback.

See [router-tests](../router-tests/README.md) for the opt-in harness and
reproduction boundaries. Nothing was committed or pushed as part of this work.
