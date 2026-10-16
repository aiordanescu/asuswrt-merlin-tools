# Low-severity review fixes — 2026-10-16

WAN Affinity 0.4.6 and this watchdog update close the 22 Low findings left open
by the local review's Round 4. The earlier stale-configuration precedence
finding was already closed in Round 3 and is excluded from these 22. Findings
that 0.4.5 had already addressed were checked again; their original reports
remain useful regression cases.

## Disposition

| # | Finding | Resolution and regression coverage |
| --- | --- | --- |
| L01 | Status truncates mappings and hides counts | Size output for all 1,024 associations; retain counts and supervisor heartbeat ahead of mappings. A smaller caller buffer emits complete lines plus an explicit truncation count. C tests cover full capacity and small buffers. |
| L02 | Refused hooks leave files and a stopped service | Stage and syntax-check both hooks with automatic cleanup; installer preflight precedes stop. Rollback restores monitoring even if a hook is refused. Private installer tests cover a refused second hook, Bash options and BusyBox shell shebangs. |
| L03 | Synchronous, duplicate checkpoint writes | Already fixed in 0.4.5: a separate bounded worker saves coalesced checkpoints. Scheduling tests and the private blocked-writer/queue-recovery driver protect this behavior. |
| L04 | Probe utility's unsigned netlink iteration | Already fixed in 0.4.5: the probe uses the checked shared frame iterator. The engine's short/final-frame tests and rebuilt router probe cover it. |
| L05 | Invisible NFQUEUE overruns | Already fixed in 0.4.5: `queue_enobufs` counts `ENOBUFS` and is included in status. Queue recovery and status checks retain this diagnostic. |
| L06 | Unstable IDs in unconfirmed dumps | Ignore unconfirmed kernel IDs. Only confirmed rows establish kernel identity; tuple-based U-to-C merging and membership retention remain intact. C parser and migration tests cover this. |
| L07 | Epoch and reconstruction-capacity divergence | New C associations advance the decision epoch. Both models refuse partial reconstruction above 1,024 relevant sources, retain connections, and report `reconstruct-capacity`. C and Python tests cover overflow. Binding IDs remain opaque identifiers independent of later decision epochs. |
| L08 | Missing facts look fresh | `fact_age_ms=-1` means unavailable or invalid; nonnegative values are real ages. Host decision tests and a blocked-adapter router check cover missing facts. |
| L09 | Runtime privacy not enforced | C startup and shell lifecycle tools enforce directory mode 0700, with umask 077 and private logs. Tests start from an existing permissive directory. |
| L10 | Listener failure floods logs; impossible socket path accepted | Reject paths that cannot fit `sockaddr_un.sun_path` before startup. Log the initial failure once and retry with exponential backoff capped at five minutes; status exposes publisher readiness. A private obstruction/removal test checks recovery. |
| L11 | Raw sockets receive all ICMP types | Install an echo-reply-only `ICMP_FILTER`; retain token, address, checksum, sequence and deadline checks. Router tests inspect the kernel filter and exercise reply isolation. |
| L12 | Shell DDNS job table can grow | A short foreground subshell detaches each bounded worker, so the long-lived shell does not retain background job records. Routing lock descriptors are closed in workers. Shell tests check completion, throttling and an empty job table. |
| L13 | Watchdog worker/main-loop and protocol test gaps | Host tests now exercise the actual worker and portable decisions used by the main loop: lease capture/invalidation, freshness, shadow recovery and routing acknowledgements. Linux socket tests cover the seventeenth subscriber, client-data disconnect and rejection of a different UID. File-only router maintenance tests remain available for the complete loop. |
| L14 | Stale sanitizer guidance and broken archive links | Sanitizer instructions already reflect normal inlining. Bundle creation now rewrites links to shipped files or immutable source-tag URLs and refuses missing source targets. Both archives are checked after extraction. |
| L15 | Multiline settings truncated at handoff | Decode NUL-delimited environment records without converting embedded newlines into record boundaries. Preserve literal allowlisted values with shell quoting; unknown records never become assignments. Host and BusyBox cases cover multiline LAN/DNS settings and a false assignment inside an excluded value. |
| L16 | Equivalent enabled forms fail upgrade/rollback restart | Use the actual sourced enabled value. Resume enabled intent across a supervisor restart gap; retain an explicit manual-stop marker. Tests cover quotes, export, whitespace, comments, no current helper PID and rollback. |
| L17 | First installation cannot retry after rollback | A completed shell rollback permits another `--install`, retaining prior recovery snapshots. An armed or incomplete recovery still blocks a competing handoff. Capture policy before creating the installation tree. Lifecycle tests perform install, rollback and reinstall. |
| L18 | Reused PID permanently blocks recovery | If the routing lock is free, discard only the stale PID record. Preserve the unrelated process. A held routing lock still refuses handoff. Lifecycle tests cover both cases. |
| L19 | Configuration grammar differs; runtime setting ignored | The helper accepts a literal shell-assignment subset, and the controller checks it before sourcing. Quotes, export and comments work consistently; expansion, commands, escapes, multiline values and CRLF are refused. Controller runtime is fixed at `/tmp/wan-affinity`; conflicting `STATUS_DIR` values are refused and the setting is removed from controller examples. |
| L20 | README confuses test counts and evidence age | Separate host process tests from router shell mocks, give explicit test patterns, and label the original router evidence historical. This report records the new run. |
| L21 | DDNS uses persisted selection after watchdog exit | Postconf and `--address` require both the held routing lock and a main-loop heartbeat no older than 30 seconds. Progress refreshes even when messages are unchanged or provider requests are throttled. Missing ownership returns selection to firmware behavior. |
| L22 | Killed Namecheap checker leaves permanent mkdir lock | Use a stable flock file, released by the kernel on exit. Close its descriptor throughout the DNS pipeline and service child. A SIGKILL test leaves the first lookup alive and confirms a second checker can run; historical stale lock directories are harmless. |

