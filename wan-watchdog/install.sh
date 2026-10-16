#!/bin/sh
# Guarded first install and upgrade. Preserve hooks and routing state.
set -eu
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SOURCE/lib/ww-service.sh"
case "${1:-}" in --install|--upgrade) action=$1;; *) ww_die 'usage: install.sh --install|--upgrade';; esac
awk '$1=="Uid:" && $3==0 {ok=1} END {exit !ok}' "$PROC/self/status" || ww_die 'root required'
umask 077
ww_runtime_private
mkdir -p "$OWNER"
exec 7>"$RUN/install.lock"
flock -n 7 || ww_die 'another installer is running'
ww_no_foreign_owner
ww_owner || ww_die 'running watchdog owner required'
oldpid=$p;oldkind=$kind
if [ "$action" = --install ]; then
    [ "$oldkind" = shell ] || ww_die 'use --upgrade for an installed monitor'
    # A completed rollback deliberately retains its recovery tree. Reuse it
    # for another first-install attempt, but never race an armed guard.
    if [ -e "$BASE" ]; then
        [ -r "$BASE/rollback/latest" ] || ww_die 'unrecognized existing service directory'
        read previous <"$BASE/rollback/latest"
        case "$previous" in ''|*[!0-9-]*) ww_die 'invalid previous transaction';; esac
        [ -f "$BASE/rollback/$previous/restored" ] &&
          [ ! -f "$BASE/rollback/$previous/armed" ] || ww_die 'previous recovery has not completed'
    fi
else
    [ -d "$BASE" ] && [ "$oldkind" = monitor ] || ww_die 'monitor owner required for upgrade'
fi
for file in wan-watchdog wan-healthctl wan-watchdog.sh wan-watchdog-legacy.sh lib/ww-adapter.sh lib/ww-service.sh rollback.sh; do
    [ -f "$SOURCE/$file" ] || ww_die "missing bundle file: $file"
done
if [ "$oldkind" = shell ]; then
    installed_hash=$(md5sum "$ENTRY" | awk '{print $1}')
    reference_hash=$(md5sum "$SOURCE/wan-watchdog-legacy.sh" | awk '{print $1}')
    case "$installed_hash" in "$reference_hash"|257b8053539066f0df12a751113eefd7) ;; *) ww_die 'installed shell differs from the reviewed reference';; esac
    grep -q 'whole_failed=none' "$RUN/status" && grep -q '^WAN0 usable ' "$RUN/status" &&
        grep -q '^WAN1 usable ' "$RUN/status" || ww_die 'wait for both WANs healthy with no failover'
else
    "$BASE/wan-healthctl" --timeout 2 >"$RUN/install-before.json" || ww_die 'health publisher unavailable'
    grep -q '"fresh":true' "$RUN/install-before.json" && grep -q '"routing_error":0' "$RUN/install-before.json" &&
      [ "$(grep -o '"eligible":1' "$RUN/install-before.json" | wc -l)" -eq 2 ] &&
      grep -q 'desired=-1 applied=-1' "$RUN/status" || ww_die 'wait for both WANs eligible with no override'
fi
# Serialize before creating a service tree: a failed capture leaves no partial
# installation and no changes to the running owner.
capture="$RUN/install-policy.$$"
trap 'rm -f "$capture"' EXIT
ww_capture_policy "$oldpid" "$capture" "$oldkind"
token="$(ww_now)-$$"
transaction="$BASE/rollback/$token"
mkdir -p "$BASE/lib" "$transaction/lib"
printf '%s\n' "$oldkind" >"$transaction/kind"
cp -p "$ENTRY" "$transaction/entry"
mv "$capture" "$transaction/service.conf"
if [ "$oldkind" = monitor ]; then
    for file in wan-watchdog wan-healthctl wan-watchdog-legacy.sh lib/ww-adapter.sh; do cp -p "$BASE/$file" "$transaction/$file"; done
fi
# Recovery code is ready before the independent guard is armed.
cp "$SOURCE/lib/ww-service.sh" "$BASE/lib/ww-service.sh"
cp "$SOURCE/rollback.sh" "$BASE/rollback.sh"
printf '%s\n' "$token" >"$BASE/rollback/latest.new";mv "$BASE/rollback/latest.new" "$BASE/rollback/latest"
touch "$transaction/armed"
cat >"$transaction/guard" <<GUARD
#!/bin/sh
sleep 120
WW_BASE='$BASE' STATE_DIR='$RUN' OWNERSHIP_DIR='$OWNER' WW_ENTRY='$ENTRY' WW_PROC='$PROC' WW_CONFIG='$CONFIG' sh '$BASE/rollback.sh' --guard '$token' >>'$RUN/rollback.log' 2>&1
GUARD
nohup sh "$transaction/guard" 7>&- </dev/null >/dev/null 2>&1 &
echo $! >"$transaction/guard.pid"
# One total budget, with a thirty-second margin before automatic rollback.
install_end=$(($(ww_now)+90))
check_install() {
    [ -f "$transaction/armed" ] && [ "$(ww_now)" -lt "$install_end" ] || ww_die 'installation deadline expired; recovery guard owns handoff'
}
exec 9>"$RUN/handoff.lock"
flock -x 9
check_install
ww_stop_owner || ww_die 'owner/lock did not stop; recovery guard remains armed'
check_install
for file in wan-watchdog wan-healthctl wan-watchdog.sh wan-watchdog-legacy.sh lib/ww-adapter.sh; do
    check_install
    cp "$SOURCE/$file" "$BASE/$file.new";chmod 700 "$BASE/$file.new";mv "$BASE/$file.new" "$BASE/$file"
done
cp "$transaction/service.conf" "$CONFIG.new";chmod 600 "$CONFIG.new";mv "$CONFIG.new" "$CONFIG"
cp "$BASE/wan-watchdog.sh" "$ENTRY.new";chmod 700 "$ENTRY.new";mv "$ENTRY.new" "$ENTRY"
check_install
ww_policy_load
sh "$ENTRY" start 7>&- || ww_die 'startup failed; recovery guard remains armed'
exec 9>&-
while [ "$(ww_now)" -lt "$install_end" ] && [ -f "$transaction/armed" ]; do
    if "$BASE/wan-healthctl" --timeout 2 >"$RUN/install-health.json" 2>/dev/null &&
       grep -q '"fresh":true' "$RUN/install-health.json" &&
       grep -q '"routing_error":0' "$RUN/install-health.json" &&
       [ "$(grep -o '"eligible":1' "$RUN/install-health.json" | wc -l)" -eq 2 ]; then
        check_install
        ww_owner && [ "$kind" = monitor ] && ! flock -n "$RUN/lock" true || ww_die 'monitor ownership changed during qualification; guard remains armed'
        mv "$transaction/armed" "$transaction/committed" 2>/dev/null || ww_die 'recovery guard already claimed handoff'
        echo "Watchdog installed; both WANs eligible. Rollback: sh $BASE/rollback.sh --rollback"
        exit 0
    fi
    sleep 1
done
ww_die 'health qualification failed; recovery guard remains armed'
