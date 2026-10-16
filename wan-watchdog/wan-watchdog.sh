#!/bin/sh
# Compatible foreground entry point; lifecycle operations are explicit.
set -u
BASE=${WW_BASE:-/jffs/wan-watchdog/service}
RUN=${STATE_DIR:-/tmp/wan-watchdog}
OWNER=${OWNERSHIP_DIR:-/jffs/wan-watchdog}
BIN="$BASE/wan-watchdog"
ADAPTER="$BASE/lib/ww-adapter.sh"
LEGACY="$BASE/wan-watchdog-legacy.sh"
. "$BASE/lib/ww-service.sh"
umask 077
ww_policy_load
PID="$RUN/wan-watchdog.pid"
case "$BASE:$RUN:$OWNER" in *'..'*|*' '*|*'
'*) echo 'unsafe component path' >&2;exit 2;; esac
alive() {
    ww_owner && [ "$kind" = monitor ]
}

case "${1:-run}" in
run)
    [ -x "$BIN" ] && [ -r "$ADAPTER" ] && [ -r "$LEGACY" ] || exit 2
    exec "$BIN" --active --run-dir "$RUN" --ownership "$OWNER" --adapter "$ADAPTER" --legacy "$LEGACY" --mode "${FAILOVER_MODE:-whole_lan}"
    ;;
start)
    alive && exit 0
    ww_runtime_private
    [ ! -L "$RUN/monitor.log" ] || exit 1
    : >>"$RUN/monitor.log";chmod 600 "$RUN/monitor.log" || exit 1
    nohup sh "$0" run 7>&- 8>&- 9>&- </dev/null >>"$RUN/monitor.log" 2>&1 &
    tries=0
    while [ "$tries" -lt 10 ]; do
        if alive && grep -q 'publisher_ready=1' "$RUN/status" 2>/dev/null; then exit 0;fi
        sleep 1;tries=$((tries+1))
    done
    echo 'monitor startup not confirmed; inspect monitor.log' >&2;exit 1
    ;;
stop)
    alive || { echo 'no monitor owner identified; preserving other processes' >&2;exit 1; }
    kill -TERM "$p" || exit 1
    tries=0
    while alive && [ "$tries" -lt 10 ]; do sleep 1;tries=$((tries+1));done
    ! alive
    ;;
status)
    cat "$RUN/status";alive
    ;;
cleanup|--cleanup)
    ww_runtime_private
    exec 9>"$RUN/lock"
    flock -n 9 || { echo 'watchdog still owns routing; stop it first' >&2;exit 1; }
    STATE_DIR="$RUN" OWNERSHIP_DIR="$OWNER" WW_LEGACY="$LEGACY" WW_SHADOW=0 sh "$ADAPTER" cleanup
    ;;
*) echo 'usage: wan-watchdog.sh [run|start|stop|status|cleanup]' >&2;exit 2;;
esac
