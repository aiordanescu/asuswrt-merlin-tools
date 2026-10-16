# Scoped forwarding tests and remaining qualification gates

> Follow-up (2026-10-15): the unconfirmed-flow cleanup and blocking-refresh
> issues described below were fixed and tested. Current details:
> [LIFECYCLE-FIX-2026-10-15.md](LIFECYCLE-FIX-2026-10-15.md). Other rollout
> qualification gates remain. The earlier findings are preserved as history.

Date: 2026-10-14. Target: RT-AX88U, Asuswrt-Merlin `3004.388.12_2`,
Linux `4.1.51`, aarch64. Both WANs use DHCP; native mode remained Load Balance.

## Decision

**The scoped forwarding behavior works. The prototype is not ready for wider
installation.** Stage 2 produced real packet-path evidence; stage 3 remains
partial because of an unresolved admission-lifecycle case and incomplete
performance/integration qualification.

The router has been restored. Neither this helper nor test hooks remain active.
No firmware, package, production script, DDNS setting or WAN state was changed.
Nothing was committed or pushed.

## Test boundaries and rollback

The authorized clients were an Android phone (Pixel) and a macOS client, tested
one at a time. Each initially had a GUI WAN pin. First the helper was attached
with the pin intact, confirming pass-through and no automatic assignments.
Only that client's pin was then removed temporarily from RAM NVRAM and its
priority-100 routing rule. NVRAM was never committed.

Each instance used a unique private `/tmp` directory and mangle chain, queue
20880, and a NEW/ORIGINAL/br0 rule matching exactly one source `/32`. Independent
timed cleanup was armed before activation. It restored the saved pin and
removed the exact test jump, chain and helper. No shared chain was flushed.

The macOS client also has an interface directly connected to the backup modem.
Test sockets and curl requests were bound to its home-LAN address. Router
conntrack original/reply tuples confirmed that traffic traversed the router;
the client's direct modem interface was not used.

Health changes were private fixtures using the optional snapshot schema and
the router's current boot ID/monotonic time. No production watchdog health file
was written. Neither real WAN was disabled, and no real whole-LAN failover,
firewall rebuild, router reboot or conntrack flush was performed.

## Observed forwarding results

| Scenario | Evidence / result |
| --- | --- |
| Manual pins intact | Phone remained on Secondary; macOS client remained on Primary. Helper recorded passes with zero automatic admissions. |
| Sticky on the phone | Adopted Secondary, moved new connections to Primary when the fixture declared Secondary unusable, retained Primary when both became eligible. |
| Affinity on macOS | A test seed preferred Primary. Fresh requests used Primary, then Secondary on failure, stayed on Secondary through a recovery hold, and returned to Primary only after eligibility was restored. |
| Concurrent HTTPS | Four independent HTTP/1.1 requests per phase, with unique bound source ports and `Connection: close`. Every request in each phase returned the expected WAN's public IPv4 address. |
| DNS | Bound UDP queries to Google and Quad9's unfiltered endpoint succeeded. Matching router states showed the selected WAN's SNAT reply address. |
| UDP/443 | Actual QUIC version-negotiation exchanges with Google and ident.me returned version-zero replies on both WANs. Phone traffic also included UDP/443. This does not claim a full HTTP/3 benchmark. |
| Quiet existing HTTPS | A marked Primary connection remained established while new flows moved to Secondary. After 25 seconds idle, another request on that same TLS socket returned HTTP 204. |
| Exclusion persistence | An expired test snapshot left the excluded WAN UNKNOWN rather than treating missing enhanced data as recovery. This was observed within the same running helper. |
| Local management | SSH to router/client and the phone's LAN test page remained reachable. Other LAN sources were outside the installed selector. |

The controlled macOS matrix issued 20 successful HTTPS requests, 10 successful
DNS queries and 10 successful two-way UDP/443 exchanges across the initial,
failed, stale/held, fresh-held and recovered cases. The fresh-held and recovered
runs explicitly checked `enhanced=yes` before issuing requests. Raw status,
source-port-correlated conntrack records and client outputs remain in the
private backup, not in this public directory.

## Repairs made before/during qualification

- Built the Linux adapter with Zig 0.15.2, static `aarch64-linux-musl`, and
  compiler warnings treated as errors. Fixed missing network declarations and
  the first-build warning failure.
- Repaired the controller: actually build the chain; recognize the first queue
  status column; insert before native balance; remove the exact selector rule;
  prove ownership; preserve foreign chains; clean up after partial startup;
  verify the helper's process identity; refuse the management source/CIDRs;
  and remove stale readiness files before starting.
- Matched the real router shell: `id` and fractional `sleep` are unavailable.
  Root detection falls back to `/proc/self/status`; waits use integer seconds.
- Replaced the low-bit pass sentinel with `0xa0000000/0xf0000000` in the WAN
  field, preserving unrelated bits. Existing installations using that WAN-field
  value would need an explicit namespace conflict check before deployment.
- Fixed packet framing: a final netlink message need not include alignment
  padding. Unsigned `NLMSG_NEXT` remaining-length arithmetic underflowed and
  crashed the helper on a 159-byte packet. A bounded iterator and regression
  tests cover that exact case, coalesced frames and malformed lengths. Queue
  bypass was observed after the crash; the subsequent forwarding matrix used
  the corrected build.
