# Native WAN watchdog bundle

Target: RT-AX88U, Asuswrt-Merlin 3004.388.12_2, aarch64 Linux.
This archive contains static binaries; no compiler or Python is needed on the
router. Source and licensing: https://github.com/aiordanescu/asuswrt-merlin-tools

This installer upgrades the running, unmodified shell reference included in
the archive. It requires the standard paths, healthy monitoring on both WANs,
and no active failover. It is not a fresh-install or arbitrary-upgrade tool.

Read the complete build, shadow qualification, installation and rollback guide:
https://github.com/aiordanescu/asuswrt-merlin-tools/blob/main/wan-watchdog/NATIVE.md

After the documented preparation, run from this extracted directory:

```sh
sh install-native.sh --install
/jffs/scripts/wan-watchdog status
/jffs/wan-watchdog-native/wan-healthctl
```

The installer retains the routing ownership ledger, preserves existing hooks,
and arms an independent rollback guard. Keep one routing owner. Restore the
saved shell implementation with:

```sh
sh /jffs/wan-watchdog-native/rollback.sh --rollback
```

Normal stop preserves forwarding state; explicit cleanup is separate.
No subscriber is required. This bundle includes no affinity component.
The Unix health socket's consumer contract is documented at:
https://github.com/aiordanescu/asuswrt-merlin-tools/blob/main/common/wan-health/PROTOCOL.md

The MIT license is included as `LICENSE`.
