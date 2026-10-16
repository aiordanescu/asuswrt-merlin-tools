# Shared watchdog health

Affinity consumes versioned `SOCK_SEQPACKET` health directly in its parent
admission loop, before new queued packets. A delayed native-readiness worker
cannot overwrite newer socket health.

| `HEALTH_SOURCE` | Behavior |
| --- | --- |
| `auto` (default) | Fresh socket health, otherwise native readiness retaining known exclusions |
| `socket` | Require fresh matching watchdog evidence for new eligibility; conservative startup |
| `native` | Explicit standalone address/interface/route readiness, without a quality claim |
| `legacy` | Explicit older textual snapshot compatibility at `SNAPSHOT_PATH` |

`HEALTH_SOCKET` defaults to `/tmp/wan-watchdog/health.sock`. Affinity sends no
commands. It validates schema, boot/instance, sequence, publication/per-WAN
age and current native interface/address identity. Same-boot RAM checkpoints
retain exclusions across helper restarts. A stale/absent publisher never
proves recovery; existing assignments retain the policy's UNKNOWN behavior.

Recovery affects future admissions. Sticky retains the current eligible WAN;
affinity may return future flows to the stable preferred WAN. Existing
conntrack marks stay intact. The watchdog retains household routing authority.

See the [public protocol](../../common/wan-health/PROTOCOL.md),
[service operations](../../wan-watchdog/NATIVE.md), and the affinity
[qualification report](QUALIFICATION-2026-10-15.md). Actual router reboot,
firmware upgrade and maximum sustained throughput remain untested; the report
distinguishes those limits from guarded live installation and lifecycle checks.