- Corrected snapshot freshness to use publication time, not reread time;
  validate publisher boot identity; retain exclusions; respect native route
  readiness and explicit recovery eligibility.
- Added strict native conntrack parsing, actual source/destination-port
  preservation, dump truncation/error rejection, pending-state capacity checks,
  and conservative retention when a current reconcile is invalid.
- Corrected restart egress attribution: GUI source pins outrank WAN marks, so
  an old connection can have a misleading native mark. A confirmed SNAT reply
  address matching a current WAN address takes precedence when reconstructing
  its actual egress.

The original lifecycle mock accepted a partial rule deletion that real iptables
would reject. It now requires an exact match and exercises successful startup,
rollback after insertion failure, native rule ordering and foreign ownership.

## Firmware compatibility finding

The live kernel reports:

```
# CONFIG_NETFILTER_NETLINK_QUEUE_CT is not set
```

Setting the NFQUEUE conntrack flag succeeds but queued packet messages omit
`NFQA_CT`/`CTA_ID`. Native `conntrack -L -o extended,id` still provides IDs.

The adapter now assigns local tokens using exact original five-tuple equality
(protocol, both IPv4 addresses and both ports), rather than a 32-bit hash or an
unavailable queued-packet ID. Confirmed snapshots preserve those tuples and
confirm pending admissions. Reuse conservatively retains source liveness;
new generations cannot be erased by an old raw DESTROY event. Events are
observed but do not mutate tuple liveness in this compatibility path. Nonzero
conntrack zones are refused. This is a default-zone IPv4 implementation.

For context, Linux 4.1 guards the queue connection-metadata builder with
`CONFIG_NETFILTER_NETLINK_QUEUE_CT` in
[the upstream conntrack source](https://github.com/torvalds/linux/blob/v4.1/net/netfilter/nf_conntrack_netlink.c).
The router configuration and packet bytes, not the upstream source alone,
establish the behavior of this installed vendor kernel.

## Remaining blockers and limits

1. **Terminal cleanup for never-confirmed admissions.** Both clients eventually
   reported `uncertain=yes last_error=unconfirmed-admission`. Some admitted
   tuples were absent from later confirmed dumps and were retained. A plausible
   cause is a connection completing or being rejected before the next periodic
   dump; its exact terminal path has not yet been proved. Retention prevents
   unsafe expiry but can prevent full-idle cleanup and accumulate state. At the
   configured capacity, new untracked admissions pass to native routing; source
   stickiness is then no longer guaranteed. A timer is not a valid fix: the
   earlier kernel test proved that an unconfirmed packet may still be queued.
   Resolve this with a tested release/terminal-evidence mechanism before wider
   use. Current regression coverage protects retention, not eventual cleanup.
2. **Admission latency and throughput.** The helper's resident memory was about
   488 KiB. Periodic native-fact/dump refresh reached 92.73 ms; the largest
   observed kernel-packet-timestamp-to-helper-read delay was 73.159 ms. These
   refreshes currently block the admission loop. This is a limited scoped
   sample, not a throughput/offload qualification or a pure queue-delay
   benchmark. Isolate/bound the blocking work and measure sustained load before
   LAN-wide use. No global acceleration setting was changed.
3. **Integration/lifecycle.** Reboot, firewall rebuild, upgrades, concurrent hook
   execution, full uninstall packaging, live mode changes and helper-restart
   recovery during enhanced-health disappearance still need qualification.
   The watchdog snapshot publisher was not installed; production operation in
   these tests was native readiness plus private fixtures only.
4. **Policy matrix.** Simple live GUI pins were verified. Complex subnet/
   destination pins, live pin edits/removal, public VPN policy destinations,
   inbound replies and non-DHCP WAN variants need their own controlled checks.
   The host/model tests do not substitute for those packet tests.
5. **Restart adoption.** A single known WAN can be reconstructed. Mixed or
   unattributed surviving flows remain transitional; no flow is reset to force
   adoption. This can delay activation for a busy source. Only fresh scoped
   starts with attributable traffic were qualified here.

## Restoration evidence

After stopping the helper and restoring the pins:

- Saved production script sizes and SHA-256 values matched exactly for the
  watchdog, watchdog DDNS helper, firewall-start, services-start and wan-event.
- iptables policy/rule lines matched the baseline (counter/timestamp differences
  ignored); the routing-table dump matched exactly; the RPDB rule multiset
  matched. Re-adding the macOS pin moved its position among independent
  same-priority host rules; its selector, table and priority were unchanged.
- The GUI pin NVRAM string matched exactly. The phone again uses its original
  Secondary pin and the macOS client its original Primary pin.
- Queue listings were empty, module listings matched, temporary router roots
  were archived privately and removed, and the local test-page server stopped.
- Production watchdog status showed both WANs usable with zero loss to both
  probe targets. No actual failure was induced.

## Final local validation

```
make host-tests                         # 110 checks, 0 failures
python3 -m unittest discover -s tests    # 86 tests, OK
sh -n wan-affinity.sh                    # OK
```

The host C checks also passed AddressSanitizer and UndefinedBehaviorSanitizer.
The static Linux adapter compiled with `-Wall -Wextra -Werror` and was the build
used for the macOS forwarding matrix. All source remains local/uncommitted;
no public release is implied by these passing checks.
