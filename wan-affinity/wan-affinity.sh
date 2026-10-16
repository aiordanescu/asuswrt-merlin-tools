#!/bin/sh
# Serialized lifecycle controller. Hook reconciliation preserves the engine.
set -u
BASE=${WA_BASE:-/jffs/wan-affinity}
BIN="$BASE/wan-affinity"
CFG="$BASE/wan-affinity.conf"
RUN=${WA_RUN:-/tmp/wan-affinity}
CHAIN=${WA_CHAIN:-WA_ADMIT}
PIDF="$RUN/wan-affinity.pid"
LOG="$RUN/wan-affinity.log"
OWNER="$RUN/owned-chain"
JUMP="$RUN/owned-jump"
WA_PROC=${WA_PROC:-/proc}
SELF=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/$(basename -- "$0")
SCRIPTS=${WA_SCRIPTS:-/jffs/scripts}
MONPID="$RUN/supervisor.pid"
STARTING=0
NEW_HELPER=0
LOCKED=0
fail() { echo "wan-affinity: $*" >&2; exit 1; }
say() { echo "wan-affinity: $*"; }
ipt() { iptables -t mangle "$@"; }
fingerprint() {
    # Merlin supplies md5sum; macOS host tests supply cksum. This detects
    # configuration changes, not signatures of untrusted content.
    if type md5sum >/dev/null 2>&1; then md5sum "$1"
    elif type cksum >/dev/null 2>&1; then cksum "$1"
    else return 1
    fi
}
need_root() {
    if type id >/dev/null 2>&1; then _uid=$(id -u)
    else _uid=$(awk '$1=="Uid:" {print $2}' /proc/self/status 2>/dev/null)
    fi
    [ "$_uid" = 0 ] || fail 'must run as root'
}
validate_paths() {
    case "$BASE:$RUN" in *'..'*|*' '*|*'
'*) fail 'unsafe component path' ;; esac
    for p in "$BASE" "$RUN"; do
        case "$p" in /*/*) ;; *) fail 'component paths must be absolute private subdirectories' ;; esac
        case "$p" in /jffs/scripts|/tmp/home|/usr/bin|/usr/sbin|/var/log) fail 'shared directory is not a component path' ;; esac
    done
    case "$CHAIN" in ''|*[!A-Za-z0-9_]*) fail 'invalid owned chain name' ;; esac
    [ "${#CHAIN}" -le 28 ] || fail 'chain name too long'
}
is_ipv4() {
    echo "$1" | awk -F. 'NF!=4 {exit 1} {for(i=1;i<=4;i++) if($i!~/^[0-9]+$/ || $i>255) exit 1}'
}
load_config() {
    [ -f "$CFG" ] || fail "config $CFG not found"
    if [ -x "$BIN" ];then "$BIN" --config "$CFG" --check-config >/dev/null || fail 'helper rejected configuration';fi
    . "$CFG"
    : "${MODE:=sticky}" "${QUEUE_NUM:=20880}" "${CLIENT:=}" "${LAN_SCOPE:=0}" "${ENABLED:=0}"
    : "${SUPERVISE:=0}"
}
config_disabled() {
    # Read this one switch without executing a possibly broken config. The
    # final literal assignment wins, as in the documented shell config.
    [ -f "$CFG" ] || return 1
    awk '/^[ \t]*(export[ \t]+)?ENABLED[ \t]*=/ {
      sub(/^[ \t]*(export[ \t]+)?ENABLED[ \t]*=[ \t]*/, "")
      sub(/[ \t]+#.*/, ""); sub(/[ \t]+$/, "")
      disabled=($0=="0" || $0=="\"0\"" || $0=="\0470\047")
    } END {exit !disabled}' "$CFG"
}
validate_config() {
    [ "${STATUS_DIR:-$RUN}" = "$RUN" ] || fail "STATUS_DIR is helper-only; controller runtime is $RUN"
    case "$MODE" in sticky|affinity) ;; *) fail 'invalid MODE' ;; esac
    case "$QUEUE_NUM" in ''|*[!0-9]*) fail 'invalid queue number' ;; esac
    [ "$QUEUE_NUM" -le 65535 ] || fail 'queue number out of range'
    if [ "$LAN_SCOPE" != 1 ]; then
        is_ipv4 "$CLIENT" || fail 'CLIENT must be exactly one IPv4 address'
        _ssh=${SSH_CONNECTION:-}
        [ "$CLIENT" != "${_ssh%% *}" ] || fail 'refusing the SSH management client as test scope'
    else
        [ "${ALLOW_LAN_SCOPE:-0}" = 1 ] || fail 'LAN_SCOPE requires ALLOW_LAN_SCOPE=1'
    fi
}
control_lock() {
    [ "$LOCKED" = 0 ] || return 0
    need_root
    umask 077
    [ ! -L "$RUN" ] && [ ! -L "$RUN.lock" ] || fail 'symlink at runtime path'
    mkdir -p "$RUN" || fail 'cannot create runtime directory'
    chmod 700 "$RUN"
    # Keep the lock inode outside RUN: uninstall must not unlink a held lock.
    exec 9>"$RUN.lock"
    _tries=0
    until flock -n 9 2>/dev/null; do
        _tries=$((_tries+1)); [ "$_tries" -lt 20 ] || fail 'controller busy'
        sleep 1
    done
    LOCKED=1
}
cfg_set() {
    [ -f "$CFG" ] || { printf '%s=%s\n' "$1" "$2" >"$CFG"; return $?; }
    awk -v k="$1" -v v="$2" 'BEGIN{done=0} $0~"^"k"=" {print k"="v;done=1;next} {print} END{if(!done)print k"="v}' "$CFG" >"$CFG.new" && mv "$CFG.new" "$CFG"
}
chain_exists() { ipt -n -L "$CHAIN" >/dev/null 2>&1; }
owned() { [ -r "$OWNER" ] && [ "$(cat "$OWNER")" = "$CHAIN" ]; }
balance_line() { ipt -S PREROUTING | awk '$1=="-A" {n++;for(i=1;i<NF;i++)if($i=="-j" && $(i+1)=="balance"){print n;exit}}'; }
owned_jumps() { ipt -S PREROUTING 2>/dev/null | awk -v c="$CHAIN" '{for(i=1;i<NF;i++)if($i=="-j" && $(i+1)==c)n++}END{print n+0}'; }
helper_alive() {
    [ -r "$PIDF" ] || return 1
    _p=$(cat "$PIDF")
    case "$_p" in ''|*[!0-9]*) return 1 ;; esac
    kill -0 "$_p" 2>/dev/null || return 1
    [ -r "$WA_PROC/$_p/cmdline" ] || return 1
    [ "$(tr '\000' '\n' < "$WA_PROC/$_p/cmdline" | head -n1)" = "$BIN" ]
}
queue_bound() {
    [ -r "$WA_PROC/net/netfilter/nfnetlink_queue" ] || return 1
    awk -v q="$QUEUE_NUM" '$1==q {found=1} END{exit !found}' "$WA_PROC/net/netfilter/nfnetlink_queue"
}
prereq() {
    [ -x "$BIN" ] || fail "helper $BIN missing"
    type conntrack >/dev/null 2>&1 || fail 'conntrack missing'
    type iptables >/dev/null 2>&1 || fail 'iptables missing'
    [ -r "$WA_PROC/net/netfilter/nfnetlink_queue" ] || fail 'queue interface unavailable'
    grep -qw NFQUEUE "$WA_PROC/net/ip_tables_targets" || fail 'NFQUEUE target unavailable'
    [ "$(nvram get wans_mode)" = lb ] || fail "wans_mode is not 'lb'"
    [ -n "$(balance_line)" ] || fail 'firmware balance jump missing'
    if chain_exists && ! owned; then fail "foreign chain $CHAIN exists; preserving it"; fi
    if queue_bound && ! helper_alive; then fail 'queue already bound by another process'; fi
}
remove_rules() {
    if [ -r "$JUMP" ]; then
        owned || { echo 'wan-affinity: missing ownership proof; preserving rules' >&2; return 1; }
        _record=$(cat "$JUMP")
        case "$_record" in *"-j $CHAIN") ;; *) return 1 ;; esac
        # Record contains only tokens created below; never eval or source it.
        set -- $_record
        while ipt -C PREROUTING "$@" 2>/dev/null; do ipt -D PREROUTING "$@" || return 1; done
        rm -f "$JUMP"
    fi
    if owned; then
        if chain_exists; then
            [ "$(owned_jumps)" = 0 ] || return 1
            # A foreign reference from any other chain also prevents deletion.
            if ipt -S | awk -v c="$CHAIN" '{for(i=1;i<NF;i++)if($i=="-j" && $(i+1)==c)f=1}END{exit !f}'; then
                echo 'wan-affinity: foreign reference; preserving owned chain' >&2; return 1
            fi
            ipt -F "$CHAIN" && ipt -X "$CHAIN" || return 1
        fi
        rm -f "$OWNER"
    elif chain_exists; then
        echo "wan-affinity: preserving foreign chain $CHAIN" >&2; return 1
    fi
}
stop_helper() {
    if helper_alive; then
        _pid=$(cat "$PIDF")
        kill -CONT "$_pid" 2>/dev/null || true
        kill -TERM "$_pid" 2>/dev/null || true
        _n=0
        while [ "$_n" -lt 5 ] && kill -0 "$_pid" 2>/dev/null; do sleep 1; _n=$((_n+1)); done
        if helper_alive; then
            kill -KILL "$_pid" 2>/dev/null || true
            sleep 1
            helper_alive && return 1
        fi
    fi
    rm -f "$PIDF"
}
start_helper() {
    rm -f "$RUN/status" "$RUN/status.new"
    # Never let the long-lived helper or its children inherit the control lock.
    "$BIN" --config "$CFG" --queue "$QUEUE_NUM" --status-dir "$RUN" 9>&- </dev/null >>"$LOG" 2>&1 &
    echo $! >"$PIDF"
    _n=0
    while [ "$_n" -lt 10 ]; do
        if helper_alive && queue_bound && [ -s "$RUN/status" ]; then return 0; fi
        sleep 1; _n=$((_n+1))
    done
    return 1
}
build_chain() {
    chain_exists && return 1
    # Write intent before mutation, so interruption cannot strand our chain.
    printf '%s\n' "$CHAIN" >"$OWNER" || return 1
    ipt -N "$CHAIN" || { rm -f "$OWNER"; return 1; }
    # NEW includes retransmissions/unreplied UDP of an already confirmed flow.
    # Admit only genuinely unconfirmed connections, never retag an existing NAT
    # mapping after a health/policy change. Firmware restore runs after balance.
    ipt -A "$CHAIN" -m conntrack --ctstatus CONFIRMED -j RETURN || return 1
    ipt -A "$CHAIN" -m connmark ! --mark 0x0/0xf0000000 -j RETURN || return 1
    # Pass sentinel occupies the already-reserved WAN field, not unrelated bits.
    ipt -A "$CHAIN" -m mark --mark 0x0/0xf0000000 -j NFQUEUE --queue-num "$QUEUE_NUM" --queue-bypass || return 1
    ipt -A "$CHAIN" -m mark --mark 0xa0000000/0xf0000000 -j MARK --set-xmark 0x0/0xf0000000 || return 1
    for mark in 0x80000000 0x90000000; do
        ipt -A "$CHAIN" -m mark --mark "$mark/0xf0000000" -j CONNMARK --save-mark --nfmask 0xf0000000 --ctmask 0xf0000000 || return 1
    done
    ipt -A "$CHAIN" -j RETURN || return 1
    ipt -S "$CHAIN" >"$RUN/chain.rules"
}
insert_jump() {
    _pos=$(balance_line); [ -n "$_pos" ] || return 1
    if [ "$LAN_SCOPE" = 1 ]; then _scope="$(nvram get lan_ipaddr)/$(nvram get lan_netmask)"; else _scope="$CLIENT"; fi
    _lan=$(nvram get lan_ifname); _lan=${_lan:-br0}
    case "$_lan" in *[!A-Za-z0-9_.:-]*|'') return 1 ;; esac
    _record="-i $_lan -m conntrack --ctstate NEW --ctdir ORIGINAL -s $_scope -j $CHAIN"
    printf '%s\n' "$_record" >"$JUMP.new" && mv "$JUMP.new" "$JUMP" || return 1
    set -- $_record
    ipt -I PREROUTING "$_pos" "$@"
}
rules_current() {
    owned && [ -r "$JUMP" ] && [ -r "$RUN/chain.rules" ] || return 1
    [ "$(owned_jumps)" = 1 ] || return 1
    set -- $(cat "$JUMP")
    ipt -C PREROUTING "$@" 2>/dev/null || return 1
    ipt -S "$CHAIN" >"$RUN/chain.check" || return 1
    cmp -s "$RUN/chain.rules" "$RUN/chain.check" || return 1
    ipt -S PREROUTING | awk -v c="$CHAIN" '
      $1=="-A" {n++;for(i=1;i<NF;i++)if($i=="-j"){if($(i+1)==c)a=n;if($(i+1)=="balance" && !b)b=n}}
      END{exit !(a && b && a<b)}'
}
monitor_alive() {
    [ -r "$MONPID" ] || return 1
    read _mp <"$MONPID"
    case "$_mp" in ''|*[!0-9]*) return 1 ;; esac
    kill -0 "$_mp" 2>/dev/null || return 1
    _monitor_args=$(tr '\000' '\n' <"$WA_PROC/$_mp/cmdline" 2>/dev/null)
    # Accept the shell bootstrap and its exec'd independent supervisor.
    [ "$(printf '%s\n' "$_monitor_args" | sed -n '2,3p')" = "$SELF
watch" ] || [ "$(printf '%s\n' "$_monitor_args" | sed -n '1,4p')" = "$BIN
--supervise
$SELF
$RUN" ]
}
start_monitor() {
    [ "$SUPERVISE" = 1 ] || return 0
    monitor_alive && return 0
    nohup sh "$SELF" watch 9>&- </dev/null >>"$RUN/supervisor.log" 2>&1 &
    echo $! >"$MONPID"
}
stop_monitor() {
    if monitor_alive; then kill -TERM "$_mp" 2>/dev/null || true; fi
    rm -f "$MONPID"
}
rollback_start() { remove_rules || true; [ "$NEW_HELPER" != 1 ] || stop_helper || true; }
on_exit() { _rc=$?; [ "$STARTING" != 1 ] || rollback_start; exit "$_rc"; }
trap on_exit 0
cmd_start() {
    control_lock
    if [ "${1:-start}" = reconcile ]; then
        # Restore ordinary firmware traversal before attempting to restart a
        # missing helper, even if activation settings have since become invalid.
        if ! helper_alive; then remove_rules || fail 'could not remove abandoned admission rules'; fi
        if [ -f "$RUN/supervisor-tripped" ]; then
            remove_rules && stop_helper || fail 'could not leave firmware in control'
            return 0
        fi
        if config_disabled; then cmd_stop; return 0; fi
        [ ! -f "$RUN/stopped" ] || return 0
    fi
    load_config
    if [ "${1:-start}" = reconcile ]; then
        if [ "$ENABLED" != 1 ]; then cmd_stop; return 0; fi
    else
        rm -f "$RUN/stopped" "$RUN/supervisor-tripped"
    fi
    [ "$ENABLED" = 1 ] || fail 'disabled in config; refusing activation'
    validate_config
    # Boot hooks may run before the firmware firewall or queue interface exists.
    # Keep a retry owner even if this first attempt cannot become ready yet.
    start_monitor
    prereq
    _config=$(fingerprint "$CFG") || fail 'cannot fingerprint config (md5sum or cksum required)'
    if helper_alive && queue_bound && [ ! -f "$RUN/restart-request" ] && [ -r "$RUN/config.signature" ] &&
       [ "$(cat "$RUN/config.signature")" = "$_config" ]; then
        if ! rules_current; then
            STARTING=1
            remove_rules && build_chain && insert_jump || fail 'rule repair failed'
            STARTING=0
            say 'repaired owned rules; retained helper and source associations'
        fi
        start_monitor
        return 0
    fi
    STARTING=1
    remove_rules || fail 'could not remove prior owned rules'
    stop_helper || fail 'could not stop prior helper'
    NEW_HELPER=1
    start_helper || fail 'helper readiness failed'
    [ ! -f "$RUN/supervisor-tripped" ] || fail 'supervisor stopped repeated recovery attempts'
    build_chain || fail 'chain construction failed'
    insert_jump || fail 'jump insertion failed'
    printf '%s\n' "$_config" >"$RUN/config.signature"
    rm -f "$RUN/restart-request"
    STARTING=0
    start_monitor
    say "started queue=$QUEUE_NUM mode=$MODE client=${CLIENT:-LAN}"
}
cmd_stop() {
    control_lock; touch "$RUN/stopped"; stop_monitor
    remove_rules || fail 'owned rule removal failed'
    stop_helper || fail 'helper stop failed'; say stopped
}
cmd_watch() {
    exec "$BIN" --supervise "$SELF" "$RUN" "$BIN"
}
cmd_status() {
    if helper_alive; then say "helper running pid=$(cat "$PIDF")"; else say 'helper not running'; fi
    [ ! -f "$RUN/supervisor-tripped" ] || say 'supervisor-tripped: firmware balancing; explicit start required'
    say "jumps=$(owned_jumps)"; [ ! -r "$RUN/status" ] || cat "$RUN/status"
    return 0
}
validate_paths
case "${1:-}" in
 start) cmd_start ;;
 reconcile) cmd_start reconcile ;;
 restart) cmd_stop; cmd_start ;;
 watch) cmd_watch ;;
 recover-stop) control_lock; [ -f "$RUN/supervisor-tripped" ] || exit 0; remove_rules && stop_helper ;;
 status) cmd_status ;;
 stop) cmd_stop ;;
 cleanup) cmd_stop; rm -f "$RUN/status" "$RUN/status.new" "$LOG" ;;
 disable) control_lock; cmd_stop; cfg_set ENABLED 0 || fail 'stopped, but could not persist ENABLED=0' ;;
 uninstall) cmd_stop; [ ! -f "$BASE/hooks.sh" ] || sh "$BASE/hooks.sh" remove; rm -rf "$BASE" "$RUN" ;;
 *) echo "usage: $0 {start|reconcile|restart|status|stop|disable|cleanup|uninstall}" >&2; exit 2 ;;
esac
