# Loss-aware Dual-WAN watchdog

In Asuswrt-Merlin Dual WAN **load-balance mode**, a WAN can remain connected
while dropping enough traffic to be unusable. This watchdog redirects ordinary
IPv4 internet traffic from the active LAN bridges to the healthy WAN, then
removes its temporary overrides after sustained recovery.

**Whole-LAN failover is the default**, including clients without GUI WAN pins.
Set `FAILOVER_MODE=pinned` to retain the earlier pinned-clients-only policy.
Both modes redirect the router's configured IPv4 DNS resolver destinations
during failover. DNS settings themselves are not changed.

Tested on **RT-AX88U, Merlin 3004.388.12_2**. The script preserves local routes,
standard Merlin VPN policies, GUI source-IP preferences and load-balancing
settings. Whole-LAN mode uses temporary rules instead of rewriting the GUI's
priority-100 pins; an upgrade from the older pinned-rule version can restore
those preferences after the whole-LAN override is in place. Connections can
reconnect when their outgoing WAN changes. IPv6 and custom VPN/routing layouts
outside the recognized policies require separate validation.

## Policy

| Setting | Default |
| --- | --- |
| Preferred probe targets | Google `8.8.8.8`, Quad9 `9.9.9.9`; substitutes are selected if those addresses are configured DNS servers |
| Probe cadence | About 2 seconds; both targets on both enabled WANs in parallel |
| Probe limits | One ICMP request, one-second timeout/deadline |
| Rolling loss window | 30 seconds |
| Measurable window | 30 seconds since reset; at least 10 samples spanning 26 seconds |
| Degradation trigger | At least 30% loss on **both** targets |
| Total-outage trigger | No replies from either target for 12 seconds |
| Eligible destination | At most 10% loss on each target; both newest probes successful |
| Recovery | At most 5% loss on each target for 120 uninterrupted seconds; both newest probes successful |
| Both WANs unhealthy/unmeasured | Keep current routes |
| Manually disabled WAN | Never a destination or eligible for restoration |

The outage trigger is an additional failure path, not a mandatory minimum wait:
the degradation threshold can be reached first. Either path requires a measured
healthy destination. After startup, that destination normally needs about
30 seconds of warm-up.

Loss percentages round up. At the nominal 15 samples, one loss is 7% and five
losses are 34%. The minimum sample count allows 2–3-second cycles under timeout
or CPU load without hiding a healthy backup. Sparse/invalid windows never
qualify as healthy.

One new loss or invalid probe resets the recovery timer. Interface, lease,
gateway, or enable changes reset the measurement window. After connectivity
becomes clean, allow roughly **30 seconds to clear the loss history, then two
clean minutes** before restoration. Restarting the daemon also resets that
verification period but preserves active failover markers.

Constants are near the top of [wan-watchdog.sh](wan-watchdog.sh).

## Configuration and rule ownership

Settings are near the top of [wan-watchdog.sh](wan-watchdog.sh). The mode and
routing settings also accept environment overrides in `services-start`:

```sh
# Default, no override needed:
FAILOVER_MODE=whole_lan /jffs/scripts/wan-watchdog >/tmp/wan-watchdog.log 2>&1 </dev/null &
# Earlier behavior, for GUI-pinned clients only:
FAILOVER_MODE=pinned /jffs/scripts/wan-watchdog >/tmp/wan-watchdog.log 2>&1 </dev/null &
```

Use one startup command, not both. Defaults:

| Setting | Default | Purpose |
| --- | --- | --- |
| `FAILOVER_MODE` | `whole_lan` | Whole-LAN or `pinned` client policy |
| `FAILOVER_PRIORITY` | `88` | Temporary LAN and router-resolver overrides |
| Derived guard priority | `87` | Preserve existing VPN rules before the override |
| Derived probe priority | `86` | Route source-bound health checks independently |
| `FAILOVER_TABLE` | `20188` | Private table with the healthy WAN's explicit gateway |
| `PROBE_TABLE0`, `PROBE_TABLE1` | `20186`, `20187` | Independent WAN probe tables |
| `LAN_NETWORKS` | `auto` | Active LAN bridges (`br*` and the configured LAN interface), or a space-separated IPv4 CIDR list |
| `DNS_SERVERS` | `auto` | Active WAN/resolver/Stubby IPv4 addresses, or an explicit space-separated list |
| `OWNERSHIP_DIR` | `/jffs/wan-watchdog` | Persistent write-ahead ownership records |

The table IDs are leased exclusively by the watchdog. A pre-existing table or
foreign rule in its reserved priorities/table namespace causes a refusal;
the script does not overwrite or delete the other owner's rules. Change these
advanced settings only after stopping the daemon and running `--cleanup` with
its previous settings.

