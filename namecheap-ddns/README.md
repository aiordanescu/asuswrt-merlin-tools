# Namecheap DDNS repair for Merlin Dual WAN

A Namecheap DDNS problem was observed on an RT-AX88U running Asuswrt-Merlin 3004.388.12_2 with Dual WAN in load-balance mode. DDNS was set to Primary WAN, but the public DNS record had the wrong address while the router reported a successful update. Applying the DDNS settings and the daily forced refresh both skipped the update.

This workaround was installed and tested. It keeps the built-in DDNS client and existing credentials, adds the missing IP parameter to its Namecheap request, and checks the full public hostname separately.

## Setup and symptoms

- Primary WAN: fiber with a directly assigned public IPv4 address.
- Secondary WAN: cellular connection behind NAT.
- Dual WAN mode: load balance.
- DDNS provider: WWW.NAMECHEAP.COM.
- DDNS interface: Primary WAN.
- Host Name: home.
- Domain Name: example.com (anonymized).
- WAN IP and hostname verification: enabled, every 30 minutes.
- Forced update interval: one day.
- DDNS IPv6 updates: disabled.

For example, the primary WAN and router's DDNS cache could both show 198.51.100.10, while home.example.com resolves publicly to 203.0.113.20. Those are documentation-only example addresses.

The logs showed:

```text
watchdog: Forced DDNS update (after 1 days)
rc_service: watchdog ...:notify_rc restart_ddns
ddns: IP address, server and hostname have not changed since the last update.
```

## Observed firmware behavior

**1. The generated Namecheap request does not include the selected IP.**

The stock configuration contained this URL template:

```text
ddns-path = "/update?domain=%u&password=%p&host=%h"
```

Namecheap's documentation says that when ip is omitted, it uses the address the request comes from. In Dual WAN load-balance mode, selecting Primary WAN for DDNS determines which interface IP inadyn checks, but the update connection can leave through another WAN. That is the likely explanation for the original wrong record; The historical update route was not captured.

The bundled inadyn documentation already gives this Namecheap example:

```text
ddns-path = "/update?domain=%u&password=%p&host=%h&ip=%i"
```

That explicit IP removes the dependency on the request's outgoing WAN.

**2. The periodic hostname verification does not combine Namecheap's two fields.**

The firmware's verifier uses the stored Host Name, which is home, rather than constructing home.example.com from Host Name plus Domain Name. A lookup of home failed on the tested router, so the verifier did not repair the actual public DNS record.

Keep Host Name = home and Domain Name = example.com. Putting the full domain into Host Name is not the fix: Namecheap expects the subdomain and domain separately.

**3. Restarting DDNS does not necessarily force an update.**

The native updater can return early when the WAN address, provider, hostname, and existing cache still match. This occurred even after the watchdog logged a daily forced refresh. Removing the volatile inadyn cache before requesting the update bypasses that check.

This cache behavior is broader than Namecheap. The missing-IP and split-hostname issues are the parts relevant to the Namecheap/Dual WAN combination.

## Workaround

There are two small scripts:

- inadyn.postconf adds &ip=%i whenever Merlin generates the Namecheap configuration. It is idempotent and only applies to Namecheap.
- namecheap-ddns-check verifies the full public name and requests a native update after clearing stale cache if the record differs from the selected public WAN IPv4, or the configured forced-refresh interval is due.

The cron scheduler starts the checker every five minutes. The checker stores its last check time and exits immediately until the configured verification interval has elapsed, with a minimum of 30 minutes. With a 30-minute verification interval, that means a DNS lookup roughly every 30 minutes, not every five minutes. A healthy record causes no update unless the daily refresh is due.

The scripts read the GUI settings at runtime. No DDNS password needs to be pasted into either script; Merlin continues generating the credential-bearing configuration itself.

## Installation

Enable JFFS custom scripts/configs under Administration > System.

If you already have inadyn.postconf, merge the Namecheap change into that hook rather than overwriting it. Preserve any existing services-start commands as well.

Upload [inadyn.postconf](inadyn.postconf) and
[namecheap-ddns-check](namecheap-ddns-check) from this repository's root:

```sh
scp -O namecheap-ddns/inadyn.postconf router:/tmp/inadyn.postconf.new
scp -O namecheap-ddns/namecheap-ddns-check router:/tmp/namecheap-ddns-check.new
```

The `router` hostname is an example SSH alias; substitute yours. `scp -O` uses
the legacy SCP protocol supported by the tested router.

On the router, validate and back up the existing files before installation:

```sh
sh -n /tmp/inadyn.postconf.new
sh -n /tmp/namecheap-ddns-check.new
backup=/jffs/backups/namecheap-ddns/$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup"
chmod 700 "$backup"
for file in inadyn.postconf namecheap-ddns-check services-start; do
    [ ! -f "/jffs/scripts/$file" ] || cp -p "/jffs/scripts/$file" "$backup/"
done
echo "$backup"
```

Keep the backup path. If no `inadyn.postconf` exists, copy the supplied hook.
If one exists, merge this hook's Namecheap logic into it, preserving the existing
logic; its early exits must not skip other providers' existing work.

```sh
if [ ! -f /jffs/scripts/inadyn.postconf ]; then
    cp /tmp/inadyn.postconf.new /jffs/scripts/inadyn.postconf
else
    echo 'Merge the Namecheap logic into your existing inadyn.postconf.'
fi
cp /tmp/namecheap-ddns-check.new /jffs/scripts/namecheap-ddns-check.new
mv /jffs/scripts/namecheap-ddns-check.new /jffs/scripts/namecheap-ddns-check
```

Make both executable:

