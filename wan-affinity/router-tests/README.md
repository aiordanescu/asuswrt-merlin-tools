# Router kernel tests (opt-in only)

Current release qualification is in
[QUALIFICATION-2026-10-15.md](../docs/QUALIFICATION-2026-10-15.md). These drivers
use SSH aliases `router` and `alexs-mac-mini`; the latter has a separate modem
interface, so client probes explicitly bind its LAN address. Review hostnames,
scopes, guards and supported firmware before adapting a driver to another site.

## Service, health and package drivers

Build from the component directory:

```sh
make router router-test router-health-tests bundle
```

- `run_mini_socket.py --allow-client-test --controlled-ports --evidence PATH`
  validates both policies against a private synthetic publisher with real
  HTTPS/DNS/UDP-443 egress. It temporarily removes the exact test client's GUI
  pin in RAM, then restores it and its routing rule. It also checks that a
  confirmed unreplied UDP flow is not retagged during failover. Test port ranges
  must have expired naturally before reusing them; the harness refuses conflicts.
  `--production-restart` is a separate opt-in to an independently guarded real
  publisher stop/start, not part of a routine run.
- `run_service.py --allow-client-test --evidence PATH` uses the production binary
  in private directories and one pinned client. It validates delayed firewall
  readiness, steady supervision, concurrent reconciliation, loss of owned rules,
  crash/stall recovery, hook editing, manual stop and bounded transfers after
  safe client adoption. Its independent guard restores the client pin even if
  SSH disappears. It does not rebuild the shared firewall.
- `run_install.py --allow-install-tests --evidence PATH` requires **no existing
  affinity installation**. It exercises persistent disabled installation,
  scoped startup, upgrade, manual and failed-upgrade rollback, disable and
  uninstall. It changes real startup hooks temporarily and compares their
  original bytes after removal. An independent uninstall guard is armed first.
  Do not run it against a deployed affinity service.

Service/package drivers require queue 20880 to be unused. They are deliberately
not part of unit tests or routine installation. Evidence stays in the requested
private directory. No blanket state flush, real WAN failure or NVRAM commit is
used. The health consumer executable `build/test_health_socket` exercises a
private Unix socket without installing a firewall rule.

## Admission stalls and blocked checkpoints

`run_stalls.py` uses the production executable, queue 20894 and synthetic UDP
from `127.88.0.2` to `127.88.0.1:58994`. The private controller copy changes its
chain hook from PREROUTING to OUTPUT and ingress to egress; a read-only iptables
shim supplies a virtual balance anchor. Actual queue operations, verdicts,
supervision, restart and ownership cleanup still use the real kernel and code.
No household admission rule, health state or WAN is faulted.

```sh
zig cc -target aarch64-linux-musl -static -O2 -Wall -Wextra -Werror \
  router-tests/nfq_probe.c -o /absolute/local/nfq-probe
python3 router-tests/run_stalls.py --allow-loopback-tests \
  --probe /absolute/local/nfq-probe --trials 12 --soak-seconds 90 \
  --evidence /absolute/private/evidence
```

An independent 900-second cleanup guard is armed first. A FIFO blocks only the
private checkpoint writer while new tuples and a confirmed echo flow continue.
The driver also checks recovery while the controller lock is held, repeated
single-packet freezes, a frozen idle helper followed
by one arrival, SIGKILL bypass, the three-stall circuit breaker, explicit restart
and production PID/rule/pin/hook preservation. Source ports are below Linux's
usual ephemeral range to avoid collisions with the router's own sockets.
It records each sample and median/p95/maximum rather than only an average. The
confirmed UDP flow spans the pressure and fault tests; the Mini adoption driver
separately checks preservation of real HTTPS sessions in both placement modes.
See [the qualification report](../docs/RECOVERY-2026-10-16.md).

## Sustained primary-WAN download (live service)

`run_throughput.py` requires an **already healthy, LAN-wide production sticky
service on queue 20880**, fresh socket health with both WANs eligible, and the
Mini's exact GUI source pin to WAN0. It is a site-specific driver using the
aliases above, Mini interface `en0`, a `192.168.1.0/24` LAN, firmware WAN0 mark
`0x80000000`, policy priority 100, and OVHcloud's Hillsboro HTTPS test file.
Review those assumptions before adapting it. It refuses surviving test-port
connections; allow previous states to expire naturally before another run.

