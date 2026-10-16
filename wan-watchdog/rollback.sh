#!/bin/sh
# Restore a verified previous owner and policy; preserve the routing ledger.
set -eu
BASE=${WW_BASE:-/jffs/wan-watchdog/service}
. "$BASE/lib/ww-service.sh"
case "${1:-}" in
--rollback) read token <"$BASE/rollback/latest";;
--guard) token=${2:-};;
*) ww_die 'usage: rollback.sh --rollback';;
esac
case "$token" in ''|*[!0-9-]*) ww_die 'invalid rollback transaction';; esac
transaction="$BASE/rollback/$token"
[ -r "$transaction/entry" ] && [ -r "$transaction/kind" ] || ww_die 'rollback snapshot missing'
mkdir -p "$RUN"
exec 8>"$RUN/rollback.lock"
flock -n 8 || ww_die 'another rollback is running'
if [ "${1:-}" = --guard ]; then
    mv "$transaction/armed" "$transaction/guard-claimed" 2>/dev/null || exit 0
else
    [ ! -f "$transaction/armed" ] || mv "$transaction/armed" "$transaction/manual-claimed"
fi
exec 9>"$RUN/handoff.lock"
flock -x 9
ww_stop_owner || ww_die 'previous owner or inherited lock did not stop'
read previous_kind <"$transaction/kind"
case "$previous_kind" in
monitor)
    for file in wan-watchdog wan-healthctl wan-watchdog-legacy.sh lib/ww-adapter.sh; do
        cp "$transaction/$file" "$BASE/$file.new";chmod 700 "$BASE/$file.new";mv "$BASE/$file.new" "$BASE/$file"
    done
    # Keep the hardened wrapper and recovery tools with the previous executable.
    cp "$BASE/wan-watchdog.sh" "$ENTRY.new"
    ;;
shell) cp "$transaction/entry" "$ENTRY.new";;
*) ww_die 'invalid previous owner kind';;
esac
chmod 700 "$ENTRY.new";mv "$ENTRY.new" "$ENTRY"
cp "$transaction/service.conf" "$CONFIG.new";chmod 600 "$CONFIG.new";mv "$CONFIG.new" "$CONFIG"
ww_policy_load
rm -f "$RUN/status" "$RUN/status.new"
if [ "$previous_kind" = monitor ]; then
    sh "$ENTRY" start 8>&- || ww_die 'previous monitor did not restart'
else
    nohup "$ENTRY" 8>&- 9>&- </dev/null >>"$RUN/rollback-owner.log" 2>&1 &
fi
restore_end=$(($(ww_now)+45))
while [ "$(ww_now)" -lt "$restore_end" ]; do
    if [ "$previous_kind" = shell ]; then
        if ww_shell_ready; then touch "$transaction/restored";echo 'Previous shell watchdog restored and monitoring.';exit 0;fi
    elif ww_owner && [ "$kind" = monitor ] && ! flock -n "$RUN/lock" true &&
         "$BASE/wan-healthctl" --timeout 2 >"$RUN/rollback-health.json" 2>/dev/null &&
         grep -q '"fresh":true' "$RUN/rollback-health.json" &&
         grep -q '"routing_error":0' "$RUN/rollback-health.json" &&
         [ "$(grep -o '"eligible":1' "$RUN/rollback-health.json" | wc -l)" -eq 2 ]; then
        touch "$transaction/restored";echo 'Previous monitor watchdog restored and qualified.';exit 0
    fi
    sleep 1
done
ww_die 'replacement monitoring not confirmed; recovery snapshot retained'
