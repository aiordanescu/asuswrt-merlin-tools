# Prebuilt WAN tools

[WAN tools — 2026-10-15 beta](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/tag/wan-tools-2026.10.15-beta.7)
provides static **aarch64 Linux** bundles. No compiler, Python or Entware is
needed on the router. Tested target: RT-AX88U, Asuswrt-Merlin 3004.388.12_2,
Linux 4.1.51. This is not a build for 32-bit ARM, MIPS or every Merlin model.

| Download | Included production binaries | Installation |
| --- | --- | --- |
| [wan-affinity-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.15-beta.7/wan-affinity-aarch64.tar.gz) | `wan-affinity` (0.4.4), controller, installer, disabled example, rollback and guides | [Affinity instructions](wan-affinity/README.md#download-and-install) |
| [wan-watchdog-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.15-beta.7/wan-watchdog-aarch64.tar.gz) | `wan-watchdog`, `wan-healthctl`, shell actuator/reference, installer and rollback | [WAN Watchdog instructions](wan-watchdog/GUIDE.md#download-and-qualification) |

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

Affinity 0.4.4 passes 289 engine/checkpoint C checks (also under ASan/UBSan),
21 worker C checks and 107 Python tests on the tagged source. On the tested
router, with the production service running, the new helper passed the loopback
lifecycle regression and the mixed-session adoption matrix in both modes with
real HTTPS connections. It read the router's real routes, rules and pins and
reported `domain=ok local_prefixes=11/64 policy_selectors=20/128`. The packaged
helper then replaced the production service through `install.sh --upgrade`. It
restarted with its mappings restored and both WANs eligible, and firewall
rules, policy rules and hooks were unchanged. The over-capacity paths are
host-tested only.

The watchdog executables are byte-identical to beta.5 and its scripts to
beta.6. They pass 82 shell/DDNS/lifecycle tests, 11 adapter/parity tests and
434 core/codec C checks. See the
[maintenance qualification and review disposition](MAINTENANCE-2026-10-15.md)
for the beta.5 router qualification, lease-bound routing decisions, slow-worker
tests and the corrected kernel queue fallback guarantee. Build metadata and the
validation run for this release are recorded in `BUILD-INFO.json`.

The adoption update's prior mixed-session and sustained-throughput evidence is
retained in the component documentation. Testing remains scoped to the listed
router/firmware; the beta does not claim universal Merlin support.
