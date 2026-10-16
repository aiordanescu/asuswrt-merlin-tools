# Integration boundaries and the 3-stage handoff

> Historical handoff notes. Current operation and qualification are described
> in the [README](../README.md) and
> [2026-10-15 qualification report](QUALIFICATION-2026-10-15.md).

> Native health update (2026-10-15): [HEALTH-SOCKET.md](HEALTH-SOCKET.md)
> supersedes the proposed text publisher below. Historical stage boundaries
> describe earlier work; current user authorization also covers the native
> watchdog implementation and guarded qualification. Wider affinity enablement
> remains a separate rollout gate.

> Follow-up (2026-10-15): the unconfirmed-flow cleanup and blocking-refresh
> issues described below were fixed and tested. Current details:
> [LIFECYCLE-FIX-2026-10-15.md](LIFECYCLE-FIX-2026-10-15.md). Other rollout
> qualification gates remain. The earlier findings are preserved as history.

This section records the seams between stages so scope stays explicit.
**Current status:** scoped stage-2 forwarding tests have run and were cleaned
up. The cleanup and refresh-latency issues were fixed and checked in the
2026-10-15 follow-up. Stage 3 remains partial for sustained throughput/offload,
reboot/firewall/upgrade lifecycle and the wider policy matrix. See
[STAGE2-3-RESULTS-2026-10-14.md](STAGE2-3-RESULTS-2026-10-14.md).
The stage-1 notes below preserve the handoff history.

## Agreed next stages and handoff

The user will have **pi implement stage 1**, then return to **Codex for stages
2 and 3**. The earlier model/loopback gate is partial; it does not authorize
LAN-wide enablement. Before implementing, read
[the actual router-test findings](ROUTER-TESTS-2026-10-14.md), especially the
NF_ACCEPT/re-entry correction and the removal of provisional-state TTL expiry.

### Stage 1 — pi: build the on-router component

- Replace documentation-only kernel bindings with a real, self-contained
  on-router helper. The model must no longer require a Mac/SSH decision engine.
  The static aarch64 C diagnostic proved that queue binding and verdict marks
  do not require Entware or a firmware rebuild.
- Integrate serialized admission, corrected packet re-entry/mark persistence,
  conntrack identities/events/validated dumps, safe pending-state retention,
  bounded resource use and visible uncertainty. Supply tests against both the
  model contract and the real adapter's failure boundaries.
- Implement `sticky` as the default policy and selectable persistent
  `affinity`, including their distinct recovery behavior and live WAN weights.
- Implement native standalone readiness plus optional watchdog integration,
  with recovery-aware admission and conservative stale-snapshot handling.
  No independent quality-probe engine or mandatory watchdog dependency.
- Preserve manual GUI pins, local/VPN/incoming exclusions, unrelated marks,
  existing connections and the watchdog's emergency routing authority.
- Provide inspectable start/status/stop/cleanup controls, exact ownership,
  bounded/fail-open queue handling, safe disable and an explicit **single-client
  test scope**. Default policy does not imply automatic activation for the LAN.
  Prepare test/build instructions and any package/hook source without enabling it.
- Stage-1 completion means a buildable implementation and passing applicable
  tests, with binaries/config examples and known limitations handed back.
  **Do not install/enable it on the live router, add persistent hooks, modify
  the production watchdog, toggle WANs or broaden test scope in this stage.**
  If an additive watchdog publisher is needed, prepare its code/tests separately
  without deploying it; standalone operation must remain complete.

  *Stage-1 status: implemented as `src/wa_engine.{h,c}`, `src/wan_affinity.c`,
  `wan-affinity.sh`, config/build/hook sources and tests. The engine and the
  controller are host-tested; the Linux adapter is written but was not compiled
  in the development environment (no aarch64 toolchain). See
  [STAGE1-STATUS.md](STAGE1-STATUS.md) for the exact commands and caveats.*

### Stage 2 — Codex: one-device forwarding validation

- Review pi's implementation and findings before executing it. Back up current
  state again; use temporary exact rules and independent timed cleanup.
