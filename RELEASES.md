# Prebuilt WAN tools

[WAN tools — 2026-10-15 beta](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/tag/wan-tools-2026.10.15-beta.3)
provides static **aarch64 Linux** bundles. No compiler, Python or Entware is
needed on the router. Tested target: RT-AX88U, Asuswrt-Merlin 3004.388.12_2,
Linux 4.1.51. This is not a build for 32-bit ARM, MIPS or every Merlin model.

| Download | Included production binaries | Installation |
| --- | --- | --- |
| [wan-affinity-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.15-beta.3/wan-affinity-aarch64.tar.gz) | `wan-affinity` (0.4.1), controller, installer, disabled example, rollback and guides | [Affinity instructions](wan-affinity/README.md#download-and-install) |
| [wan-watchdog-aarch64.tar.gz](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/download/wan-tools-2026.10.15-beta.3/wan-watchdog-aarch64.tar.gz) | `wan-watchdog`, `wan-healthctl`, shell actuator/reference, installer and rollback | [WAN Watchdog instructions](wan-watchdog/GUIDE.md#download-and-qualification) |

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
Affinity's installer starts disabled. The watchdog's C-monitor installer requires
an existing matching shell reference, healthy monitoring on both WANs and no
active failover; it refuses an existing service directory. A prebuilt download
does not change those checks or turn this into an unattended watchdog upgrade.

## Verification for this release

The renamed programs passed 176 Python tests and 602 host C checks. Router-only
socket, subscriber/health-merge and loopback-probe tests passed 1,273 checks using
private test endpoints. All four health-source settings passed configuration
validation on the router.

A guarded update on the tested router preserved the GUI WAN pins, routing
rules, mangle rules and startup-hook bytes. Both WANs became freshly eligible,
Affinity consumed watchdog health using `HEALTH_SOURCE=watchdog`, and its
supervisor retained the new helper. Eight LAN-bound HTTPS/DNS/QUIC checks from
the Mac mini passed. These checks do not expand the platform or outage
qualification described in the component reports.

## Optional source builds

Source remains available at the release tag. Build on a development computer
with the documented static aarch64 toolchain:

- [WAN Affinity build instructions](wan-affinity/BUILDING.md)
- [WAN Watchdog build and test instructions](wan-watchdog/GUIDE.md#optional-source-build)

The release builds use Zig 0.15.2, `aarch64-linux-musl`, static linkage,
`-std=c11 -O2 -Wall -Wextra -Werror`. Maintainers build the two `bundle` targets
from a clean export of the tagged commit, inspect the package contents and
generate checksums for those exact archives. Recreating an archive may produce
a different archive hash because tar/gzip metadata can differ; the manifest
also records the contained binary hashes.