`owned-rules` stores each rule's complete priority, match selectors and
lookup/goto action. Its intent is atomically recorded **before** the kernel add.
Cleanup deletes one exact matching instance for each recorded rule, then removes
the record. It never runs a deletion using priority 88 alone. `owned-tables`
records successfully leased IDs; cleanup flushes only those tables, and refuses
to flush a table still referenced by a foreign rule. Table IDs must not be
shared with other tools.

Ownership survives daemon restarts and loss of `/tmp` state. If the persistent
ledger is lost while its rules/tables remain in the kernel, initialization
refuses to guess ownership. Restore the ledger from backup or inspect the
remaining exact rules. Do not delete `/jffs/wan-watchdog` to stop failover.
Normal daemon termination keeps the forwarding rules in place for a restart;
`--cleanup` removes them explicitly after the daemon is stopped. The ledger is
written only when ownership changes, not on each successful probe.

### VPN, local traffic, DNS and probes

Existing native rules remain present. For recognized VPN-client table lookups
(`ovpnc*`, `wgc*`, IDs 111–120) and destination-specific `main` rules at/after the
override priority, priority-87 guards use the same selectors and jump to the
original rule priority. This lets the original VPN policy and its route table
make the decision; priority 88 does not silently overtake those VPN rules.

The failover table has explicit healthy-WAN routes, avoiding inherited WAN-table
DNS host routes. It uses `throw` routes for directly connected and VPN prefixes,
so local-network destinations fall through to the native routing policies.
Router-originated traffic to configured resolver addresses gets separate
`iif lo` overrides. The tested kernel ignores newer port selectors, so these
match resolver **addresses**, not only ports 53/853; other router-originated
traffic to those same addresses follows the override too. IPv6, unconfigured
ad-hoc resolvers and encrypted DNS inside a browser are not managed here.

Health probes use their WAN's own leased table and source address. Resolver
addresses are excluded from probe selection, which avoids DNS redirection and
reverse-path-filter interactions. If a preferred target is also configured for
DNS, the script selects a substitute from `PROBE_FALLBACKS` (default:
`8.8.4.4 9.9.9.11 1.1.1.2 208.67.222.222`). If there are not two distinct
non-resolver candidates, it refuses to start. Changing effective targets resets
the measurement/recovery windows. Confirm that the selected targets answer
ICMP on both WANs in your network.

Before pinging, `ip route get` must report the expected interface. Invalid
routing yields an unknown sample; missing IPv4 leases yield losses. DNS- or
HTTPS-only failures are not independent WAN failure triggers. An exclusive
`flock` prevents duplicate daemons, `/proc/uptime` avoids clock jumps, and
status is replaced atomically each cycle.

## Install or upgrade

Enable **Administration → System → Enable JFFS custom scripts and configs**.
Configure Dual WAN load balancing in the GUI; source-IP pins are optional in whole-LAN mode.
Use DHCP reservations for pinned clients. Commands below assume an SSH alias
named `router`; substitute your router's address/alias.

Upload the source from this repository's root. Older Merlin installations lack
an SFTP subsystem, so `scp -O` selects the legacy SCP protocol:

```sh
scp -O wan-watchdog/wan-watchdog.sh router:/tmp/wan-watchdog.new
```

On the router, syntax-check it and back up the existing files **before replacing
anything**:

```sh
sh -n /tmp/wan-watchdog.new
backup=/jffs/backups/wan-watchdog/$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup"
chmod 700 "$backup"
for file in wan-watchdog services-start wan-event; do
    [ ! -f "/jffs/scripts/$file" ] || cp -p "/jffs/scripts/$file" "$backup/"
done
[ ! -d /jffs/wan-watchdog ] || cp -pR /jffs/wan-watchdog "$backup/ownership"
ip rule show > "$backup/ip-rules.txt"
ip route show table all > "$backup/ip-routes.txt"
echo "$backup"
```

Keep the printed backup path for rollback. If a watchdog is already running,
verify its PID before stopping it:

```sh
pid=$(cat /tmp/wan-watchdog/wan-watchdog.pid)
tr '\000' ' ' < /proc/$pid/cmdline
```

Only if that command identifies `/jffs/scripts/wan-watchdog`, stop it:

```sh
kill "$pid"
while kill -0 "$pid" 2>/dev/null; do sleep 1; done
```

Wait for any bounded probe children to release the lock, then install:

```sh
mkdir -p /tmp/wan-watchdog
while ! flock -n /tmp/wan-watchdog/lock true; do sleep 1; done
cp /tmp/wan-watchdog.new /jffs/scripts/wan-watchdog.new
chmod 755 /jffs/scripts/wan-watchdog.new
mv /jffs/scripts/wan-watchdog.new /jffs/scripts/wan-watchdog
```

