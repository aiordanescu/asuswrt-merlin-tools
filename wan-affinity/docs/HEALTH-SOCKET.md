# WAN health sources

Choose the health provider independently of the watchdog's implementation language:

| Provider | `HEALTH_SOURCE` | Behavior |
| --- | --- | --- |
| **WAN Watchdog** | `socket` | Use measured watchdog health; fresh matching evidence is required for new eligibility |
| **Router readiness** | `native` | Check interface, address, gateway, state and route readiness; no active Internet probes |
| Automatic (default) | `auto` | Discover WAN Watchdog when available; otherwise use router readiness while retaining known exclusions |
| Older file adapter | `legacy` | Read the older textual snapshot at `SNAPSHOT_PATH` |

Use `socket` with WAN Watchdog, including its C implementation. The value
`native` refers to router-readiness checks. These are the existing configuration
values; the provider labels above make their meaning explicit.

## Socket integration details

The connection uses the versioned, read-only Unix `SOCK_SEQPACKET` protocol.
Health arrives directly in affinity's admission event loop; a delayed router
fact collector cannot overwrite newer watchdog information. Multiple independent
consumers can subscribe.

`HEALTH_SOCKET` defaults to `/tmp/wan-watchdog/health.sock`. Affinity sends no
commands. It validates schema, boot/instance, sequence, publication/per-WAN
age and current router interface/address identity. Same-boot RAM checkpoints
retain exclusions across helper restarts. A stale/absent publisher never
proves recovery; existing assignments retain the policy's UNKNOWN behavior.

Recovery affects future admissions. Sticky retains the current eligible WAN;
affinity may return future flows to the stable preferred WAN. Existing
conntrack marks stay intact. The watchdog retains household routing authority.

See the [public protocol](../../common/wan-health/PROTOCOL.md),
[service operations](https://github.com/aiordanescu/asuswrt-merlin-tools/blob/main/wan-watchdog/GUIDE.md), and the affinity
[qualification report](QUALIFICATION-2026-10-15.md). Actual router reboot,
firmware upgrade and maximum sustained throughput remain untested; the report
distinguishes those limits from guarded live installation and lifecycle checks.
The [primary-WAN download report](THROUGHPUT-2026-10-15.md) records a subsequent
30-second comparison with per-flow acceleration evidence; it does not establish
maximum or aggregate capacity.