## Reproduce

Run from the repository root on the build host:

```sh
make -C wan-affinity host-tests sanitize
python3 -m unittest discover -s wan-affinity/tests
make -C wan-watchdog host-tests
python3 -m unittest discover -s wan-watchdog/tests
python3 -m unittest discover -s namecheap-ddns/tests
```

The Affinity host run has 119 Python tests, 299 engine/checkpoint C checks,
21 worker checks, and separate configuration and supervisor/scheduling suites.
The watchdog host run has 88 shell/DDNS/lifecycle tests, 11 adapter/parity tests,
434 core/codec C checks, 21 worker checks and the runtime-decision suite. The
Namecheap SIGKILL regression is a separate test. Host tests mock firmware
commands and never change network state.

Build router binaries using the optional instructions in
[Affinity BUILDING.md](wan-affinity/BUILDING.md) and the
[watchdog guide](wan-watchdog/GUIDE.md). Then run the scoped router checks:

```sh
python3 wan-watchdog/router-tests/run_low_review.py \
  --evidence /path/to/private/evidence
python3 wan-watchdog/router-tests/run_maintenance.py \
  --evidence /path/to/private/maintenance-evidence
```

The review driver uses private files, loopback ICMP and mock routing. It runs
675 socket checks (including a real different-UID child), 17 probe checks,
the C worker/runtime/engine suites, configuration parsing, missing-fact status,
runtime privacy, listener obstruction/recovery and NUL policy capture. It also
runs the current 39 watchdog and 30 DDNS shell tests through BusyBox. It checks
production process identities, hooks, GUI pins, routing policy and firewall
rules before and after. It does not fail a physical WAN or flush household
connection state.

For admission recovery, use the opt-in loopback driver documented in the
[recovery report](wan-affinity/docs/RECOVERY-2026-10-16.md). Previously published
throughput and physical-outage observations remain historical evidence; these
fixes do not claim new throughput or outage qualification.

## Router qualification for this revision

The private monitor upgrade and rollback both retained pinned mode and priority
78. The loopback admission run used three single-packet freezes and three
SIGKILL exits: queue closure took 5.47–5.80 seconds and full reactivation
6.88–7.08 seconds. Fresh echoes after SIGKILL took 0.143–0.157 ms. The confirmed
flow sent 2,030 packets with zero lost. A blocked checkpoint writer and a held
controller lock did not prevent independent queue recovery. These are scoped
regressions, not a new general latency or throughput guarantee.

Guarded updates on the RT-AX88U / Merlin 3004.388.12_2 qualified both WANs and
Affinity 0.4.6 in sticky mode. Routing policy, firewall rules, GUI pins, hooks
and Affinity configuration were preserved; each component upgrade preserved
the other component's PID. The separate DDNS and Namecheap scripts were updated
after verifying their old files against the project version. The DDNS live-owner
check passed without forcing a provider update. Eight LAN-bound HTTPS/DNS/QUIC
checks from the Mac mini passed. Production soak duration and results accompany
the release in `BUILD-INFO.json`. No WAN was deliberately failed.
