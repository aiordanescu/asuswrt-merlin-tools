# Design

> Current release operations and evidence are in the [README](../README.md)
> and [qualification report](QUALIFICATION-2026-10-15.md). The stage narrative
> below records the evolution of the implementation.

> Follow-up (2026-10-15): the unconfirmed-flow cleanup and blocking-refresh
> issues described below were fixed and tested. Current details:
> [LIFECYCLE-FIX-2026-10-15.md](LIFECYCLE-FIX-2026-10-15.md). Other rollout
> qualification gates remain. The earlier findings are preserved as history.

## Current runtime qualification

The scoped helper builds and forwards real LAN traffic. The original stage-2
findings are in [that report](STAGE2-3-RESULTS-2026-10-14.md); current cleanup
and latency results are in [the follow-up](LIFECYCLE-FIX-2026-10-15.md).
The installed firmware omits queue conntrack metadata. Its adapter uses exact
original five-tuples and local tokens. A bounded worker collects unconfirmed
then confirmed states; their validated union can retire completed admissions
without expiring held packets by time. The parent protects admissions at/after
the observation cutoff and applies results in its serialized admission loop.
Raw events do not delete tuple identities. See the current qualification report
for the guarded rollout and remaining platform/throughput limits.

The invariant/event discussion below describes the model contract. The live
compatibility adapter conservatively observes events and relies on validated
full lifecycle observations for retirement. It does not claim that native kernel IDs were delivered
with queued packets.

## Correctness invariants

1. One association per eligible LAN IPv4 source. Simultaneous first
   connections cannot choose different WANs.
2. Relevant TCP/UDP states - including quiet established connections - retain
   the association. Optional idle retention starts only after the last relevant
   state disappears.
3. Creation and expiry have a defined serialization boundary; conntrack event
   arrival order is not that boundary.
4. GUI source/destination/subnet pins, local/VPN traffic and incoming flows are
   excluded. HTTPS/QUIC are included.
5. Established marks and unrelated mark bits are preserved. Uncertain tracking
   retains associations and reports uncertainty. Restart reconciles existing
   mixed-WAN flows without resetting them.
6. Health exclusions and recovery eligibility constrain selection independently
   of stickiness.

## Mechanism: serialized NFQUEUE admission

Router-kernel follow-up: the original NF_ACCEPT + following CONNMARK sequence
below was disproved. NF_ACCEPT skips the rest of the current iptables hook.
The successful loopback diagnostic used NF_REPEAT and a WAN-mark guard before
queueing, then saved the mark on re-entry. This must be integrated carefully
with production exclusions/native ordering; the schematic is not deployable.
See [the kernel test report](ROUTER-TESTS-2026-10-14.md).

All eligible unclassified first packets enter one serialized decision path; the
helper advertises its intent to reserve the source association *before* the
packet is released.

```
mangle PREROUTING (before the native `-j balance` jump)
  -i br0 -m state --state NEW -j WAN_AFFINITY_ADMIT
  WAN_AFFINITY_ADMIT: eligibility exclusions -> NFQUEUE --queue-num N [--queue-bypass]
  ... helper verdict carries the WAN mark ...
  CONNMARK --save-mark (persist) / connmark --restore-mark (route)
```

Why this is atomic per source: the helper runs a **single-threaded event loop**
over the NFQUEUE fd and the conntrack event/dump sources. Two simultaneous
first packets from one source are dequeued one after the other; the first
creates the association, the second observes it. No userspace "native chooses,
then learn" window exists (that approach is rejected: two simultaneous first
packets are chosen independently before any learning).

Conntrack events and periodic reconcile are **lifecycle tracking, not atomic
assignment**. They tell us when a source's last relevant state is gone.

## Expiry without event-order dependence

Conntrack netlink events are not globally ordered (four CPUs on this router), so
a lone DESTROY never expires anything. A DESTROY only marks the source as an
expiry *candidate*. The association is removed only when a **validated** dump
confirms the source has zero relevant states, and the removal happens in the
same single-threaded loop as admission. Consequences:

* An expiry can never race an admission for the same source.
* An empty/error/truncated read never removes state; the association is
  retained and uncertainty is reported.
* If no fresh validated reconcile exists, nothing expires.
* A just-admitted but not-yet-confirmed connection is retained until confirmation
  or explicit terminal lifecycle evidence. The router test disproved the
  original short provisional TTL: a still-queued packet can remain absent from
  the confirmed table beyond it. A two-second threshold now reports uncertainty
  without dropping the provisional state. Production resource bounds and
  incomplete-lifecycle recovery still need qualification.

Because every eligible connection-start is admitted through this one loop, the
linearization point for "which WAN does this connection use" is the admission
itself, not kernel/event timing.

## Health

Two layers, never mixed:

* **Native (standalone).** `wanN_enable`, `wanN_state_t` (2 = connected), a
  valid lease/gateway/interface and route readiness. No probes. Native
  "connected" is link/DHCP level and is *not* a quality claim — on the inspected
  router `dns_probe=0` and `wan_dns_probe=0`.
* **Enhanced (optional).** A versioned, atomically published `wan-watchdog`
  snapshot carrying raw flags, an explicit `eligible_for_new_assignment`
  decision (already accounting for the watchdog's recovery hold and emergency
  reversal), a separate `recovered` flag, sequence, publisher/boot identity and
  monotonic publication time.

Rules: an existing association is reassigned only when its WAN is **known
unusable**; `unknown` retains it. New selections need an *eligible* WAN. When
enhanced health disappears, native state takes over, but a previously excluded
WAN stays excluded (UNKNOWN) until fresh recovery evidence or an explicit
native-only switch. `eligible` is never equated with completed recovery.

## Rejected mechanisms (executable counter-examples)

* **native-choice-then-learn** (`mechanisms.NativeThenLearn`): splits
  simultaneous first packets (test fails as designed).
* **timer-only / packet-idle IP sets** (`mechanisms.TimeoutIpset`): expires a
  quiet but established connection (test fails as designed).

A kernel IP set fast path is deliberately **omitted** from the admission path
until its synchronization with expiry is demonstrated; the IP set must not be
able to bypass the serialized decision.

## Unresolved dependencies

The authorized loopback tests resolved queue binding, NFQA_MARK verdicts,
guarded re-entry/CONNMARK persistence and basic crash/overflow behavior. A
static C diagnostic ran without Entware. These are available building blocks,
not an implemented production helper. Remaining work includes:

* Production admission/helper packaging, re-entry/exclusion ordering and lifecycle.
* Reliable pending-admission cleanup without a time-based safety violation.
* Real concurrent conntrack identity, event loss and dump reconciliation.
* IPset protocol revision (userspace v7.6 vs kernel protocol 6) under load.
* Hardware/software flow acceleration interaction and throughput.

The router already exposes the NFQUEUE target, `connmark`/`conntrack`/`mark`
matches, `xt_set.ko`, `hash:ip`/`hash:mac`/`list:set` and a working
`conntrack -E`; that is capability evidence, not packet-path proof.
