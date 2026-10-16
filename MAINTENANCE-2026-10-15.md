# WAN tools maintenance: lifecycle and routing safeguards

Release: `wan-tools-2026.10.15-beta.5`, with follow-up fixes in `beta.6` and `beta.7`; WAN Affinity version `0.4.4`.
Target: RT-AX88U, Asuswrt-Merlin 3004.388.12_2, Linux 4.1.51 aarch64.

This update addresses the preliminary review's lifecycle, configuration,
watchdog scheduling and diagnostic findings. Sticky/affinity selection,
OPNsense-style adoption of new connections, the 30-second loss window and the
two-minute recovery policy remain as documented.

## Changes

- **Affinity off switch:** `disable` removes owned admission rules and stops
  the helper before saving `ENABLED=0`. It does not source or validate the
  activation configuration. It works with invalid values, broken shell syntax,
  a missing config and SSH from the scoped client. Reconciliation recognizes a
  literal disabled switch before executing a broken config.
- **Hook interpreters:** hook installation preserves an existing shebang and
  shell options. Syntax is checked with that interpreter. Install/remove is
  covered with a Bash array/conditional hook as well as POSIX shell hooks.
- **Watchdog configuration:** handoff captures supported settings from the
  running owner into private `service.conf`, including pinned mode and custom
  priorities. It copies neither the complete environment nor credentials.
  Starts, upgrades and rollback reuse that file.
- **Verified recovery:** install and rollback wait for both the previous owner
  and its inherited lock. Rollback rejects a foreign live owner, discards stale
  PID records, and requires the replacement's process, lock and monitoring
  output before declaring success. Recovery uses a kernel-released flock
  instead of a persistent directory mutex.
- **Coordinated upgrade:** a 90-second installer budget precedes its independent
  120-second guard. Success and automatic rollback atomically claim a per-update
  transaction. `--upgrade` snapshots the previous program and saved policy;
  manual rollback remains available after successful installation.
- **Lease-bound decisions:** the watchdog captures expected facts and health
  flags when making a decision. Configuration changes discard pending intent;
  worker launch cannot rebind old intent to new facts. The adapter still checks
  the current lease, interface, gateway and probe targets before mutation.
- **Fact freshness:** age remains measured from collection start. Its bound is
  11 seconds, covering two serialized five-second workers and the probe
  deadline. This permits valid slow workers without falsely refreshing old
  facts. Worker failures invalidate the view; status exposes fact age and
  stale-cycle counts.
- **Health diagnostics:** successful shadow inspections clear recovered errors.
  A preserved routing override remains marked unconfirmed after restart until
  an actual successful action acknowledges it.
- **Shell reference:** probe waiting names only probe PIDs, so DDNS background
  work cannot join that wait. Every LAN CIDR is validated before sorting; an
  invalid entry fails instead of returning a truncated scope.
- **Tests and fallback wording:** large test engines use static storage so the
  ordinary ASan/UBSan target no longer needs an inlining workaround. Remaining
  router adoption checks use the current status key. Release publication occurs
  before updating the default branch's download links.

## Queue fallback: corrected guarantee

When the running Affinity helper declines admission, it returns the packet to
firmware balancing. Kernel absent-listener and queue-full bypass instead accept
the packet without executing later rules in the current mangle hook. Remaining
routing policy selects its path; neither weighted balancing nor sticky placement
is guaranteed in that case. Already queued packets can be lost when a listener
exits. A missing helper's abandoned admission jump is removed before restart;
`stop` and `disable` restore ordinary firmware traversal by removing that jump.

The router's kernel was checked using a dedicated loopback UDP chain and an
unused test queue. The production helper continued running:

| Condition | Result | Later mark-rule packets |
| --- | --- | --- |
| No listener, three new flows | Connections confirmed with connmark 0 | 0 |
| Queue capacity eight, 64 new packets | Eight held, 56 bypassed; bypassed connmarks 0 | 0 |
| Queue rule removed, three new flows | Connections confirmed with test connmark 512 | 3 |

These observations establish hook traversal on the tested vendor kernel. They
do not claim that every unmarked client uses a particular WAN; policy routes can
still select another table.

## Validation

Portable checks cover the real shell functions/controllers with mocked networking,
real host process/lock handoff fixtures, C decision/checkpoint/worker tests,
shell/C classification parity and address/undefined-behavior sanitizers.
The suites pass 107 Affinity Python tests, 78 watchdog shell/DDNS/lifecycle
tests, 11 monitor adapter/parity tests, 434 core/codec C checks, 270 Affinity
engine/checkpoint C checks and 21 worker checks.

`wan-watchdog/router-tests/run_maintenance.py` runs the actual C monitor with
loopback ICMP and file-only routing adapters. It checks:

- A queued decision across a disabled-WAN/new-address transition produces no
  stale action on the replacement lease.
- Repeated four-second workers produce eligible WANs without freshness-related
  UNKNOWN cycles.
- A worker timeout is reported and a subsequent successful shadow inspection
  clears the error.
- A preserved override stays unconfirmed through early restart warm-up, then
  becomes acknowledged after a successful routing action.

The actual router controller also passes invalid-value, invalid-syntax,
missing-config and disabled-broken-config off-switch cases using a private
loopback-only synthetic LAN. Real router install/rollback of a separate
file-only service preserves pinned mode and custom priority 78.

Existing real-clock orchestrator tests cover whole-LAN and pinned recovery,
failed restoration, emergency reversal, monitor stalls and hung workers.
These use private runtime directories and file-only actuators. Production
processes, hooks, pins and rules are checked around scoped tests.

