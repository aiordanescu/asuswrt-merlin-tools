# Stage 1 - implementation status

> For current operations and evidence, see the [README](../README.md) and
> [qualification report](QUALIFICATION-2026-10-15.md).

> Historical record. Subsequent scoped forwarding tests compiled and exercised
> the ARM helper, repaired startup/cleanup and packet framing, and identified
> a remaining admission-lifecycle blocker. Current status is in
> [STAGE2-3-RESULTS-2026-10-14.md](STAGE2-3-RESULTS-2026-10-14.md).

Date: 2026-10-14. Author: pi. Scope: build the on-router component locally,
without installing or enabling anything on the live router.

## Delivered

* `src/wa_engine.{h,c}` - the decision engine, no Linux dependency. Serialized
  admission, sticky/affinity selection with live weights, firmware + optional
  snapshot health, provisional-admission retention, framed validated
  reconciliation, event handling, restart reconstruction, bounded status.
* `src/wan_affinity.c` - the platform adapter: NFQUEUE netlink bind/verdict,
  `conntrack -E` child, periodic `conntrack -L` dump, nvram reads, atomic
  status file.
* `wan-affinity.sh` - controller with `start|status|stop|disable|cleanup|uninstall`,
  exact ownership of one chain and one jump, single-client scope enforcement,
  readiness checks before rule insertion, and rollback on partial failure.
* `wan-affinity.conf.example`, `hooks/README.md`, `BUILDING.md`, `Makefile`.
* Tests: `tests/c/test_engine.c` (70 checks) and `tests/test_lifecycle_script.py`
  (6 tests, mock iptables/nvram/conntrack). Python suite is now 81 tests.
* One documented affinity hash shared by both implementations: FNV-1a 64 over the
  seed text then the address bytes. Golden vectors are asserted in
  `tests/test_selection.py` and `tests/c/test_engine.c`, so the model and the
  engine cannot drift apart silently.

## Verified on this host

```
make host-tests                          -> 70 checks, 0 failures
python3 -m unittest discover -s tests    -> Ran 81 tests ... OK
sh -n wan-affinity.sh                    -> OK
python3 -m compileall wan_affinity tests -> OK
```

## Not verified here (blocking)

* `src/wan_affinity.c` was **not compiled**: this host is macOS arm64 with no
  Linux/aarch64 toolchain (`zig` is not installed). The exact build command is
  in `BUILDING.md`; build it before any on-router use.
* The adapter's runtime behavior (queue bind, verdict marks, conntrack parsing,
  restart, crash) is exercised only by the loopback diagnostics in
  `router-tests/`, not by the production adapter.

## Not done here (deferred to stages 2-3)

* No installation, no enabling, no persistent hooks, no WAN toggling, no
  broadened scope, no watchdog publisher change, no commit or push.
* No LAN forwarding, offload or throughput qualification.

## Design points carried from the router report

* `NF_REPEAT`, not `NF_ACCEPT`, for both mark and pass verdicts; the `0x40`
  sentinel is cleared on re-entry and the firmware `balance` rule then runs.
* Provisional (queued/unconfirmed) admissions are never expired by time alone;
  they clear only when a validated dump confirms the connection, and their
  absence is reported as visible uncertainty.
* A lone `DESTROY` never expires an association; expiry needs a validated dump
  showing no relevant states.
* Invalid/truncated/error dumps retain prior state and inhibit expiry.