```sh
chmod 755 /jffs/scripts/inadyn.postconf /jffs/scripts/namecheap-ddns-check
```

See [services-start.example](services-start.example). Add this line to your existing /jffs/scripts/services-start so the schedule is registered at boot:

```sh
cru a NamecheapDDNS '*/5 * * * * /jffs/scripts/namecheap-ddns-check'
```

If services-start does not exist, create it with a #!/bin/sh first line and that cron command, then make it executable. If it already exists, place the command before any unconditional exit. Do not replace other startup commands.

Register the schedule now and run the first check:

```sh
chmod 755 /jffs/scripts/services-start
cru a NamecheapDDNS '*/5 * * * * /jffs/scripts/namecheap-ddns-check'
/jffs/scripts/namecheap-ddns-check --check-now
```

The --check-now option bypasses the interval timer; it does not unconditionally force an update.

## Validation and monitoring

On the tested router, the checker detected the mismatch, cleared the cache, and invoked the native updater. The logs confirmed that inadyn.postconf ran and that the generated URL included &ip=%i.

A separate primary-WAN HTTPS update returned ErrCount=0, Done=true, and the expected public IPv4. A native inadyn --once --force run also completed successfully with that IP. Both Namecheap authoritative nameservers, Cloudflare DNS, and Google DNS subsequently returned the primary WAN address. A further checker run requested no additional update.

Useful checks from the router shell:

```sh
cru l
/jffs/scripts/namecheap-ddns-check --check-now
grep -Ei 'namecheap-ddns|ddns|inadyn' /tmp/syslog.log | tail -40
nslookup home.example.com 1.1.1.1
nslookup home.example.com 8.8.8.8
```

Replace example.com in those diagnostic commands with your own domain. The scripts themselves derive it from the GUI.

The native client's short-name resolution warning for home can still appear. The additional verifier checks the full name.

## Scope and limitations

Tested on RT-AX88U with Merlin 3004.388.12_2 and the setup above. Boot registration is installed persistently in JFFS, but reboot behavior, other models, and later firmware upgrades have not been tested.

The checker targets a selected WAN with a directly assigned public IPv4. It skips disconnected WANs and private/CGNAT addresses. It does not provide DDNS failover through a secondary WAN behind CGNAT, handle IPv6, apex-host @, wildcard-host *, or multiple hostnames. The example is for a normal subdomain such as home.

The extra DNS verification follows the GUI verification toggle. It tries Cloudflare and falls back to Google when the first lookup fails; a lookup failure alone does not request a repair. Allow ordinary DNS queries to those resolvers if you use the script unchanged.

The existing WAN failover watchdog and policy routing were left untouched. This fixes DDNS publication and checking; it does not change Dual WAN routing.

## Upgrades and rollback

Back up the same three files before each upgrade. Preserve an existing
`inadyn.postconf` hook and all unrelated startup commands. Copy the updated
checker over its previous version and merge any hook changes. Re-registering
the named cron entry replaces its schedule rather than adding a second job.
The synthetic URL hook was also tested twice on the router: it added the IP
parameter once and preserved all existing URL placeholders. No router reboot
is required.

To restore a previous checker or hook, use the saved files in `$backup`. If the
hook was merged with other logic, undo just this project's changes instead of
replacing unrelated updates made since. Restore only the DDNS cron line in
`services-start`, not the entire startup script. Then regenerate the DDNS
configuration as described below. Do not restore old cached DNS/IP data.

## Removal

Delete the NamecheapDDNS cron registration line you added to services-start, then run:

```sh
cru d NamecheapDDNS
rm -f /jffs/scripts/namecheap-ddns-check
```

If inadyn.postconf was created solely for this workaround, delete it. If you merged the change into an existing hook, remove just the added Namecheap logic:

```sh
# Only if this hook contains solely the workaround:
rm -f /jffs/scripts/inadyn.postconf
```

Then regenerate the stock DDNS configuration:

```sh
rm -f /var/cache/inadyn/*.cache
service restart_ddns
```

That restores the original updater behavior, including the issues described above.

## References

- [Namecheap: dynamically updating the host IP with HTTPS](https://www.namecheap.com/support/knowledgebase/article.aspx/29/11/how-to-dynamically-update-the-hosts-ip-with-an-https-request/)
- [Merlin postconf hooks](https://github.com/RMerl/asuswrt-merlin.ng/wiki/Custom-config-files#postconf-scripts)
- [388.12_2: update cache check](https://github.com/RMerl/asuswrt-merlin.ng/blob/3004.388.12_2/release/src/router/rc/services.c#L4723)
- [388.12_2: Namecheap URL generation](https://github.com/RMerl/asuswrt-merlin.ng/blob/3004.388.12_2/release/src/router/rc/services.c#L4991)
- [388.12_2: periodic verification](https://github.com/RMerl/asuswrt-merlin.ng/blob/3004.388.12_2/release/src/router/rc/watchdog.c#L6972)
- [388.12_2: hostname helper](https://github.com/RMerl/asuswrt-merlin.ng/blob/3004.388.12_2/release/src/router/shared/misc.c#L6661)
- [Current bundled inadyn: documented Namecheap request with ip=%i](https://github.com/RMerl/asuswrt-merlin.ng/blob/main/release/src/router/inadyn/README.md#custom-ddns-providers)
- [Related 388.4 beta discussion, posts #174–175](https://www.snbforums.com/threads/asuswrt-merlin-3004-388-4-beta-is-now-available.86109/page-9)

The FIX linked in that older discussion resets the retry counter after a successful custom DDNS update. That line is already present in 388.12_2; it does not address the Namecheap URL or hostname checks above.