The guarded production update installs the same qualified executables, waits
for eligible health, and checks LAN-bound HTTPS, DNS and QUIC forwarding from
the Mac mini. No reboot, physical WAN outage, blanket conntrack flush or global
firewall rebuild is part of this maintenance qualification. The qualification
remains limited to the listed router and firmware.

## Review disposition

| Preliminary finding | Resolution |
| --- | --- |
| 1. Download links before release | Publish/verify the tagged assets before advancing `main` |
| 2. Sanitizer test stack overflow | Static test engines; normal sanitizer target |
| 3. Hook shebang replacement | Preserve and validate declared interpreter |
| 4. Emergency disable validation | Stop independently; recognize disabled config before sourcing |
| 5. Queue fallback claim | Correct docs, remove abandoned jump, verify kernel traversal |
| 6. DDNS joins probe wait | Wait only for recorded probe PIDs |
| 7. Handoff loses settings | Capture allowlisted running settings; persistent private config. Completed in beta.6 (see follow-up) |
| 8. Stale PID accepted by rollback | Verify replacement process, ownership and output. Completed in beta.6 (see follow-up) |
| 9. Inherited lock race | Bounded wait for shared lock before handoff |
| 10. Installer/guard race | One shorter deadline and atomic transaction claim |
| 11. Invalid LAN list truncated | Validate complete scope before emitting it |
| 12. Old adoption status key | Update remaining router driver |
| 13. Stale rollback directory mutex | Kernel-managed flock |
| 14. Old lease intent rebound | Capture decision provenance and invalidate on config reset |
| 15. Slow-worker freshness | Conservative age with a cadence-aware bound and counters |
| 16. Shadow error latches | Clear on a valid recovered inspection |
| 17. Unacknowledged restart overlay | Retain uncertainty until an acting acknowledgement |

## Follow-up in beta.6: rollback confirmation and first-handoff capture

A second review found that two of the fixes above were incomplete. Both are
fixed in `wan-tools-2026.10.15-beta.6`; the beta.5 bundle does not contain these
fixes. Beta.6 ships the beta.5 executables unchanged; only the watchdog
installer, service library and documentation differ.

- **Shell rollback confirmation (finding 8).** `ww_shell_ready` looked for a
  status line beginning `mode=`, but the shell's `write_status` begins
  `uptime_seconds=N mode=...`. Every rollback to the shell restarted it
  correctly, then reported `replacement monitoring not confirmed` after 45
  seconds and exited 1. Readiness now matches the shell's actual first line
  (with mode `whole_lan` or `pinned`) and both WAN lines. The monitor's own
  status, which begins `monitor=1`, does not match.
- **First-handoff capture (finding 7).** An existing `service.conf` was copied
  as-is even when the previous owner was the shell, skipping the running
  process's settings and its recorded mode. A file left by an earlier rollback
  could therefore restore an old mode or priority. `--install` from a shell now
  always captures from the running process. `--upgrade` from a monitor keeps
  the installed `service.conf`, which is the monitor's actual configuration.

The lifecycle fixture's fake shell now writes the real status format. New
tests run the real `write_status` against the readiness check, roll back a
first install to the shell, and cover both capture sources. The watchdog suite
now passes 82 shell/DDNS/lifecycle tests; the three new rollback and capture
tests fail against the beta.5 scripts. This follow-up was validated with host
tests only, not on the router.

## Affinity 0.4.4 in beta.7: capacity limits

The second review found two undocumented capacity limits in WAN Affinity that
stopped placement for the whole LAN.

- **Routing domain.** Local prefixes had 24 slots, and the 8 reserved ranges and
  the LAN used 9 of them. That left 15 for main-table routes, and every route
  counted, including duplicates and subnets of ranges already listed. The
  tested router used 12. GUI pins and policy exclusions shared 64 slots.
  Exceeding either made the refresh worker exit on every cycle, so health went
  stale and the helper could not start. Entries covered by a broader entry are
  now merged and use no capacity, and the limits are 64 local prefixes and 128
  policy selectors. On the tested router the same routes and rules use 11 and
  20. If the domain still can't be represented exactly, the helper keeps
  refreshing and pauses placement: new connections use firmware balancing and
  status reports `domain=local-capacity`, `pin-capacity`, `unsupported-pin` or
  `truncated-rule`. A partial domain could otherwise send local or VPN traffic
  to a WAN.
- **Connection view.** Every LAN-sourced TCP/UDP conntrack row counted toward
  4,096, including DNS to the router and LAN-local traffic. One more row rejected
  the whole refresh, which made WAN facts stale and stopped failover decisions
  and expiry. The view now holds only connections the helper could place. If
  those still exceed 4,096, the refresh keeps WAN facts current but neither
  reconciles nor expires mappings, since a partial view cannot prove a source
  idle. Status reports `view_overflow` and `last_error=view-capacity`.

The status file has a new `capacity` line with the domain state, local prefix
and policy selector counts, placement passes caused by an incomplete domain,
and view rows and overflow. The engine's decisions and the mapping checkpoint
format are unchanged.

### Validation

Host: 289 engine/checkpoint C checks (also under ASan/UBSan), including the
tested router's main-table shape, capacity limits, covering-prefix
replacement, GUI pin and policy-rule parsing, and view filtering and overflow;
21 worker C checks and 107 Affinity Python tests. Router, with the production
service running: the loopback lifecycle regression (never-confirmed cleanup,
held-packet retention, 360 admissions during a slow refresh, worker timeout and
crash cleanup) and the mixed-session adoption matrix in both modes with real
HTTPS connections. In that run the test helper read the router's real routes,
rules and pins and reported `domain=ok local_prefixes=11/64
policy_selectors=20/128`. Firewall rules, policy rules, pins and hooks were
restored. The overflow paths are host-tested; no router test produced more than
4,096 placeable connections or an over-capacity domain.
