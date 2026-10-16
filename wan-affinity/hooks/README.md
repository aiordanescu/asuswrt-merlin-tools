# Startup and firewall hooks

`install.sh` uses `../hooks.sh` to place one marked asynchronous `reconcile`
block near the top of each of `/jffs/scripts/services-start` and
`/jffs/scripts/firewall-start`. Existing content and permissions are retained;
repeated installation is idempotent. Uninstall removes only those marked blocks.
An unmatched/edited marker pair is refused for manual review.

The block checks that `/jffs/wan-affinity/wan-affinity.sh` is executable, then
calls it in the background. It does not replace watchdog startup, DDNS cron
registration, T-Mobile TTL rules, or other add-ons. It does not request a global
firewall restart. Hooks themselves are silent; helper/supervisor logs are in RAM.

Reconciliation is serialized by `flock`. A healthy unchanged helper retains its
PID and associations while its missing owned chain/jump is rebuilt. The optional
supervisor also retries native readiness when boot hooks run too early.
A manual `stop` survives firewall hooks until explicit `start` or the next boot;
`disable` persists across boot. Actual firmware boot/upgrade remains a separate
qualification limit; live tests emulate readiness delay and lose only owned rules.
