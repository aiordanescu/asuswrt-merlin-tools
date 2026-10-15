# Loss-aware Dual-WAN watchdog

The native implementation uses a persistent C monitor and shared health
socket, with a bounded shell routing/DDNS worker. The original shell monitor
remains available as a reference and rollback option. See [NATIVE.md](NATIVE.md) for build, guarded installation,
operation and rollback, and [the public protocol](../common/wan-health/PROTOCOL.md)
for independent subscribers. Measured results and remaining gates are in
[QUALIFICATION-2026-10-15.md](QUALIFICATION-2026-10-15.md).

In Asuswrt-Merlin Dual WAN **load-balance mode**, a WAN can remain connected
while dropping enough traffic to be unusable. This watchdog redirects ordinary
IPv4 internet traffic from the active LAN bridges to the healthy WAN, then
removes its temporary overrides after sustained recovery.

**Whole-LAN failover is the default**, including clients without GUI WAN pins.
Set `FAILOVER_MODE=pinned` to retain the earlier pinned-clients-only policy.
Both modes redirect the router's configured IPv4 DNS resolver destinations
during failover. DNS settings themselves are not changed. With the DDNS helper and postconf
integration installed, **DDNS Auto** also follows measured WAN health; explicit
DDNS interface choices stay fixed. **Before installing, check WAN → DDNS →
Interface**: Auto is the firmware default and enables DDNS movement with this
integration. Read [DDNS Auto](#ddns-auto-check-the-ui-setting) for eligibility
and provider limits.

Tested on **RT-AX88U, Merlin 3004.388.12_2**. The script preserves local routes,
standard Merlin VPN policies, GUI source-IP preferences and load-balancing
settings. Whole-LAN mode uses temporary rules instead of rewriting the GUI's
priority-100 pins; an upgrade from the older pinned-rule version can restore
those preferences after the whole-LAN override is in place. Connections can
reconnect when their outgoing WAN changes. IPv6 and custom VPN/routing layouts
outside the recognized policies require separate validation.

## Choose an implementation

| Implementation | Installation and operation |
| --- | --- |
| Native C monitor + shell actuator | [NATIVE.md](NATIVE.md): build a static aarch64 bundle, qualify shadow mode, then use the guarded shell-to-native installer. Current target: RT-AX88U / Merlin 3004.388.12_2. |
| Original shell monitor | [Shell installation and upgrade](#shell-installation-and-upgrade), below. Also establishes the reference/helper/hooks before a first native installation. |

The native monitor works without subscribers. Its read-only Unix socket
publishes complete, versioned health snapshots for multiple independent local
consumers. No affinity component is required or included in this update.
The watchdog retains routing failover, restoration and DDNS responsibilities.

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

The shell constants are near the top of [wan-watchdog.sh](wan-watchdog.sh).
The native thresholds are compiled into [ww_core.c](src/ww_core.c) and match
that reference; editing shell constants does not reconfigure the C classifier.

## Configuration and rule ownership

Shell settings are near the top of [wan-watchdog.sh](wan-watchdog.sh). The mode and
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

Before probing, `ip route get` must report the expected interface. Invalid
routing yields an unknown sample; missing IPv4 leases yield losses. DNS- or
HTTPS-only failures are not independent WAN failure triggers. An exclusive
`flock` prevents duplicate daemons. The shell uses `/proc/uptime`; the native
monitor uses monotonic timers and cross-checks suspend gaps. Status is replaced
atomically; the socket is the native live-health interface.

## Shell installation and upgrade

Enable **Administration → System → Enable JFFS custom scripts and configs**.
Configure Dual WAN load balancing in the GUI; source-IP pins are optional in whole-LAN mode.
Use DHCP reservations for pinned clients. Commands below assume an SSH alias
named `router`; substitute your router's address/alias.

Upload the source from this repository's root. Older Merlin installations lack
an SFTP subsystem, so `scp -O` selects the legacy SCP protocol:

```sh
scp -O wan-watchdog/wan-watchdog.sh router:/tmp/wan-watchdog.new
scp -O wan-watchdog/wan-watchdog-ddns.sh router:/tmp/wan-watchdog-ddns.new
```

On the router, syntax-check it and back up the existing files **before replacing
anything**:

```sh
sh -n /tmp/wan-watchdog.new
sh -n /tmp/wan-watchdog-ddns.new
backup=/jffs/backups/wan-watchdog/$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup"
chmod 700 "$backup"
for file in wan-watchdog wan-watchdog-ddns inadyn.postconf services-start wan-event; do
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
cp /tmp/wan-watchdog-ddns.new /jffs/scripts/wan-watchdog-ddns.new
chmod 755 /jffs/scripts/wan-watchdog-ddns.new
mv /jffs/scripts/wan-watchdog-ddns.new /jffs/scripts/wan-watchdog-ddns
```

Merge [inadyn.postconf.example](inadyn.postconf.example) into
`/jffs/scripts/inadyn.postconf`, before any existing early `exit`. **Keep your
existing commands**, especially provider-specific fixes. Make the hook executable.
The marker comment identifies the integration; keep it together with the helper
call. If you omit the helper or this integration, DDNS stays under native control.

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

The commands below work for either implementation. Native lifecycle controls
and JSON health inspection are in [NATIVE.md](NATIVE.md). Native worker errors,
eligibility, sequence and desired/applied routing generations appear in status;
DDNS diagnostics remain in `/tmp/wan-watchdog/ddns-status`.

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

For a native-to-shell rollback, use the saved reference and shared ledger:

```sh
sh /jffs/wan-watchdog-native/rollback.sh --rollback
```

That preserves forwarding state and restores the original entry point.
See [native operations](NATIVE.md#install-and-operate) before deleting an
inactive native bundle. The older-version rollback/removal procedure below
applies to the retained shell installation.

Verify and stop the PID as above. Before restoring a pre-v3 script or removing
this installation, remove the v3-owned rules with the **currently installed v3
script and its current settings**:

```sh
/jffs/scripts/wan-watchdog --cleanup
```

The command acquires the same daemon lock and refuses cleanup while it is
running. It removes exact recorded rules and leased table contents, preserving
unrelated priority-88 rules. It also withdraws its DDNS Auto selection and asks
the native updater to regenerate its configuration when needed. Then set `backup` to your saved path and restore:

```sh
cp -p "$backup/wan-watchdog" /jffs/scripts/wan-watchdog.rollback
sh -n /jffs/scripts/wan-watchdog.rollback
mv /jffs/scripts/wan-watchdog.rollback /jffs/scripts/wan-watchdog
# Restore the saved postconf hook and helper too, if the backup has them.
[ ! -f "$backup/inadyn.postconf" ] || cp -p "$backup/inadyn.postconf" /jffs/scripts/inadyn.postconf
[ ! -f "$backup/wan-watchdog-ddns" ] || cp -p "$backup/wan-watchdog-ddns" /jffs/scripts/wan-watchdog-ddns
/jffs/scripts/wan-watchdog >/tmp/wan-watchdog.log 2>&1 </dev/null &
```

Do not replay a stale routing snapshot or restore an old PID. When restoring a
previous v3 installation, restore its matching ownership directory/configuration
as well if you changed it. For a first-time removal, also remove just the marked DDNS integration block
from postconf and its helper, and the watchdog's startup/event commands, preserving unrelated hooks. If migrating
from pinned mode, reapply GUI preferences when ready; whole-LAN cleanup does
not rewrite priority-100 client rules. An older single-ping watchdog restores
clients after one successful reply again.

## Validation and compatibility

The 65 simulated tests cover loss/outage decisions in both directions, bad
backups, single-target failure, disabled WANs, recovery timing/reset, route
validation, partial routing errors, rolling samples, warm-up, timing jitter,
whole-LAN decisions, persistent ownership, collision refusal, exact cleanup,
VPN guards, LAN discovery and DNS/probe separation. DDNS tests cover UI Auto
versus fixed selections, both switch directions, recovery reset, public-address
eligibility, native provider callbacks, DNS confirmation, postconf preservation,
provider-route ownership, and probe/cleanup coexistence:

```sh
python3 -m unittest discover -s wan-watchdog/tests
```

Run the same mock-based tests on a router shell against the installed **shell
reference** (not the native startup wrapper):

```sh
WAN_WATCHDOG_TEST_ROUTER=router \
WAN_WATCHDOG_TEST_SCRIPT=/jffs/scripts/wan-watchdog \
WAN_WATCHDOG_TEST_DDNS_HELPER=/jffs/scripts/wan-watchdog-ddns \
python3 -m unittest discover -s wan-watchdog/tests
```

When the native service is installed, set `WAN_WATCHDOG_TEST_SCRIPT` to
`/jffs/wan-watchdog-native/wan-watchdog-legacy.sh` instead. Native C and
socket tests/build commands are in [NATIVE.md](NATIVE.md); see
[the qualification report](QUALIFICATION-2026-10-15.md) for the complete evidence.

These shell tests use temporary directories and mock routing/probes; they do not
change live WANs or client rules. Python 3.8+ runs the harness on the host;
Python is not needed on the router. The shell monitor needs Merlin's `nvram`, `ip`, BusyBox `ping`, `awk`,
`flock`, and `logger`. Native monitoring sends ICMP directly; its routing worker
still uses the firmware's shell utilities. DDNS also needs native Inadyn,
`service`, `sleep`, and `nslookup`; lookups are bounded without a `timeout` utility.

The same 65 tests passed on the router's BusyBox shell. A real primary outage
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


## DDNS Auto: check the UI setting

**Look at WAN → DDNS → Interface before installing this integration.** Auto is
Merlin's default (`ddns_wan_unit=-1`); your saved setting may differ. Installation
does not change it.

| DDNS Interface | Behavior with the helper installed |
| --- | --- |
| **Auto** | Prefer the first WAN with a directly assigned public IPv4, normally Primary. On measured degradation/outage, choose the other WAN only when healthy and public. Return after a full measured clean window and 120 clean seconds. |
| **Primary WAN** | Keep DDNS on Primary, even if LAN traffic fails over. |
| **Secondary WAN** | Keep DDNS on Secondary, even if LAN traffic fails over. |

Use **Auto** if you want the hostname to move between public WANs during
watchdog failover. Use a fixed choice if the hostname must stay on that WAN.
The helper works in both `whole_lan` and `pinned` watchdog modes. Emergency
movement to a healthy public WAN can occur sooner if the current destination
itself fails; ordinary return still requires sustained recovery. DDNS Auto
selection persists across watchdog restarts, while its recovery timer starts
over. Both WANs bad/unmeasured means no new publication request. If Auto has no
public WAN candidates, the native behavior is left in place.

Stock Auto selects the first WAN the firmware regards as connected with a
public address; it does not know this watchdog's packet-loss decision. We keep
the native updater, credentials, provider implementation, and success callback.
The postconf hook changes only its address source and interface when the UI is
Auto. Neither `ddns_wan_unit` nor native primary flags are rewritten. The GUI's
native last-WAN indicator can reflect the firmware's original selection; the
watchdog status shows the effective selection and address.

The helper resolves the provider's IPv4 update endpoints through the router's
resolver and installs exact owned, router-only priority-86 destination rules
using the selected WAN's probe table. These narrow rules keep the provider
request off the degraded WAN without rerouting other router traffic. They
remain while Auto is managed, refresh with endpoint resolution, and are removed
when a fixed UI choice is detected or on cleanup. Requests retry at most once
per minute, do not interrupt a running Inadyn process, and run outside the
probe loop. The integration invalidates only the selected hostname's native
cache to bypass the firmware's pre-hook no-change check.

The status distinguishes an update request, native provider success, and public
DNS confirmation. Success requires the native success flag **and the expected
published address**. A bounded background check then asks Cloudflare/Google
resolvers for the hostname's A record. Cached DNS can take time to reflect an
update; lack of DNS confirmation alone does not repeatedly resend an already
successful provider update. This verifies publication, not inbound reachability.

### DDNS scope and limitations

- This release handles **IPv4-only networks with directly assigned public WAN
  IPv4 addresses**. An active IPv6 service leaves DDNS under native control.
- Private WANs, CGNAT, and modem-NAT WANs needing external-address discovery are
  not Auto destinations. The watchdog does not guess whether an externally
  discovered address accepts incoming traffic. For example, T-Mobile CGNAT
  does not become an inbound backup by changing DDNS.
- Supported native providers: Namecheap, No-IP, DynDNS, Cloudflare, ZoneEdit,
  EasyDNS, DNS-O-Matic, Selfhost, FreeDNS, FreeMyIP, DNSPod, and basic ASUS DDNS
  (`.com`/`.cn`). Account-bound ASUS replacement/token profiles, Custom/Oray,
  unknown providers and custom `inadyn.conf` / `inadyn.conf.add` files stay native.
  Endpoint mappings come from the tested firmware's provider code and need
  review on upgrades. Arbitrary other postconf rewrites are not validated.
- The normal generated single-provider Inadyn configuration is required. The
  supplied hook preserves provider credentials and URL adjustments in place;
  it creates no additional credentials file and never prints credentials.
- When a user changes Auto to a fixed interface, our selection/provider routes
  are withdrawn and native regeneration is requested. No WAN service restarts
  or changes to physical WAN assignments occur.
- This does **not** move port forwards, VPN listeners, or fix incoming reply
  routing. A hostname pointing to the backup does not establish that its
  services are reachable there; configure/verify that separately.
- Native periodic DDNS verification can also request redundant refreshes because
  its Auto selection is still based on firmware connection status.
- The separate Namecheap checker is unchanged. With Auto and two public WANs,
  its older primary-based verification can request redundant native refreshes;
  the postconf hook still supplies the watchdog-selected address. This does
  not affect a fixed Primary/Secondary setting.
- If changing `STATE_DIR` or `OWNERSHIP_DIR`, supply the same paths to the helper
  in the postconf call as to the daemon. Native service hooks do not inherit
  your daemon's environment. Default paths need no extra configuration.

Additional status and logs:

```sh
cat /tmp/wan-watchdog/status
cat /tmp/wan-watchdog/ddns-status
grep wan-watchdog-ddns /tmp/syslog.log | tail -30
```

DDNS transition/provider outcomes were tested with mocks on the host and the
router's BusyBox shell. The actual installed Inadyn parser accepts the generated
configuration with dummy credentials. Live installation retained the user's
fixed Primary selection; no artificial WAN outage, change to the GUI selection,
or publication of a different address was performed. A real public-backup
publication and inbound service access have not been tested on this router.
