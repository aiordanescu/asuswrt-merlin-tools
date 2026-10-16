# Sustained primary-WAN download: 2026-10-15

The live RT-AX88U completed a 30-second download comparison at **637.8 Mb/s
with test connections bypassing admission and 632.2 Mb/s with normal sticky
admission**. Hardware flow counters increased throughout both transfers.
The 0.9% difference in this one pair is consistent with similar throughput;
it is not a measurement of affinity's precise overhead or maximum capacity.

## Setup and scope

- Router: RT-AX88U, Merlin 3004.388.12_2, Linux 4.1.51 aarch64. The existing
  production helper stayed running in LAN-wide sticky mode with
  `IDLE_SECONDS=0` and direct watchdog socket health.
- Client: `alexs-mac-mini`, wired 1 Gb/s Ethernet. Every curl connection bound
  its LAN IPv4 address; its separate modem interface was not used.
- Download: one HTTPS/HTTP/1.1 connection to the
  [OVHcloud Hillsboro test server](https://pdx1-hil.speedtest.network.ovh.net/),
  requesting its 10 GiB test file. Each transfer stopped at its duration limit;
  the complete file was not downloaded. Python counted and discarded the body
  in memory. The same resolved server IPv4 address was used for both runs.
- New-connection probes: one fresh curl connection per second fetching the
  same server's small homepage. These were separate connections from the bulk
  transfer, with controlled source ports and recorded TCP/TLS/request timings.
- Order: five seconds capped at 100 Mb/s, 30 seconds bypassing admission,
  three seconds of cooldown, then 30 seconds with normal admission.

The Mini normally has a GUI primary-WAN pin. Its exact pin was temporarily
removed in RAM, while the existing kernel source rule kept it on the primary
until affinity adopted its surviving primary-WAN connections. Then that source
rule was removed for the comparison. All test transfers had the expected WAN0
connmark and primary-WAN reply NAT address.

For the bypass run, a temporary rule immediately before `WA_ADMIT` pre-marked
only the Mini's benchmark HTTPS connections, selected by server address and
source-port range. This bypassed userspace admission while retaining the same
WAN and the rest of the production packet path. **It was not a comparison
against an uninstalled addon or an otherwise idle router.** Other clients kept
their ordinary affinity behavior. No WAN, acceleration setting, global
firewall, connection table or production process was reset.

## Results

Payload averages include startup and every byte read before the cutoff.
Per-second figures are medians of the recorded approximately one-second bins.

| Measurement | Capped smoke | Admission bypass | Normal affinity |
| --- | ---: | ---: | ---: |
| Elapsed seconds | 5.000 | 30.010 | 30.007 |
| Payload bytes | 62,472,192 | 2,392,391,680 | 2,371,239,936 |
| Average payload Mb/s | 100.0 | 637.8 | 632.2 |
| Median one-second Mb/s | 99.9 | 647.1 | 649.0 |
| Successful fresh connection probes | 5/5 | 30/30 | 30/30 |
| Largest sampled bulk hardware hit counter | 39,671 | 1,702,252 | 1,685,766 |

For both full-speed runs, the same identified bulk flow had a nonzero hardware
template and an increasing hardware hit counter across 13 samples from
`/proc/fcache/nflist`. This is per-flow evidence of acceleration during these
transfers, beyond merely reading the global acceleration setting. It does not
qualify every protocol or accelerated flow path.

The production helper retained its PID. Both WANs stayed eligible, the watchdog
reported no routing error or failover, collection failures stayed at zero, and
NFQUEUE kernel/userspace drop counters stayed at zero. The helper's previously
recorded maximum packet timestamp delay remained 6.151 ms throughout the pair;
this cumulative diagnostic is not a bound on every admission's latency.

## Connection timing

The probes include the WAN, TCP handshake, TLS and server response. TLS
completion is measured from the start of each request, not just TLS duration.
Percentiles use the nearest-rank calculation on 30 probes per full-speed run.

| Timing in milliseconds | Bypass median / p95 / max | Affinity median / p95 / max |
| --- | ---: | ---: |
| TCP connect | 7.492 / 9.652 / 1,008.052 | 7.777 / 9.697 / 9.828 |
| TLS completed | 16.843 / 21.057 / 1,018.325 | 17.448 / 255.933 / 261.144 |
| Complete homepage request | 31.342 / 48.913 / 1,034.506 | 32.504 / 270.737 / 276.409 |

There were timing outliers in both runs, at different stages. In particular,
the affinity run's TLS/request p95 was higher despite similar TCP-connect p95.
This small, ordered Internet sample cannot attribute those tails to affinity,
or establish a tail-latency overhead bound. All requests succeeded.

## Cleanup, reproduction and remaining coverage

The driver arms an independent router-local timed cleanup guard before changing
the client's pin. Host-side cleanup runs on completion or failure. Original
GUI pins, policy rules, normalized firewall rules, production helper/watchdog
PIDs and captured startup-hook hashes matched after the final comparison.
Raw snapshots remain in a private evidence directory outside the repository.

See [the opt-in driver instructions](../router-tests/README.md). Preliminary
attempts stopped on public-server HTTP 403/429 responses; restoration passed
after each. Those attempts are excluded from the comparison above. The final
driver uses one large-file request per transfer and small-page probes rather
than repeated requests to a rate-limited large-file endpoint.

One client is enough to test this bulk forwarding path and create concurrent
fresh connections under load. Additional source clients are useful for testing
source placement, contention and aggregate use of both WANs. This result covers
one primary-WAN download stream at the observed rate. It does not establish
maximum 1 Gb/s throughput, upload performance, backup-WAN capacity, aggregate
dual-WAN capacity, many-client scaling, or an hours-long soak. Other deployment
limits remain in the [qualification report](QUALIFICATION-2026-10-15.md).