- Apply only to one explicitly selected unpinned test client. Verify actual
  WAN egress for TCP, UDP/QUIC, HTTPS, DNS and unrelated destinations.
- Exercise quiet connections, sticky expiry, concurrent creation, delayed
  confirmation, helper failure/restart and persistent-affinity recovery.
- Confirm GUI pins and VPN/local/incoming traffic retain their precedence.
  Simulate health changes only inside the scoped component test policy; never
  disable either real WAN or force whole-LAN watchdog failover to test this.
- Remove test rules/resources and compare production state afterward. Report
  failures before expanding scope; other clients and router management stay out.

### Stage 3 — Codex: performance, packaging and rollout readiness

- Measure scoped admission latency/rate and forwarding/offload behavior without
  globally disabling acceleration or interrupting existing sessions.
- Validate resource limits, full/absent queues, event loss, incomplete dumps,
  mode/config transitions, safe disable and exact ownership cleanup.
- Complete/review installation, upgrades, backup/rollback, hook coexistence,
  uninstall and documentation. Validate restart/firewall/reboot lifecycle in an
  isolated or explicitly scheduled environment; do not reboot/rebuild the live
  router just to complete a checklist.
- Broader enablement and publishing follow successful qualification and the
  user's rollout direction. They are not part of pi's stage-1 handoff.

## Health source

* Independent native-readiness monitoring: `wanN_enable`, `wanN_state_t`,
  lease/gateway/interface, route readiness. No probing, no quality claim.
* Optional versioned snapshot consumed from `wan-watchdog`. The publisher is a
  small, additive write in the watchdog's status path (does not change any
  watchdog decision). The component runs standalone without it.
* Explicit recovery-aware admission eligibility and persistent exclusions when
  enhanced monitoring disappears.

### Live watchdog-stop check

- [x] The guarded 2026-10-15 socket qualification actually stopped and restarted
  the production publisher while both `sticky` and `affinity` forwarded on the
  authorized Mac mini. Baseline, publisher absence and fresh return passed
  48 HTTPS/DNS/QUIC probes in total.
- Independent timed restart guards preserved the ability to recover without
  the SSH session. Shared routing state stayed installed; temporary client
  objects and its original pin were restored afterward.
- The separate synthetic matrix verified that disappearance/staleness cannot
  recover an excluded WAN, and that sticky and affinity retain their distinct
  recovery behavior. See [the native qualification report](../../wan-watchdog/QUALIFICATION-2026-10-15.md).

## Identity and selection

* MAC-derived router seed for **IP-keyed** affinity: `derive_seed(router_mac)`.
  This is a salt for the weighted hash; identity remains the LAN IP. A device
  preference still does not follow a device across an IP change - that would be
  a separate MAC-keyed feature.
* Live weights read from `wans_lb_ratio` (currently `2:1`); GUI changes are
  picked up, not snapshotted at start.
* Own persistent directory `/jffs/wan-affinity` (seed, config) and own runtime
  directory `/tmp/wan-affinity` (status, events). No high-frequency flash writes.

## Ownership and packaging

* Own mangle chain and its own write-ahead ownership record; never flush shared
  chains or delete other add-ons' rules.
* Preserve the `firewall-start` T-Mobile TTL rule and other hooks; reconcile
  idempotently after firewall/WAN rebuilds.
* Standalone `wan-affinity.sh status|disable|uninstall` controls.
* The static aarch64 helper was built and tested without Entware. Resource/
  performance limits and general packaging/lifecycle remain unqualified.

## Explicit non-goals

* No inbound-service migration; no guarantee sessions survive a real WAN
  failure.
* No firmware rebuild.
* No watchdog publisher changes or full installer in this gate.

## Stage-1 artifacts (this stage)

The adapter's control surface is complete for review: `wan-affinity.sh
{start|status|stop|disable|cleanup|uninstall}` owns exactly one mangle chain and
one PREROUTING jump, requires a single-client scope, checks native readiness
before inserting rules and rolls back on partial failure. The helper is
`src/wan_affinity.c`; the decision engine it calls is `src/wa_engine.c`. Build
instructions and the current (uncompiled-here) caveat are in `BUILDING.md`.
