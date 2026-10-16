# Gate 1 - decision and evidence

> Historical record. Subsequent scoped forwarding tests compiled and exercised
> the ARM helper, repaired startup/cleanup and packet framing, and identified
> a remaining admission-lifecycle blocker. Current status is in
> [STAGE2-3-RESULTS-2026-10-14.md](STAGE2-3-RESULTS-2026-10-14.md).

Date: 2026-10-14. Component: `wan-affinity`. Original scope: local prototype
and read-only router inspection. Follow-up: explicitly authorized temporary
loopback kernel tests, subsequently cleaned up. No production watchdog,
persistent hooks or packages changed.

## Decision: PARTIAL / UNPROVED

Update after explicitly authorized, loopback-only router tests: the installed
kernel's NFQUEUE/verdict-mark facilities work. The corrected loopback packet
scenarios pass, and the model suite now has 71 passing tests. Two defects were
exposed: NF_ACCEPT skipped the following CONNMARK save, and a two-second
provisional timeout expired a still-queued admission. See
[the kernel test report](ROUTER-TESTS-2026-10-14.md) for fixes and limits.
The full gate remains partial: no production daemon or LAN forwarding policy
was deployed, and WAN offload/throughput remain untested.

Original read-only gate result: the association/serialization **model** passed
its adversarial suite. The real **packet path** was unproved because no isolated Linux environment was available
(host is macOS arm64 with no Docker/podman/lima) and the live router must not be
used for queue binding, module loading or test rules.

## Environment verified read-only (RT-AX88U, 3004.388.12_2, kernel 4.1.51 aarch64)

* Load balance `wans_mode=lb`, `wans_lb_ratio=2:1`; wan0=eth0, wan1=eth2;
  marks `0x80000000`/`0x90000000`, mask `0xf0000000`.
* Firmware PREROUTING: `-i br0 -m state --state NEW -j balance`, then
  `connmark --mark 0x80000000 -j CONNMARK --restore-mark`. Firmware `balance`
  randomly picks WAN0 at `--probability 0.67` else WAN1, and returns early for
  local/VPN destinations and TCP 443/8443 and UDP 443/80.
* `CONNMARK` help: `--save-mark` = "save the packet nfmark in the connection";
  **no `--runtime` option**. Confirms the earlier claim was wrong and that marks
  are applied when the rule runs, not at teardown.
* `NFQUEUE` target present; `connmark`, `conntrack`, `mark` matches present;
  `xt_set.ko` and `ip_set_hash_ip/mac/ipmac/list_set` present; ipset v7.6 vs
  kernel protocol 6.
* `conntrack -E` streamed ~67 events in 5 s with no errors (runtime observation;
  no rules, modules or queues touched).
* `dns_probe=0`, `wan_dns_probe=0` -> firmware "connected" is link/DHCP only.
* No Entware (`/opt` -> empty `tmp/opt`, no `opkg`).
* GUI pins: `wans_routing_rulelist`, 17 entries.

## Original model results (local, `python3 -m unittest discover -s tests`)

69 tests, all pass.

| Invariant | Tests |
| --- | --- |
| Simultaneous first TCP/UDP/QUIC from one source -> one WAN | `test_creation` |
| Retransmitted/pending first packet keeps WAN; unconfirmed admission protected | `test_creation` |
| Quiet established TCP / long-lived UDP retained | `test_expiry` |
| Expiry only after last state gone; idle retention cancelled by new traffic | `test_expiry` |
| No expiry on truncated/error read or stale reconcile; expiry never preempts admission | `test_expiry`, `test_reconcile` |
| Duplicate/lost/reordered conntrack events tolerated | `test_events` |
| Validated empty vs truncated/miscounted/garbage dumps | `test_reconcile` |
| Pins (source/destination/subnet), local/VPN/incoming/already-marked excluded; only WAN bits changed; HTTPS/QUIC in scope | `test_exclusions` |
| Restart: single-WAN reconstruct, mixed-WAN/unattributed deferred, pinned ignored | `test_restart` |
| Health: firmware readiness, recovery hold != recovered, enhanced disappearance preserves exclusion, reassign only on known-unusable | `test_health` |
| Weighted ratio, affinity determinism, MAC-derived seed stability | `test_selection` |
| Randomized serialization stress (3 seeds) + recovery does not force return | `test_invariants` |
| Rejected mechanisms fail the same scenarios | `test_rejected` |

## Original unresolved items (superseded where noted by the test report)

1. NFQUEUE verdict carrying a packet mark, and `nfnetlink_queue` binding.
2. Packet-level confirmation of `CONNMARK --save-mark` immediacy.
3. IP set revision + expiry synchronization under load.
4. Hardware/software offload interaction and throughput.

## Stage-1 implementation update (2026-10-14, pi)

The on-router component requested by [INTEGRATION.md](INTEGRATION.md) is now
implemented locally. Additions since the loopback report:

* `src/wa_engine.{h,c}` - host-tested C decision engine (`make host-tests`:
  70 checks, 0 failures). Covers simultaneous-first-connection serialization,
  mark-bit preservation, all exclusion classes, provisional-admission
  retention, validated-dump-only expiry, lone-DESTROY safety, affinity return
  vs. sticky retention, and restart reconstruction.
* `src/wan_affinity.c` - the Linux adapter (NFQUEUE netlink, `conntrack -E`
  child, periodic `conntrack -L` reconcile, nvram, atomic status).
* `wan-affinity.sh` + config + `hooks/` + `BUILDING.md` + `Makefile` -
  the independent control/package surface, default disabled, single-client
  scope, readiness-gated activation and ownership-only cleanup.
* Python suite now 81 tests (`python3 -m unittest discover -s tests`), adding
  the Lifecycle controller suite over mock iptables/nvram/conntrack.

**Decision is unchanged: PARTIAL / NOT DEPLOYABLE.** The adapter was not
compiled in the development environment (macOS arm64, no Linux/aarch64
toolchain), so no runtime evidence exists for the production binary. Nothing was
installed, enabled, hooked, committed or pushed. See
[STAGE1-STATUS.md](STAGE1-STATUS.md).

## Original gate requirement

Provide one isolated Linux environment (throwaway container/VM/netns) with the
component's real packet path: bind a queue, verify the verdict-mark or the
IP-set approach, capture packets and conntrack marks, and re-run the
simultaneous-first-connection and expiry-overlap scenarios against the kernel.
At that point router mutation was not authorized. The user subsequently
authorized carefully scoped loopback tests; the report distinguishes their
results from production deployment readiness.

## Original read-only safety record

No live rule, module, queue, hook, package or persistent file was touched. The
router was inspected read-only. `wan-watchdog` is unchanged. Nothing was
committed or pushed.
