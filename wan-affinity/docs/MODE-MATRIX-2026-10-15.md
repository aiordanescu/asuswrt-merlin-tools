# Sticky retest and operation without watchdog health data

Date: 2026-10-15. RT-AX88U, Merlin `3004.388.12_2`, Linux `4.1.51`
aarch64. These checks used the unchanged normal ARM build from the
[cleanup/latency follow-up](LIFECYCLE-FIX-2026-10-15.md), scoped exclusively to
the authorized Mac mini's home-LAN IPv4 address.

**The latest sticky failover/recovery retest passed. Both policies also passed
healthy native-only operation without watchdog health data.** The production
watchdog remained active throughout. This does not qualify an actual watchdog
stop/restart or native outage/recovery without enhanced health data.

## Passing matrix

Each row ran four concurrent fresh HTTPS requests, two bound UDP DNS queries,
and two bidirectional UDP/443 QUIC version negotiations. HTTPS public addresses
and the router's original/reply conntrack tuples verified actual WAN egress.
All client requests bound the home-LAN address; the mini's separate direct
modem interface could not substitute for router forwarding.

| Policy and health input | Expected and observed egress |
| --- | --- |
| Sticky, original GUI Primary pin intact, absent snapshot | Primary; helper passed pinned traffic without creating an association. |
| Sticky, unpinned, absent snapshot | Primary; helper actively admitted traffic using native readiness. |
| Affinity, unpinned, absent snapshot | Primary, its configured preferred WAN; helper actively admitted traffic using native readiness. |
| Sticky, private snapshot: both eligible | Primary. |
| Sticky, private snapshot: Primary unusable | Backup for fresh traffic. |
| Sticky, private snapshot: Primary recovery held | Backup retained. |
| Sticky, private snapshot: Primary eligible again | Backup retained while the source still had relevant tracked connections. |

All **56 checks passed** in the final guarded run: 28 HTTPS requests, 14 DNS
queries and 14 QUIC version negotiations. Both native-only cases reported
`enhanced=no`, an adopted source association, successful admissions, no refresh
failures and no uncertainty. Their snapshot path was a deliberately absent
file in the private test directory, rather than the watchdog's runtime path.

The sticky sequence finished with one failover, 45 admissions, 46 successful
refreshes, zero failed refreshes, zero unobserved admissions and no uncertainty.
Transient unobserved counts between admission and the next observation cleared
normally. The largest packet timestamp-to-helper-read sample was 591 us; this
small forwarding sample is not a throughput or latency bound.

## Test preparation and protection

An early attempt encountered a TLS reset. Inspection showed mixed-WAN states
and the helper's conservative transitional pass-through, so it was not counted
as a successful helper-routing test. Between helper restarts, temporarily
removing the pin had allowed native load balancing to admit background traffic
on different WANs. This is a test setup limitation, not evidence qualifying
mixed-state restart adoption.

The final harness kept the client's Primary routing pin effective until each
new helper had reconstructed a known Primary association. It restored that
routing protection before changing helper modes. Earlier mixed states drained
naturally; no connection flush or session reset forced adoption. An independent
600-second router cleanup guard was armed before live changes. Only the
client's exact GUI pin and matching routing rule were temporarily removed;
the original NVRAM pin string was restored without a flash commit.

Failures and recovery were simulated only through a private health snapshot
consumed by the scoped helper. Neither real WAN was toggled. There was no
router reboot, firewall rebuild, persistent installation, production watchdog
change or LAN-wide activation. All source and documentation remain local;
nothing was committed or pushed.

Afterward, exact firewall rule lines, the routing-rule multiset, unicast route
multiset, module list, production script hashes and original GUI pin string
matched the backup. Dynamic multicast membership/order is recorded separately
from the unicast routing comparison. No test process, bound queue, owned chain
or temporary router directory remained. Both WANs still reported zero probe
loss in the production watchdog's status.

## Deferred coverage

- [ ] Actually stop and restart the production watchdog while testing both
  policies, with a separately scheduled window and independent timed restart.
  Check snapshot loss/staleness, retained exclusions/assignments and publisher
  return. See the [integration test note](INTEGRATION.md#deferred-live-watchdog-stop-check).
- [ ] Native-only outage and recovery: the native-only rows above cover
  healthy readiness and forwarding, not an actual failed link or route.

The earlier wider-rollout gates remain: throughput/offload, reboot/firewall/
upgrade lifecycle, publisher integration, mixed-state restart and the wider
VPN/inbound/live-pin policy matrix. These scoped passes do not enable the LAN.
