# Building WAN affinity

Run from this directory in a complete repository checkout; the health client
uses `../common/wan-health`. Python 3.8+ and a C11 compiler run the host tests:

```sh
python3 -m unittest discover -s tests
make host-tests
```

The router helper needs Linux headers and an aarch64 Linux toolchain. The tested
build uses Zig 0.15.2 on macOS arm64, static musl, and warnings as errors:

```sh
make router ROUTER_CC='zig cc -target aarch64-linux-musl'
make bundle ROUTER_CC='zig cc -target aarch64-linux-musl'
```

Use the Makefile rather than an abbreviated direct compile command: it includes
the connection parser, worker and shared health protocol/client. The bundle is
`build/wan-affinity-aarch64.tar.gz`, containing the normal binary, controller,
example config, hook editor, installer, rollback script, README, current
build/qualification/health guides, project license and static musl notices.
No test binary, fixture publisher, credentials or router-specific config is
included. Build outputs are ignored by Git.

`make router-test router-health-tests` builds separate opt-in diagnostics:
`wan-affinity-test` accepts bounded loopback/port-domain fault controls that the
production executable rejects; `test_health_socket` validates the consumer;
`health-fixture` can publish synthetic health only at a private test socket.
Never install the test executable as the persistent service.

## Runtime layout

```
/jffs/wan-affinity/wan-affinity          static helper
/jffs/wan-affinity/wan-affinity.sh       serialized controller
/jffs/wan-affinity/wan-affinity.conf     root-owned settings
/jffs/wan-affinity/{hooks,rollback}.sh   hook maintenance and previous-version restore
/jffs/wan-affinity/previous/            one upgrade backup
/tmp/wan-affinity/                     PID, status, checkpoints, ownership, bounded logs
/tmp/wan-affinity.lock                 controller lock; stable inode outside runtime dir
```

The controller requires `flock`, `iptables`, `conntrack`, native dual-WAN load
balancing, and NFQUEUE support. It refuses foreign chain/queue ownership and
requires a valid initial connection view before activation. On the tested
firmware, NFQUEUE does not supply connection IDs, so validated complete original
TCP/UDP tuples have local identities. Only the default conntrack zone is supported.
The shell lacks `id` and fractional `sleep`; root checking has a `/proc` fallback.

`WA_BASE`, `WA_RUN`, `WA_CHAIN`, `WA_PROC` and `WA_SCRIPTS` are private test
instance overrides. Production uses the defaults. See the [README](README.md)
for installation and [qualification report](docs/QUALIFICATION-2026-10-15.md)
for the scope of live validation. Actual router reboot, firmware upgrade and
maximum sustained throughput remain untested on the sole live router.
