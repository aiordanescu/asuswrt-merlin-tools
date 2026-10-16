# WAN Watchdog

The C monitor owns concurrent source-bound ICMP, deadlines, in-memory loss
windows, health/recovery classification, routing intent and publication. Its
event loop also handles routing notifications and independent subscribers.
It preserves the shell thresholds and whole-LAN/pinned responsibilities.

The shell stays as the routing/DDNS reference and rollback path.
`lib/ww-adapter.sh` sources it in a bounded worker receiving explicit numeric
intent. It preserves exact ownership, VPN guards, local-route exceptions and
DDNS policy. Five-second worker deadlines, process groups and parent-death handling
bound worker failures. Before mutation, the worker verifies that the lease,
interface, gateway and probe targets still match its decision. Monitoring and
publication continue while commands run. Pending decisions are discarded when
WAN configuration changes. Fact age is measured from collection start; its
11-second bound covers two serialized five-second workers and a probe deadline.
A failed worker immediately invalidates its facts. Status reports `fact_age_ms`
and `stale_fact_cycles`; these are separate from the 30-second loss window.
Network transmission errors on a
validated source/route count as lost attempts; local resource, permission and
address errors remain UNKNOWN.

The public [socket protocol](../common/wan-health/PROTOCOL.md) allows future
consumers without watchdog policy changes. Samples stay in memory. Status and
exclusion checkpoints stay in RAM under `/tmp`; flash writes retain the
existing ownership/transition purpose.

The [maintenance qualification](../MAINTENANCE-2026-10-15.md) records the
lifecycle, lease-change and slow-worker safeguards.

## Download and qualification

