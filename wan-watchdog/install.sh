#!/bin/sh
# Run from an extracted monitor bundle on the router. Existing hooks stay valid.
set -eu
BASE=/jffs/wan-watchdog/service
ENTRY=/jffs/scripts/wan-watchdog
RUN=/tmp/wan-watchdog
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
die() { echo "$*" >&2;exit 1; }
case "${1:-}" in
--install)
    awk '$1=="Uid:" && $3==0 {ok=1} END {exit !ok}' /proc/self/status || die 'root required'
    [ ! -e "$BASE" ] || die 'monitor directory exists; use its rollback or an explicit upgrade'
    for file in wan-watchdog wan-healthctl wan-watchdog.sh wan-watchdog-legacy.sh lib/ww-adapter.sh rollback.sh;do
        [ -f "$SOURCE/$file" ] || die "missing bundle file: $file"
    done
    cmp -s "$ENTRY" "$SOURCE/wan-watchdog-legacy.sh" || die 'installed shell differs from the frozen reference; review configuration first'
    [ -r "$RUN/wan-watchdog.pid" ] || die 'running legacy owner required'
    grep -q 'whole_failed=none' "$RUN/status" &&
        grep -q '^WAN0 usable ' "$RUN/status" &&
        grep -q '^WAN1 usable ' "$RUN/status" || die 'wait for healthy legacy monitoring with no active failover'
    awk '$1!=0 || $2!=1 {bad=1} END {exit(bad || NR!=2)}' "$RUN/health_wan0" "$RUN/health_wan1" || die 'both WANs must be acceptable destinations'
    read oldpid <"$RUN/wan-watchdog.pid"
    case "$oldpid" in ''|*[!0-9]*) die 'invalid legacy PID';; esac
    oldcmd=$(tr '\000' ' ' <"/proc/$oldpid/cmdline")
    case "$oldcmd" in *"$ENTRY"*) ;; *) die 'legacy PID identity mismatch';; esac
    mkdir -m 700 "$BASE"
    mkdir -m 700 "$BASE/lib" "$BASE/rollback"
    cp "$ENTRY" "$BASE/rollback/wan-watchdog"
    for file in wan-watchdog wan-healthctl wan-watchdog.sh wan-watchdog-legacy.sh lib/ww-adapter.sh rollback.sh;do
        cp "$SOURCE/$file" "$BASE/$file"
    done
    chmod 700 "$BASE"/*.sh "$BASE/wan-watchdog" "$BASE/wan-healthctl" "$BASE/lib/ww-adapter.sh"
    # The independent guard survives this installer and an SSH disconnect.
    cat >"$BASE/rollback/guard" <<'GUARD'
#!/bin/sh
sleep 120
[ -f /jffs/wan-watchdog/service/rollback/armed ] || exit 0
sh /jffs/wan-watchdog/service/rollback.sh --rollback >>/tmp/wan-watchdog-rollback.log 2>&1
GUARD
    touch "$BASE/rollback/armed"
    nohup sh "$BASE/rollback/guard" </dev/null >/dev/null 2>&1 &
    echo $! >"$BASE/rollback/guard.pid"
    kill -TERM "$oldpid"
    tries=0
    while kill -0 "$oldpid" 2>/dev/null && [ "$tries" -lt 10 ];do sleep 1;tries=$((tries+1));done
    kill -0 "$oldpid" 2>/dev/null && die 'legacy owner did not stop; guard will restore service'
    cp "$BASE/wan-watchdog.sh" "$ENTRY.new"
    chmod 700 "$ENTRY.new";mv "$ENTRY.new" "$ENTRY"
    sh "$ENTRY" start || die 'startup failed; guard will restore the shell'
    tries=0
    while [ "$tries" -lt 70 ];do
        if "$BASE/wan-healthctl" --timeout 2 >"$RUN/install-health.json" 2>/dev/null &&
           grep -q '"routing_error":0' "$RUN/install-health.json" &&
           [ "$(grep -o '"eligible":1' "$RUN/install-health.json" | wc -l)" -eq 2 ];then
            rm -f "$BASE/rollback/armed"
            echo 'Monitor watchdog installed; both WANs eligible. Rollback: sh /jffs/wan-watchdog/service/rollback.sh --rollback'
            exit 0
        fi
        sleep 1;tries=$((tries+1))
    done
    die 'health qualification failed; independent guard remains armed'
    ;;
*) die 'usage: install.sh --install';;
esac
