# Prebuilt WAN tools

[WAN tools — 2026-10-15 beta](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/tag/wan-tools-2026.10.15-beta.6)
provides static **aarch64 Linux** bundles. No compiler, Python or Entware is
needed on the router. Tested target: RT-AX88U, Asuswrt-Merlin 3004.388.12_2,
Linux 4.1.51. This is not a build for 32-bit ARM, MIPS or every Merlin model.

| Download | Included production binaries | Installation |
| --- | --- | --- |
| [wan-affinity-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.15-beta.6/wan-affinity-aarch64.tar.gz) | `wan-affinity` (0.4.3), controller, installer, disabled example, rollback and guides | [Affinity instructions](wan-affinity/README.md#download-and-install) |
| [wan-watchdog-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.15-beta.6/wan-watchdog-aarch64.tar.gz) | `wan-watchdog`, `wan-healthctl`, shell actuator/reference, installer and rollback | [WAN Watchdog instructions](wan-watchdog/GUIDE.md#download-and-qualification) |

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

The executables (`wan-affinity`, `wan-watchdog`, `wan-healthctl`) are
byte-identical to beta.5. Only the watchdog installer, its service library and
documentation changed. `--upgrade` from an installed beta.5 monitor installs the
corrected rollback tools. See the
[maintenance follow-up](MAINTENANCE-2026-10-15.md#follow-up-in-beta6-rollback-confirmation-and-first-handoff-capture).

If a beta.5 rollback to the shell already reported failure, check
`cat /tmp/wan-watchdog/status`. A first line `uptime_seconds=... mode=...` that
keeps updating, with `WAN0` and `WAN1` lines, means the shell is running. Don't
run that rollback again; each run restarts the healthy shell.

## Verification for this release

The changed lifecycle scripts pass 82 watchdog shell/DDNS/lifecycle tests on
the host, including new tests against the shell's real status output, a
first-install rollback, and both settings-capture sources. They have not been
run on the router. All host suites pass on the tagged source: Affinity 107
Python tests, 270 engine/checkpoint C checks (also under ASan/UBSan) and 21
worker C checks; watchdog 434 core/codec C checks and 11 adapter/parity tests.
The executables are the beta.5 files, whose code and data sections match a
clean Zig 0.15.2 rebuild of this source.

The beta.5 router qualification covers the same executables. See the
[maintenance qualification and review disposition](MAINTENANCE-2026-10-15.md)
for lifecycle fixes, lease-bound routing decisions, slow-worker tests and the
corrected kernel queue fallback guarantee.

For beta.5, private router tests exercised the actual monitor with loopback
probes and file-only routing adapters, plus kernel queue traversal without
stopping the production helper. Guarded production updates and LAN-bound client
checks used the same executables packaged in this release. Build metadata and
the validation run for this release are recorded in `BUILD-INFO.json`.

The adoption update's prior mixed-session and sustained-throughput evidence is
retained in the component documentation. Testing remains scoped to the listed
router/firmware; the beta does not claim universal Merlin support.