From the component directory, explicitly authorize both client changes and
short full-speed traffic:

```sh
python3 router-tests/run_throughput.py --allow-client-test --full-speed \
  --evidence /absolute/private/evidence --duration 30
```

It temporarily removes only the Mini's GUI source pin in RAM, keeps its kernel
primary-WAN rule until affinity safely adopts existing connections, then removes
that exact rule. A five-second 100 Mb/s smoke check precedes two 10–30-second
downloads: test-port admission bypass, followed by normal affinity admission.
Both use the primary WAN. The bypass pre-marks only test connections to the
chosen server; the production helper, acceleration and other clients stay active.
The test can briefly consume available primary-WAN bandwidth and several GB
of data. Bodies are counted in memory and discarded.

The source-bound client creates a new small-page HTTPS connection once per
second during each download. Large-file requests are not used for those probes.
The host checks live health and queue drops every approximately two seconds,
records identified bulk hardware counters, and checks WAN connmark/NAT egress.
Any health deterioration, unexpected HTTP/curl failure or failed evidence check
aborts the comparison. Cleanup restores the exact pin and source rule and removes
the test mark rule; a separate router-local 600-second guard also does that if
the host disappears. Each client transfer has its own duration cutoff.

Before/after rule, pin, PID and hook comparisons and private client/router
evidence accompany the result. It does not deliberately fail a WAN, change
other GUI pins, restart a production process or flush states. See the
[measured results and limits](../docs/THROUGHPUT-2026-10-15.md).

The older bridge and full-lifecycle loopback drivers are described below.

These are explicit live-kernel diagnostics, not an installer or a production
daemon. They are never run by the ordinary unit suite. The first runs were
authorized on the RT-AX88U on 2026-10-14 after the original read-only review.

The tests use only synthetic UDP from `127.88.0.2` to `127.88.0.1:58888`, an
otherwise unused NFQUEUE 58888, and a unique owned mangle chain. No LAN ingress
jump, WAN transition, RPDB change, persistent hook or blanket conntrack flush
is used. A router-side 180-second cleanup guard is armed before linking the
test chain. Normal cleanup also runs in a host-side `finally` block. Cleanup
removes only exact test rules, the owned chain and the dedicated test flows.

`nfq_probe.c` is a small static C netlink bridge and bounded synthetic traffic
generator. It binds a queue, requests packet copy and queue-full fail-open,
and exposes packet metadata over an SSH pipe. `run_loopback.py` runs the
prototype Python engine on the Mac and returns its marks to that bridge.
This transport does not demonstrate that a production helper runs on the
router, nor does loopback demonstrate WAN forwarding/offload throughput.

The bridge understands `V id hexmark` (NF_ACCEPT) and `R id hexmark`
(NF_REPEAT). NF_ACCEPT experimentally skipped the rest of the mangle table,
so the positive test uses NF_REPEAT with a WAN-mark guard to avoid requeueing.
The following masked CONNMARK save and mark/connmark counter matches prove
same-pass persistence. Do not copy this test chain into the production adapter:
its loopback policy domain and sentinel marks are deliberately synthetic.

The delayed-packet case intentionally holds a real first packet unconfirmed
for three seconds. A confirmed-table dump is empty while that packet is held.
This exposed and then verified the fix for the model's unsafe two-second
provisional-state expiry.

The failure tests distinguish two cases:

- When the queue is full with fail-open enabled, additional test packets bypass.
- When the helper dies, newly arriving test packets bypass the absent queue,
  but packets already held by the destroyed queue are dropped. Fail-open does
  not guarantee that already-queued packets survive a crash.

## Reproduction prerequisites

1. Obtain explicit permission for the chosen router and loopback test scope.
2. Capture current `iptables-save`, IP rules/routes, module/queue state and
   hashes of production scripts locally. Do not save a full secrets-bearing
   NVRAM dump. The harness needs `iptables-save.before.txt`,
   `ip-rule.before.txt` and `scripts.before.json` in its backup directory.
