# WAN health protocol, version 1

The watchdog publishes authoritative IPv4 WAN measurements at
`/tmp/wan-watchdog/health.sock`, a read-only `AF_UNIX/SOCK_SEQPACKET` service.
Every subscriber receives complete snapshots independently. The watchdog keeps
its routing and DDNS responsibilities even with no subscribers.

Connect once, receive records and reconnect after EOF/error with bounded
backoff. A connection immediately receives the current complete state. There
is no subscription request, command channel or acknowledgement. Sending data
closes that subscriber. Directory/socket permissions are 0700/0600, and peer
credentials must identify root or the service UID. Supporting non-root clients
requires a deliberately configured permission policy.

The publisher supports 16 subscribers with small bounded kernel buffers. The
application keeps one latest state, replacing superseded pending snapshots.
Slow subscribers never block monitoring; persistent backlog disconnects after
15 seconds. Intermediate events can be missed: this is a current-state service,
not a durable event log.

## Encoding

Use `wh_encode`/`wh_decode`, or implement the layout below. Never send an in-memory
C struct. All integers are big-endian; signed values use 32-bit two's
complement. IPv4 values are four network-order bytes. Version 1 is exactly
384 bytes: header 112 plus two 136-byte records in unit order. Reject unknown
versions and lengths.

| Header offset | Bytes | Field |
| --- | --- | --- |
| 0 | 4 | Magic `WHL1` (`0x57484c31`) |
| 4 | 4 | Version 1 |
| 8, 12, 16, 20 | 4 each | Total bytes 384, header bytes 112, record bytes 136, WAN count 2 |
| 24, 40 | 16 each | Router boot UUID, publisher instance UUID |
| 56, 64, 72 | 8 each | Sequence, publication time, completed probe-cycle count |
| 80, 88 | 8 each | Desired routing generation, acknowledged applied generation |
| 96, 100 | 4 each | Pinned mode, routing error (booleans) |
| 104, 108 | 4 each | Desired/applied failed WAN, signed; -1 means no whole-LAN override |

| WAN record offset | Bytes | Field |
| --- | --- | --- |
| 0, 4 | 4 each | Unit 0/1, reason |
| 8, 12, 16 | 4 each | Bad, acceptable, legacy routing recovered |
| 20, 24, 28 | 4 each | Eligible for new assignments, excluded, recovery state |
| 32 | 8 | Configuration/window generation |
| 40, 48, 56, 64 | 8 each | Latest measurement, latest valid measurement, admission recovery start or zero, transition count |
| 72, 88 | 16 each | Target A/B: sample, lost, unknown counts; signed latest result |
| 104 | 16 | NUL-terminated, padded interface name |
| 120, 124, 128, 132 | 4 each | Source IPv4, gateway, effective target A, target B |

Reasons: 0 warming, 1 usable, 2 disabled, 3 unknown, 4 degraded, 5 unreachable.
Recovery: 0 not required, 1 held, 2 complete. Probe result: -1 unknown, 0 lost,
1 reply. Booleans are 0 or 1.

Times are router `CLOCK_MONOTONIC` nanoseconds, comparable within one boot.
The monitor cross-checks `CLOCK_BOOTTIME` to reset windows after suspend.
Wall-clock/NTP changes do not accrue recovery. Fresh publication never makes
old per-WAN evidence fresh.

## Consumer contract

Validate the complete frame before applying state: current boot, nonzero
instance/sequence, plausible nonfuture timestamps, publication age at most
15 seconds, and eligible WAN measurement/valid-evidence ages at most
15 seconds. The reference decoder also checks enums, counts, eligibility and
routing-generation consistency.

Compare sequence within `(boot, instance)`. Each daemon start gets a new
instance and may restart at sequence 1. Reject duplicate/decreasing sequences
within a connection. Reconnecting to the same instance may return the same
sequence; this does not refresh its publication time.

Use `eligible` for new assignments; `acceptable` alone does not authorize a
recovering WAN. The exclusion latch survives failure without a destination,
failed actuation and same-boot restarts. Its clean recovery hold is independent
of the legacy routing recovery clock. An acknowledged emergency overlay
reversal may admit its acceptable destination before the normal hold. A
routing ledger entry records intent; applied fields advance only after a
successful worker acknowledgement.

Conjoin health with firmware address/interface/route readiness. Match source
address and interface before acting on a record. Publisher loss never proves
recovery. Consumers own their assignment policy and UNKNOWN behavior; a
recovered WAN becoming eligible alone must not move existing connections.

`wh_socket.{h,c}` contains bounded reference server/client implementations.
`wan-healthctl` prints JSON. Status and RAM-only restart checkpoints are
diagnostic/recovery artifacts, separate from the live interface.
