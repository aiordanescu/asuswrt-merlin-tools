#!/bin/sh
# Preserve the shared routing ledger and active markers across the handoff.
set -eu
BASE=/jffs/wan-watchdog/service
RUN=/tmp/wan-watchdog
ENTRY=/jffs/scripts/wan-watchdog
[ "${1:-}" = --rollback ] || { echo 'usage: rollback.sh --rollback' >&2;exit 2; }
[ -r "$BASE/rollback/wan-watchdog" ] || exit 1
mkdir "$RUN/rollback-running" 2>/dev/null || exit 1
trap 'rmdir "$RUN/rollback-running"' EXIT
if [ -r "$RUN/wan-watchdog.pid" ];then
    read p <"$RUN/wan-watchdog.pid"
    case "$p" in ''|*[!0-9]*) exit 1;; esac
    if [ -r "/proc/$p/cmdline" ];then
        first=$(tr '\000' '\n' <"/proc/$p/cmdline" | head -n1)
        if [ "$first" = "$BASE/wan-watchdog" ];then
            kill -TERM "$p"
            tries=0
            while kill -0 "$p" 2>/dev/null && [ "$tries" -lt 10 ];do sleep 1;tries=$((tries+1));done
            kill -0 "$p" 2>/dev/null && exit 1
        else
            cmd=$(tr '\000' ' ' <"/proc/$p/cmdline")
            case "$cmd" in *"$ENTRY"*)
                cmp -s "$ENTRY" "$BASE/rollback/wan-watchdog" && { rm -f "$BASE/rollback/armed";exit 0; };;
            esac
            echo 'unrecognized owner; preserving it' >&2;exit 1
        fi
    fi
fi
cp "$BASE/rollback/wan-watchdog" "$ENTRY.new"
chmod 700 "$ENTRY.new";mv "$ENTRY.new" "$ENTRY"
nohup "$ENTRY" </dev/null >>/tmp/wan-watchdog.log 2>&1 &
sleep 2
[ -r "$RUN/wan-watchdog.pid" ] || exit 1
rm -f "$BASE/rollback/armed"
echo 'Legacy watchdog restored; monitor bundle retained for inspection.'