3. Build `nfq_probe.c` for the router's CPU/libc. The recorded run used a
   SHA256-verified official Zig 0.15.2 macOS aarch64 toolchain and:

   ```sh
   zig cc -target aarch64-linux-musl -static -O2 -Wall -Wextra -Werror \
     router-tests/nfq_probe.c -o /absolute/local/backup/nfq-probe
   ```

4. Copy only that binary into a new owner-only
   `/tmp/wan-affinity-test-YYYYMMDDTHHMMSS/` directory. No router package install
   is required. Probe an unoccupied queue first; do not unbind another user's
   queue or issue global PF unbind commands.
5. Review the source and invoke explicitly from the component directory:

   ```sh
   python3 router-tests/run_loopback.py --allow-router-loopback-tests \
     --backup /absolute/local/backup \
     --remote /tmp/wan-affinity-test-YYYYMMDDTHHMMSS
   ```

The harness records JSON and logs, checks exact firewall rules while ignoring
changing counters, compares routing rules and production script hashes, and
checks for remaining test chains/queues. Exit 2 denotes a reproduced pending
admission lifecycle defect; infrastructure/assertion errors exit nonzero too.
PASS refers to the listed loopback scenarios, not full production readiness.

## Full-lifecycle and nonblocking-refresh regression

The separately built `WA_TESTING` helper uses the same engine and collector as
the normal build, with a fixed loopback domain and optional bounded fault
injection. `run_lifecycle.py` never links a LAN rule or changes a WAN. It arms
an independent 180-second cleanup guard before using its unique OUTPUT chain
and queue 58888. It requires that its dedicated flow tuple/queue be unused.

Build the test helper with `make router-test`, and build the diagnostic traffic
generator from `nfq_probe.c`. Then explicitly invoke:

```sh
python3 router-tests/run_lifecycle.py --allow-loopback-tests \
  --backup /absolute/private/backup \
  --binary /absolute/local/wan-affinity-test \
  --generator /absolute/local/nfq-probe
```

It checks never-confirmed DROP cleanup; four-second held-packet retention and
subsequent cleanup; 360 admissions plus 72 real UDP echo round trips during a
1.5-second refresh delay; worker timeout and stale-readiness behavior; and
helper-crash queue/worker cleanup. `--fault-only` rechecks just the timeout/
staleness/crash cases. Records remain in the supplied private backup.

The diagnostic generator's `roundtrip COUNT BASEPORT PORT` command reports
average, p95 and maximum echo latency. Locally generated packets do not always
have an NFQUEUE timestamp, so loopback timing uses those actual round trips.
See [the measured results](../docs/LIFECYCLE-FIX-2026-10-15.md).

## Mixed-session adoption beside the production service

`run_adoption.py` requires the authorized `alexs-mac-mini` client and its exact
Primary GUI pin. It refuses occupied private queues and test port ranges.
It arms an independent router-local cleanup guard before temporarily removing
that client's pin. Production affinity and watchdog remain running.

```sh
python3 router-tests/run_adoption.py --allow-client-test \
  --evidence /absolute/private/evidence
```

For each mode it keeps two real HTTPS connections alive on different WANs,
opens 16 concurrent new HTTPS connections, restarts the private helper while
members survive, and checks two further new connections. Old kernel identities,
marks and NAT addresses must remain exact. Queue 20881 and selected source
ports enter a private chain; `WA_TESTING` limits its observations to those ports.
Cleanup verifies restored pins, rules and hooks. Firewall comparisons ignore
only live chain counters and generated timestamp comments. The driver never
flushes conntrack or stops a production service. Allow its test ports to expire
before another run. See the [adoption report](../docs/ADOPTION-2026-10-15.md).

## Queue fallback regression

`run_queue_fallback.py --allow-router-loopback-tests --probe PATH --evidence PATH`
uses the separately built `nfq_probe.c` tool, a dedicated loopback UDP port and
an unused queue. It checks whether later mangle rules run during absent-listener
and queue-full bypass, then removes its objects and compares the firewall,
policy rules and production PIDs. It does not stop the production helper.

`run_off_switch.py --evidence PATH` starts a private helper/controller with a
synthetic loopback LAN visible only to that fixture. It checks disable and
reconciliation with invalid, missing and syntactically broken configurations,
then compares production processes, rules and firewall state.
