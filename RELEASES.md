# Prebuilt WAN tools

[WAN tools — 2026-10-16 beta](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/tag/wan-tools-2026.10.16-beta.2)
provides static **aarch64 Linux** bundles. No compiler, Python or Entware is
needed on the router. Tested target: RT-AX88U, Asuswrt-Merlin 3004.388.12_2,
Linux 4.1.51. This is not a build for 32-bit ARM, MIPS or every Merlin model.

| Download | Included production binaries | Installation |
| --- | --- | --- |
| [wan-affinity-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.16-beta.2/wan-affinity-aarch64.tar.gz) | `wan-affinity` (0.4.6), controller, installer, disabled example, rollback and guides | [Affinity instructions](wan-affinity/README.md#download-and-install) |
| [wan-watchdog-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.16-beta.2/wan-watchdog-aarch64.tar.gz) | `wan-watchdog`, `wan-healthctl`, shell actuator/reference, installer and rollback | [WAN Watchdog instructions](wan-watchdog/GUIDE.md#download-and-qualification) |

The components remain independently installed and operated. The shell watchdog
and Namecheap DDNS helper do not need compilation. Test executables and fixture
publishers are excluded from the downloads.

## Download and verify

Download the selected archive and `SHA256SUMS` from the release's **Assets**
section to your computer. Check that the archive's SHA-256 matches its entry
in `SHA256SUMS`. On macOS, for example:

```sh
shasum -a 256 wan-affinity-aarch64.tar.gz
```

On Linux, use `sha256sum` instead. If you download both bundles and
`BUILD-INFO.json`, verify all files together:

```sh
shasum -a 256 -c SHA256SUMS
```

`BUILD-INFO.json` records the source commit, compiler/target, build flags and
archive/binary hashes. Each release is tied to its Git tag. Archives include
the project license and static-runtime notices. Published assets are not a
copy of a router's installed configuration or runtime state.

Transfer the verified archive with `scp -O` and follow its component guide.
Affinity's installer starts disabled. The watchdog installer requires healthy
monitoring on both WANs and no active override. Use `--install` for handoff from a
reviewed shell reference or `--upgrade` for an installed monitor; its supported
settings are saved in private `service.conf` and retained through recovery.

## Changes on 2026-10-16 (beta.2)

WAN Affinity 0.4.6 and the updated watchdog close the remaining 22 Low review
findings. The [disposition and evidence](REVIEW-LOW-2026-10-16.md) map every
finding to its fix and regression coverage.

- Affinity retains complete status at maximum mapping capacity, accepts quoted
  literal configuration consistently, and preflights both hooks before stopping
  an enabled service. Upgrade and rollback preserve enabled intent across
  supervisor restart gaps. Unconfirmed identities and reconstruction limits
  follow the reference-model contract.
- Watchdog lifecycle operations preserve multiline settings, permit retry after
  shell rollback and tolerate a reused stale PID when routing is unowned. Runtime
  directories are private, raw sockets filter unrelated ICMP types, missing facts
  report an invalid age, and obstructed health sockets retry without log growth.
- DDNS workers detach without accumulating shell jobs. Auto overrides require
  live ownership and recent progress. The Namecheap checker uses a kernel lock
  that survives process death without blocking later checks.
- Downloaded Markdown links resolve within the archive or to this source tag.
  Both bundles retain optional build instructions and licensing.

**Separate helper update:** the watchdog archive now includes
`wan-watchdog-ddns.sh`. Its C-service installer does not replace the separately
installed DDNS helper. Follow the [bundle instructions](wan-watchdog/BUNDLE-README.md)
to update that script too, retaining custom paths and hooks. The Namecheap
checker is a shell-only source update; use its [installation guide](namecheap-ddns/README.md).

## Changes on 2026-10-16 (beta.1)

WAN Affinity 0.4.5 keeps checkpoint writes out of packet admission and shortens
recovery of a stalled helper. The watchdog is unchanged from the October 15
beta.7 package.

- A separate child saves coalesced RAM checkpoints at most once every five
  seconds. Its two-second timeout never waits for disk I/O in admission.
- An independent supervisor checks progress and the kernel queue once a second.
  Recovery requires five seconds without status progress and a pending packet
  for one second. One packet is sufficient; short busy bursts do not qualify.
- The supervisor closes a stuck queue before invoking the lifecycle controller.
  Three stalls in 60 seconds leave firmware balancing in charge until `start`.
- Status adds checkpoint counts/durations and netlink receive-overrun counts.
- The recovery guide distinguishes already-held packets, queue-full arrivals,
  absent-listener bypass and confirmed flows, with measured fault-test results.

See [the recovery report](wan-affinity/docs/RECOVERY-2026-10-16.md) for the test
scope, distributions and limitations. Optional source builds remain supported.

## Changes in beta.7

WAN Affinity 0.4.4 removes two capacity limits that could stop placement for
the whole LAN. WAN Watchdog is unchanged from beta.6.

- **Routing domain.** Main-table routes, GUI pins and policy exclusions
  covered by a broader entry are merged, and the limits rise to 64 local
  prefixes and 128 policy selectors. The tested router previously used 12 of
  15 route slots; the same routes now use 11 of 64 prefixes. Exceeding a limit
  used to make every refresh fail and could stop the helper from starting. Now
  the helper keeps running and pauses placement until the domain fits. New
  connections use firmware balancing, and status reports why.
- **Connection view.** Only connections the helper could place count toward
  4,096. DNS to the router and LAN traffic no longer do. Above the limit, WAN
  facts keep refreshing so failover stays current, and mappings are kept rather
  than expired. Previously one connection too many rejected every refresh and
  left health stale.

A new `capacity` line in `/tmp/wan-affinity/status` shows the counts and
limits. See the
[maintenance notes](MAINTENANCE-2026-10-15.md#affinity-044-in-beta7-capacity-limits)
and the affinity README's status and limits section.

## Changes in beta.6

Beta.6 fixes two watchdog lifecycle issues found after beta.5. Both affect only
a watchdog first install (`--install`) that is later rolled back to the shell.
Upgrades and affinity behavior are not affected.

- **Rollback to the shell now confirms the restored shell.** In beta.5,
  `rollback.sh` restarted the shell correctly, then printed `replacement
  monitoring not confirmed; recovery snapshot retained` after 45 seconds and
  exited 1.
- **`--install` captures the running shell's settings** even when an older
  `service.conf` exists. Beta.5 could reuse a file left by an earlier rollback
  and restore an old mode or priority.

Beta.6's executables (`wan-affinity`, `wan-watchdog`, `wan-healthctl`) were
byte-identical to beta.5. Only the watchdog installer, its service library and
documentation changed. `--upgrade` from an installed beta.5 monitor installs the
corrected rollback tools. See the
[maintenance follow-up](MAINTENANCE-2026-10-15.md#follow-up-in-beta6-rollback-confirmation-and-first-handoff-capture).

If a beta.5 rollback to the shell already reported failure, check
`cat /tmp/wan-watchdog/status`. A first line `uptime_seconds=... mode=...` that
keeps updating, with `WAN0` and `WAN1` lines, means the shell is running. Don't
run that rollback again; each run restarts the healthy shell.

## Verification for this release

Affinity passes 299 engine/checkpoint C checks (including ASan/UBSan), 21 worker
checks, the configuration and supervisor/scheduling suites, and 119 Python
tests. Watchdog passes 434 core/codec C checks, 21 worker checks, portable
runtime-decision tests, 11 adapter/parity tests and 88 shell/DDNS/lifecycle tests.
The Namecheap SIGKILL/child-lock regression also passes.

On the qualified router, the current 39 watchdog and 30 DDNS shell mock tests
pass through BusyBox. The private C run passes 675 socket checks, including
subscriber capacity, unexpected client data and a different UID; 17 probe checks
include the kernel ICMP filter. Additional checks cover configuration parsing,
missing facts, directory permissions, listener recovery and NUL-safe capture.
The file-only maintenance driver covers old-lease invalidation, slow workers,
restart acknowledgement and shadow recovery without changing production routes.

See the [review disposition](REVIEW-LOW-2026-10-16.md) for commands and scope,
and the [recovery report](wan-affinity/docs/RECOVERY-2026-10-16.md) for the earlier
queue and flow measurements. Each build's source commit and archive/binary
hashes are in `BUILD-INFO.json`. Routine upgrades use the supported installer
under an independent rollback guard; no physical outage is induced.

The adoption update's prior mixed-session and sustained-throughput evidence is
retained in the component documentation. Testing remains scoped to the listed
router/firmware; the beta does not claim universal Merlin support.
