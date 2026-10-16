#!/bin/sh
# Shared lifecycle operations. No routing policy mutations belong here.
BASE=${WW_BASE:-/jffs/wan-watchdog/service}
RUN=${STATE_DIR:-/tmp/wan-watchdog}
OWNER=${OWNERSHIP_DIR:-/jffs/wan-watchdog}
ENTRY=${WW_ENTRY:-/jffs/scripts/wan-watchdog}
PROC=${WW_PROC:-/proc}
CONFIG=${WW_CONFIG:-$OWNER/service.conf}
PID="$RUN/wan-watchdog.pid"
ww_die() { echo "wan-watchdog: $*" >&2; exit 1; }
case "$BASE:$RUN:$OWNER:$ENTRY:$PROC:$CONFIG" in *[!A-Za-z0-9_./:-]*|*'..'*) ww_die 'unsafe lifecycle path';; esac
ww_now() { awk '{print int($1)}' "$PROC/uptime"; }
ww_policy_load() {
    [ ! -L "$CONFIG" ] || ww_die 'symlink service configuration'
    if [ -f "$CONFIG" ]; then . "$CONFIG"; fi
    : "${FAILOVER_MODE:=whole_lan}" "${FAILOVER_PRIORITY:=88}" "${FAILOVER_TABLE:=20188}"
    : "${PROBE_TABLE0:=20186}" "${PROBE_TABLE1:=20187}" "${LAN_NETWORKS:=auto}" "${DNS_SERVERS:=auto}"
    export FAILOVER_MODE FAILOVER_PRIORITY FAILOVER_TABLE PROBE_TABLE0 PROBE_TABLE1 LAN_NETWORKS DNS_SERVERS
    export PROBE_FALLBACKS DDNS_HELPER DDNS_POSTCONF DDNS_CACHE_DIR DDNS_CUSTOM_CONFIG_DIR
    export DDNS_RETRY_SECONDS DDNS_RECOVERY_SECONDS DDNS_LOOKUP_SECONDS
}
ww_owner() {
    [ -r "$PID" ] || return 1
    read p <"$PID"
    case "$p" in ''|*[!0-9]*) return 1;; esac
    [ -r "$PROC/$p/cmdline" ] || return 1
    first=$(tr '\000' '\n' <"$PROC/$p/cmdline" | head -n1)
    if [ "$first" = "$BASE/wan-watchdog" ]; then kind=monitor; return 0; fi
    if tr '\000' '\n' <"$PROC/$p/cmdline" | awk -v entry="$ENTRY" '$0==entry {f=1} END {exit !f}'; then
        kind=shell; return 0
    fi
    return 1
}
ww_no_foreign_owner() {
    if [ -r "$PID" ]; then
        read p <"$PID"
        case "$p" in ''|*[!0-9]*) ww_die 'invalid PID record; preserving it';; esac
        if [ -r "$PROC/$p/cmdline" ] && [ -n "$(tr '\000' ' ' <"$PROC/$p/cmdline")" ]; then
            ww_owner || ww_die 'unrecognized live owner; preserving it'
        fi
    fi
}
ww_stop_owner() {
    ww_no_foreign_owner
    if ww_owner; then
        oldpid=$p
        kill -TERM "$oldpid" || return 1
        stop_end=$(($(ww_now)+15))
        while ww_owner && [ "$p" = "$oldpid" ]; do
            [ "$(ww_now)" -lt "$stop_end" ] || return 1
            sleep 1
        done
    fi
    # Probe children can outlive their parent and inherit its lock.
    lock_end=$(($(ww_now)+10))
    until flock -n "$RUN/lock" true; do
        [ "$(ww_now)" -lt "$lock_end" ] || return 1
        sleep 1
    done
    rm -f "$PID"
}
ww_capture_policy() {
    # Copy only allowlisted settings, never a raw environment or credentials.
    # Only a monitor runs from service.conf. A shell runs from its own
    # environment, so a file left by an earlier rollback must not replace it.
    capture_pid=$1;capture_path=$2;capture_kind=$3
    if [ "$capture_kind" = monitor ] && [ -f "$CONFIG" ]; then cp -p "$CONFIG" "$capture_path"; return; fi
    {
        printf '%s\n' 'FAILOVER_MODE=whole_lan' 'FAILOVER_PRIORITY=88' 'FAILOVER_TABLE=20188' \
          'PROBE_TABLE0=20186' 'PROBE_TABLE1=20187' 'LAN_NETWORKS=auto' 'DNS_SERVERS=auto'
        tr '\000' '\n' <"$PROC/$capture_pid/environ" | awk '
          BEGIN {split("FAILOVER_MODE FAILOVER_PRIORITY FAILOVER_TABLE PROBE_TABLE0 PROBE_TABLE1 PROBE_FALLBACKS LAN_NETWORKS DNS_SERVERS DDNS_HELPER DDNS_POSTCONF DDNS_CACHE_DIR DDNS_CUSTOM_CONFIG_DIR DDNS_RETRY_SECONDS DDNS_RECOVERY_SECONDS DDNS_LOOKUP_SECONDS",keys," ");for(i in keys)allow[keys[i]]=1}
          {pos=index($0,"=");if(!pos)next;key=substr($0,1,pos-1);if(!allow[key])next
           value=substr($0,pos+1);gsub(/\047/,"\047\\\047\047",value);printf "%s=\047%s\047\nexport %s\n",key,value,key}'
        printf '%s\n' 'export FAILOVER_MODE FAILOVER_PRIORITY FAILOVER_TABLE PROBE_TABLE0 PROBE_TABLE1 LAN_NETWORKS DNS_SERVERS'
    } >"$capture_path"
    chmod 600 "$capture_path"
    if [ -r "$OWNER/mode" ]; then
        read capture_mode <"$OWNER/mode"
        case "$capture_mode" in whole_lan|pinned) printf 'FAILOVER_MODE=%s\nexport FAILOVER_MODE\n' "$capture_mode" >>"$capture_path";;
          *) ww_die 'invalid running mode';; esac
    fi
    sh -n "$capture_path" || ww_die 'cannot serialize service configuration'
}
ww_shell_status_ok() {
    # The shell's write_status format; the monitor's status begins monitor=1.
    grep -Eq '^uptime_seconds=[0-9]+ mode=(whole_lan|pinned) ' "$1" &&
      grep -q '^WAN0 ' "$1" && grep -q '^WAN1 ' "$1"
}
ww_shell_ready() {
    ww_owner && [ "$kind" = shell ] || return 1
    ! flock -n "$RUN/lock" true || return 1
    [ -s "$RUN/status" ] || return 1
    ww_shell_status_ok "$RUN/status"
}
