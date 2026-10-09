# Loss-aware Dual-WAN watchdog

In Asuswrt-Merlin Dual WAN **load-balance mode**, GUI source-IP rules can leave
clients pinned to a WAN that is connected but unusable. This watchdog redirects
those configured clients when both probe targets show severe packet loss, then
waits for sustained recovery before restoring their preferred WAN.

Tested on **RT-AX88U, Merlin 3004.388.12_2**, with IPv4 source-IP pins. It manages
only priority-100 rules represented in `wans_routing_rulelist`. It does not
change WAN enable switches, load-balancing weights, DNS, default routes, or
NAT/connection tracking. Unpinned clients and router-originated traffic are
outside its scope. Connections may reconnect when their outgoing WAN changes.

## Policy

| Setting | Default |
| --- | --- |
| Probe targets | Google `8.8.8.8`, Quad9 `9.9.9.9` |
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

## Probe and routing details

Each ping binds to the WAN IPv4 address. Before sending it, an `ip route get`
lookup must select the expected interface. Incorrect/unavailable routing yields
an unknown sample, rather than a healthy result or an ISP-loss diagnosis. An
enabled WAN without an IPv4 lease yields failed samples.

Cloudflare's `1.1.1.1`/`1.0.0.1` were deliberately excluded: the tested router
inherited special DNS host routes through the active WAN in both WAN tables
as WAN state changed. Those are not dependable independent WAN probe paths.
This does not mean DNS always uses the backup. Custom routing to the default
probe targets can likewise prevent valid measurements; inspect the status.

Both WANs are measured before routing decisions. Desired priority-100 rules
are added before opposite rules are removed. Failed additions leave the old
rule in place; partial changes retain the failover marker for retries. The
script re-applies failover rules when Merlin rebuilds them, provided the backup
still qualifies as healthy. It infers failover state from existing configured
client routes at startup if a marker is missing.

An exclusive `flock` prevents duplicate daemons. Time comes from `/proc/uptime`,
so clock synchronization cannot accelerate recovery. Decision transitions go
to syslog; status is replaced atomically each cycle. DNS- or HTTPS-only failures
are not distinct failure triggers, and ICMP health can differ from application
health.

## Install or upgrade

Enable **Administration → System → Enable JFFS custom scripts and configs**.
Configure Dual WAN load balancing and the source-IP routing rules in the GUI.
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
markers in `/tmp/wan-watchdog` in place during upgrades. The process resets its
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

Verify and stop the PID as above, then wait for probe children to release the
lock. To restore an earlier watchdog, set `backup` to the path saved during
installation and run:

```sh
cp -p "$backup/wan-watchdog" /jffs/scripts/wan-watchdog.rollback
sh -n /jffs/scripts/wan-watchdog.rollback
mv /jffs/scripts/wan-watchdog.rollback /jffs/scripts/wan-watchdog
/jffs/scripts/wan-watchdog >/tmp/wan-watchdog.log 2>&1 </dev/null &
```

Keep current routes and failover markers; do not restore an old PID or blindly
replay a routing snapshot. An older single-ping watchdog will again restore
clients after one successful reply.

If removing a first-time installation, stop the daemon and remove only its
startup command and script. Remove only the logging line/hook you added to
`wan-event`, preserving unrelated behavior. Reapply the GUI Dual WAN routing
preferences when ready to restore configured pins; removal does not itself
rewrite the current client rules. Do not replace the entire `services-start`
from backup if other tools have been added since.

## Validation and compatibility

The 19 simulated tests cover loss/outage decisions in both directions, bad
backups, single-target failure, disabled WANs, recovery timing/reset, route
validation, partial routing errors, rolling samples, warm-up and timing jitter:

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

The same 19 tests passed on the router's BusyBox shell. A real primary outage
redirected pinned clients to the backup; re-enablement, clean measurements,
the exact 120-second recovery period and automatic restoration were observed
live. Router syntax, source-bound probes, the duplicate-process lock and
preservation of GUI preferences were verified. No artificial outage, reboot,
other router model or subsequent firmware upgrade was tested.

[ASUS monitoring documentation](https://www.asus.com/support/faq/1037368/)
describes the stock consecutive-failure mechanism; the thresholds above belong
to this custom source-IP pin watchdog.