Merge the command from [services-start.example](services-start.example) into
`/jffs/scripts/services-start` before any unconditional `exit`. If the hook is
absent, create it with a `#!/bin/sh` first line. Preserve other commands,
including the [Namecheap DDNS](../namecheap-ddns/) cron registration if used.
Make the hook executable:

```sh
chmod 755 /jffs/scripts/services-start
```

The tested setup uses a logging-only `wan-event`; see
[wan-event.example](wan-event.example). Preserve unrelated actions in an
existing hook. Avoid another custom watchdog/event hook modifying the same
source rules, which can fight this daemon's state decisions.

Start the watchdog directly; do not rerun the whole startup hook:

```sh
/jffs/scripts/wan-watchdog >/tmp/wan-watchdog.log 2>&1 </dev/null &
cat /tmp/wan-watchdog/status
```

The status file appears after the first sampling cycle. Leave the active
markers and persistent ownership ledger in place during same-mode upgrades. The process resets its
measurement/recovery history automatically. No NVRAM commit or router reboot
is required for installation.

## Monitoring

```sh
cat /tmp/wan-watchdog/status
cat /tmp/wan-watchdog.log
grep -E 'wan-watchdog|wan-event' /tmp/syslog.log | tail -40
ip rule show
```

Status reports each WAN's reason, independent loss percentages, sample counts,
failover marker and monotonic recovery start time. `disabled`/`unknown` report
loss as unavailable. Runtime files live under `/tmp/wan-watchdog`; they disappear
on reboot. Decision logs are in `/tmp/syslog.log` and are also volatile.

## Rollback or removal

Verify and stop the PID as above. Before restoring a pre-v3 script or removing
this installation, remove the v3-owned rules with the **currently installed v3
script and its current settings**:

```sh
/jffs/scripts/wan-watchdog --cleanup
```

The command acquires the same daemon lock and refuses cleanup while it is
running. It removes exact recorded rules and leased table contents, preserving
unrelated priority-88 rules. Then set `backup` to your saved path and restore:

```sh
cp -p "$backup/wan-watchdog" /jffs/scripts/wan-watchdog.rollback
sh -n /jffs/scripts/wan-watchdog.rollback
mv /jffs/scripts/wan-watchdog.rollback /jffs/scripts/wan-watchdog
/jffs/scripts/wan-watchdog >/tmp/wan-watchdog.log 2>&1 </dev/null &
```

Do not replay a stale routing snapshot or restore an old PID. When restoring a
previous v3 installation, restore its matching ownership directory/configuration
as well if you changed it. For a first-time removal, remove only the watchdog's
script and startup/event commands, preserving unrelated hooks. If migrating
from pinned mode, reapply GUI preferences when ready; whole-LAN cleanup does
not rewrite priority-100 client rules. An older single-ping watchdog restores
clients after one successful reply again.

## Validation and compatibility

The 36 simulated tests cover loss/outage decisions in both directions, bad
backups, single-target failure, disabled WANs, recovery timing/reset, route
validation, partial routing errors, rolling samples, warm-up, timing jitter,
whole-LAN decisions, persistent ownership, collision refusal, exact cleanup,
VPN guards, LAN discovery and DNS/probe separation:

```sh
python3 wan-watchdog/tests/test_wan_watchdog.py
```

Run the same mock-based tests on a router shell against the installed source:

```sh
WAN_WATCHDOG_TEST_ROUTER=router \
WAN_WATCHDOG_TEST_SCRIPT=/jffs/scripts/wan-watchdog \
python3 wan-watchdog/tests/test_wan_watchdog.py
```

Router tests use temporary directories and mock routing/probes; they do not
change live WANs or client rules. Python 3.8+ runs the harness on the host;
Python is not needed on the router. The daemon needs Merlin's `nvram`, `ip`,
BusyBox `ping`, `awk`, `flock`, and `logger`.

The same 36 tests passed on the router's BusyBox shell. A real primary outage
redirected pinned clients to the backup; re-enablement, clean measurements,
the exact 120-second recovery period and automatic restoration were observed
live. Router syntax, source-bound probes, the duplicate-process lock and
preservation of GUI preferences were verified. Real-kernel v3 checks with a
dummy source subnet verified healthy-WAN forwarding, local/WireGuard routes,
router DNS redirection and independent probes. Cleanup restored the original
policy rules and OUTPUT chain byte-for-byte; no LAN outage was induced. No artificial outage, reboot,
other router model or subsequent firmware upgrade was tested.

[ASUS monitoring documentation](https://www.asus.com/support/faq/1037368/)
describes the stock consecutive-failure mechanism; the thresholds above belong
to this custom source-IP pin watchdog.
