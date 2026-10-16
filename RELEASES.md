# Prebuilt WAN tools

[WAN tools — 2026-10-15 beta](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/tag/wan-tools-2026.10.15-beta.5)
provides static **aarch64 Linux** bundles. No compiler, Python or Entware is
needed on the router. Tested target: RT-AX88U, Asuswrt-Merlin 3004.388.12_2,
Linux 4.1.51. This is not a build for 32-bit ARM, MIPS or every Merlin model.

| Download | Included production binaries | Installation |
| --- | --- | --- |
| [wan-affinity-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.15-beta.5/wan-affinity-aarch64.tar.gz) | `wan-affinity` (0.4.3), controller, installer, disabled example, rollback and guides | [Affinity instructions](wan-affinity/README.md#download-and-install) |
| [wan-watchdog-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.15-beta.5/wan-watchdog-aarch64.tar.gz) | `wan-watchdog`, `wan-healthctl`, shell actuator/reference, installer and rollback | [WAN Watchdog instructions](wan-watchdog/GUIDE.md#download-and-qualification) |

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

## Known issues in beta.5

Both are fixed in source after this release; see the
[maintenance follow-up](MAINTENANCE-2026-10-15.md#follow-up-after-beta5-rollback-confirmation-and-first-handoff-capture).
They affect only a watchdog first install (`--install`) that is rolled back to
the shell. Upgrades and affinity are not affected.

- **Rollback to the shell reports failure after succeeding.** `rollback.sh`
  restarts the shell, then prints `replacement monitoring not confirmed;
  recovery snapshot retained` after 45 seconds and exits 1. Check
  `cat /tmp/wan-watchdog/status`: a first line `uptime_seconds=... mode=...`
  that keeps updating, with `WAN0` and `WAN1` lines, means the shell is
  running. Don't run rollback again; each run restarts the healthy shell.
- **A later `--install` can reuse an old `service.conf`.** After such a
  rollback, move `/jffs/wan-watchdog/service.conf` aside before retrying
  `--install`, so the installer captures the running shell's settings.

## Verification for this release

See the [maintenance qualification and review disposition](MAINTENANCE-2026-10-15.md)
for lifecycle fixes, lease-bound routing decisions, slow-worker tests and the
corrected kernel queue fallback guarantee. Affinity 0.4.3 passes 107 Python tests,
270 engine/checkpoint C checks, 21 worker C checks and the normal ASan/UBSan
build. Watchdog checks include 434 core/codec checks, shell/C parity, adapter
checks, shell/DDNS regressions and real-process install/rollback fixtures.

Private router tests exercise the actual monitor with loopback probes and
file-only routing adapters, plus kernel queue traversal without stopping the
production helper. Guarded production updates and LAN-bound client checks use
the same executables packaged in this release. Full build and qualification
metadata are recorded in `BUILD-INFO.json`.

The adoption update's prior mixed-session and sustained-throughput evidence is
retained in the component documentation. Testing remains scoped to the listed
router/firmware; the beta does not claim universal Merlin support.