Download **`wan-watchdog-aarch64.tar.gz`** and **`SHA256SUMS`** from the
[prebuilt beta release](https://github.com/aiordanescu/asuswrt-merlin-tools/releases/tag/wan-tools-2026.10.15-beta.5)
to your computer. No compiler, Python or Entware is needed on the router.
Target: **aarch64 Linux**, tested on RT-AX88U / Merlin 3004.388.12_2.
Compare the archive's SHA-256 with its entry in `SHA256SUMS`, for example on
macOS:

```sh
shasum -a 256 wan-watchdog-aarch64.tar.gz
```

Use `sha256sum` on Linux. See [release verification](../RELEASES.md).
[Building from source](#optional-source-build) remains optional.

The bundle contains the daemon,
`wan-healthctl`, wrapper, actuator, frozen reference, installer and rollback;
test fixtures are excluded. The archive includes the MIT license,
static-runtime notices and a short installation README.
`--system-info` reports clock ticks for CPU
measurement. Shadow is the default and requires a private runtime directory;
active routing requires `--active`.

Router drivers use SSH alias `router` and a private multiplexed connection:

- `run_shadow.py`: read-only comparison with the live shell.
- `run_orchestrator.py`: real loopback ICMP, a file-only actuator, real recovery
  timing, failed restoration, monitor stall and hung-worker behavior.
- `run_maintenance.py`: lease-change, slow-worker, shadow recovery and restart diagnostics with file-only routing.
- `run_handoff.py`: real install/rollback of a private loopback service, preserving pinned mode and custom priority.
- `run_actuator.py`: real owned rules in private tables with a loopback-only
  source, followed by exact cleanup verification.

The watchdog build/tests do not require an affinity source tree. Consumer
integration tests and drivers belong to their respective components; their
previous development results are recorded separately in the
[qualification report](QUALIFICATION-2026-10-15.md).
The [WAN affinity guide](../wan-affinity/) describes the optional sticky-source
consumer and links its own current qualification results.

On the tested router, `build/test_socket` exercises the public server/client
with multiple readers, a slow reader, publisher restart and invalid/stale
frames. `build/test_probe` exercises raw ICMP on loopback. These executables
need Linux; the raw-probe test needs root/CAP_NET_RAW. They do not modify
production routing. Do not mistake the route-actuator driver for a read-only
test: it explicitly leases a private namespace and verifies cleanup.

## Install and operate

For a first installation, follow the [shell setup](README.md#shell-installation-and-upgrade)
first to establish the reference, DDNS helper and existing hooks. The C-monitor
bundle upgrades that running reference; it is not a fresh-install bypass.
Qualify shadow mode on the target firmware and preserve source/runtime backups.
The installer uses the standard `/jffs` and `/tmp/wan-watchdog` paths and
requires two eligible WANs with no active override. It supports first handoff
from the included shell reference or its reviewed predecessor, and upgrades
of the installed C monitor. Review locally edited shell policies before handoff.

At first handoff it captures only supported settings from the running owner's
environment, including pinned/whole-LAN mode, routing priorities and tables,
LAN/DNS scope and DDNS helper settings. They are saved privately in
`/jffs/wan-watchdog/service.conf`. No credentials or complete process environment
are copied. A `service.conf` left by an earlier rollback is replaced by the
running shell's settings at `--install`; `--upgrade` keeps the installed file.
This file becomes the configuration used for starts, upgrades and
rollback. Edit it to change installed service settings; keep `services-start`
as the existing launch hook. Its old environment overrides no longer replace
settings saved in this file.

Transfer the verified downloaded archive from your computer (an SSH alias
`router` is used here):

```sh
scp -O wan-watchdog-aarch64.tar.gz router:/tmp/
```

On the router, extract into a new private staging directory. Keep an existing
staging directory for inspection rather than overwriting it:

```sh
umask 077
mkdir /tmp/wan-watchdog-stage
tar -xzf /tmp/wan-watchdog-aarch64.tar.gz -C /tmp/wan-watchdog-stage
cd /tmp/wan-watchdog-stage
```

For the first shell-to-monitor handoff:

```sh
sh install.sh --install
/jffs/scripts/wan-watchdog status
/jffs/wan-watchdog/service/wan-healthctl
/jffs/wan-watchdog/service/wan-healthctl --count 0 --timeout 0
```

For an installed monitor, stage the new bundle in a separate directory and run
`sh install.sh --upgrade` from that directory. The installer snapshots the
previous executable, adapter/reference, entry point and configuration before
stopping the identified owner. It waits for the shared lock too, including
short-lived shell probe children. It never overwrites a running executable.

The installer has one 90-second deadline. An independent guard attempts rollback
at 120 seconds if qualification has not committed. Success and automatic
rollback atomically claim the same transaction, so both cannot win. Qualification
requires fresh eligible health from both WANs with no routing error. The routing
ledger and failover markers survive the handoff. Installation edits neither
NVRAM nor existing hooks and does not install or enable affinity.

The monitor is single-instance and takes the same ownership lock as the
shell. Raw ICMP requires root; it opens no network listening port. The Unix
socket is local and root-only by default.

No arguments means foreground execution, preserving the existing background
launch in `services-start`. Controls: `start`, `stop`, `status`,
`cleanup`/`--cleanup`. Normal stop preserves forwarding for restart; explicit
cleanup refuses while a routing owner holds the lock. Keep one routing owner.

```sh
# Restore the previous service without discarding routing state:
sh /jffs/wan-watchdog/service/rollback.sh --rollback
```

A first-install rollback restores the previous shell. An upgrade rollback restores
the previous monitor executable and adapter, retaining the hardened lifecycle
tools. Both restore the saved configuration. Rollback rejects unrecognized live
owners, waits for inherited locks, removes demonstrably stale PID records, and
verifies the replacement's process, lock and monitoring output before reporting
success. For a restored shell, that output is its status file with the running
mode and both WAN lines. The beta.5 bundle misreports a successful shell
restore as a failure; see the [known issue](../RELEASES.md#known-issues-in-beta5).
A failed restart is reported as a failure and retains its snapshot for
retry. The recovery mutex uses `flock`, so an interrupted shell does not leave a
permanent directory lock.

Snapshots are retained under `service/rollback/`; `latest` identifies the current
rollback transaction. Keep the snapshot needed for recovery. Removing the
watchdog itself follows the original README's hook-removal/owned-cleanup
procedure. Never delete the shared ownership ledger as an uninstall shortcut.
Reboot/firewall-rebuild and physical WAN-outage qualification require a suitable
maintenance environment; live checks do not manufacture those events.

## Optional source build

From the repository root, change into `wan-watchdog` before running the
commands below. Host tests need C11 and Python 3. Use a static aarch64 Linux
musl toolchain for the router; the release uses Zig 0.15.2:

```sh
make host-tests
python3 -m unittest discover -s tests -p 'test_*.py'
make router bundle ROUTER_CC='zig cc -target aarch64-linux-musl'
```

The archive is `build/wan-watchdog-aarch64.tar.gz` relative to the
component directory. Transfer it and follow the same qualification and
installation steps above. `make router` also builds the separate Linux-only
`test_socket` and `test_probe` diagnostics described earlier; these are not
included in the install bundle.
