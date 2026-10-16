# WAN Watchdog bundle

Target: RT-AX88U, Asuswrt-Merlin 3004.388.12_2, aarch64 Linux.
This archive contains static binaries; no compiler or Python is needed on the
router. Source and licensing: https://github.com/aiordanescu/asuswrt-merlin-tools

Prebuilt downloads and checksums:
https://github.com/aiordanescu/asuswrt-merlin-tools/releases/tag/wan-tools-2026.10.15-beta.6
Verify the downloaded archive against `SHA256SUMS` on your computer before
transferring it. Building from source remains optional; the complete guide
below retains those instructions.

This installer supports a running, reviewed shell reference (`--install`) or
an installed C monitor (`--upgrade`). It requires the standard paths, healthy
monitoring on both WANs and no active failover. Establish the shell setup first
for a new watchdog installation. Supported settings are captured from the
running owner into `/jffs/wan-watchdog/service.conf`; starts and recovery use
that saved configuration.

Read the complete build, shadow qualification, installation and rollback guide:
https://github.com/aiordanescu/asuswrt-merlin-tools/blob/main/wan-watchdog/GUIDE.md

After the documented preparation, run from this extracted directory:

```sh
sh install.sh --install
/jffs/scripts/wan-watchdog status
/jffs/wan-watchdog/service/wan-healthctl
```

Use `sh install.sh --upgrade` for an installed monitor. The installer retains
the routing ownership ledger, preserves existing hooks,
and arms an independent rollback guard. Keep one routing owner. Restore the
previous service and saved configuration with:

```sh
sh /jffs/wan-watchdog/service/rollback.sh --rollback
```

Rollback verifies the replacement process, service lock and monitoring output.
Its mutex is automatically released on process exit. A stale PID file is not
accepted as evidence of successful recovery.

Normal stop preserves forwarding state; explicit cleanup is separate.
No subscriber is required. This bundle includes no affinity component.
The Unix health socket's consumer contract is documented at:
https://github.com/aiordanescu/asuswrt-merlin-tools/blob/main/common/wan-health/PROTOCOL.md

The MIT license is included as `LICENSE`; static-runtime notices are in
`LICENSES/`.
