#!/bin/sh
# Internal bounded worker. The C parent owns the service lock and all health.
# Keep the original script sourceable as the routing/DDNS reference/rollback.
set -u
: "${WW_LEGACY:?legacy implementation required}" "${STATE_DIR:?runtime required}"
WAN_WATCHDOG_LIBRARY_ONLY=1
export WAN_WATCHDOG_LIBRARY_ONLY
. "$WW_LEGACY"
phase=${1:-inspect}
mutations_allowed() { [ "${WW_SHADOW:-1}" = 0 ] || { echo 'shadow mutation refused' >&2; exit 71; }; }
case "$phase" in
init)
    mutations_allowed
    routing_init || exit 70
    previous_mode=$(read_number "$OWNERSHIP_DIR/mode" '')
    if [ -n "$previous_mode" ] && [ "$previous_mode" != "$FAILOVER_MODE" ]; then
        remove_overlay || exit 70
        rm -f "$OWNERSHIP_DIR/whole-failover"
    fi
    printf '%s\n' "$FAILOVER_MODE" >"$OWNERSHIP_DIR/mode"
    startup_failed=$(read_number "$OWNERSHIP_DIR/whole-failover" none)
    case "$startup_failed" in 0|1) echo 1 >"$STATE_DIR/active_wan$startup_failed";; esac
    for startup_unit in 0 1; do
        for startup_ip in $(configured_ips_for_unit "$startup_unit"); do
            if [ "$(current_target_for_ip "$startup_ip")" = "wan$((1-startup_unit))" ]; then
                echo 1 >"$STATE_DIR/active_wan$startup_unit"
            fi
        done
    done
    if [ "$FAILOVER_MODE" = whole_lan ] && [ "$startup_failed" = none ]; then
        if [ -f "$STATE_DIR/active_wan0" ] && [ ! -f "$STATE_DIR/active_wan1" ]; then
            echo 0 >"$OWNERSHIP_DIR/whole-failover";echo 1 >"$STATE_DIR/migrate-pins"
        elif [ -f "$STATE_DIR/active_wan1" ] && [ ! -f "$STATE_DIR/active_wan0" ]; then
            echo 1 >"$OWNERSHIP_DIR/whole-failover";echo 1 >"$STATE_DIR/migrate-pins"
        fi
    fi
    prepare_probe_routes || exit 70
    ;;
apply)
    mutations_allowed
    # Fields are numeric argv from the native core, never shell expressions.
    shift
    [ "$#" = 8 ] || exit 72
    for value in "$@"; do case "$value" in -1|0|1) ;; *) exit 72;; esac; done
    wanted=$1;restore=$2;p0=$3;p1=$4;a0=$5;a1=$6;m0=$7;m1=$8
    shift
    for value in "$@"; do case "$value" in 0|1) ;; *) exit 72;; esac; done
    [ "$restore" != 1 ] || [ "$wanted" = -1 ] || exit 72
    namespace_safe || { remove_rule_set all; exit 70; }
    # A classification for a previous lease/interface must not drive current
    # routing. Tests without an expected view retain the standalone adapter API.
    for unit in 0 1;do
        if [ "$unit" = 0 ];then expected=${WW_EXPECT0:-};else expected=${WW_EXPECT1:-};fi
        if [ -n "$expected" ];then
            e=$(nvram get wan${unit}_enable);ipaddr=$(nvram get wan${unit}_ipaddr)
            gateway=$(nvram get wan${unit}_gateway);iface=$(nvram get wan${unit}_ifname)
            case "$e" in 0|1) ;; *) e=-1;; esac
            current="$e:${ipaddr:-0.0.0.0}:${gateway:-0.0.0.0}:$iface"
            [ "$current" = "$expected" ] || exit 75
        fi
    done
    select_probe_targets || exit 70
    [ -z "${WW_TARGET0:-}" ] || [ "$TARGET_A" = "$WW_TARGET0" ] || exit 75
    [ -z "${WW_TARGET1:-}" ] || [ "$TARGET_B" = "$WW_TARGET1" ] || exit 75
    prepare_probe_routes || exit 70
    if [ "$FAILOVER_MODE" = whole_lan ]; then
        failed=$(read_number "$OWNERSHIP_DIR/whole-failover" none)
        if [ "$restore" = 1 ]; then
            if remove_overlay; then
                rm -f "$OWNERSHIP_DIR/whole-failover" "$STATE_DIR/active_wan0" "$STATE_DIR/active_wan1"
            else exit 73;fi
        elif [ "$wanted" != -1 ]; then
            other=$((1-wanted));set -- $(cat "$STATE_DIR/health_wan$other")
            if [ "$2" = 1 ]; then
                if [ "$failed" != "$wanted" ]; then
                    echo "$wanted" >"$OWNERSHIP_DIR/whole-failover.new"
                    mv "$OWNERSHIP_DIR/whole-failover.new" "$OWNERSHIP_DIR/whole-failover"
                    rm -f "$STATE_DIR/active_wan0" "$STATE_DIR/active_wan1" "$STATE_DIR/recovery_wan0" "$STATE_DIR/recovery_wan1"
                    echo 1 >"$STATE_DIR/active_wan$wanted"
                fi
                apply_overlay "$other" whole_lan || exit 73
                if [ -f "$STATE_DIR/migrate-pins" ]; then
                    ensure_unit_rules 0 wan0 && ensure_unit_rules 1 wan1 && rm -f "$STATE_DIR/migrate-pins"
                fi
            fi
        fi
    else
        for unit in 0 1; do
            if [ "$unit" = 0 ]; then dest=$p0;active=$a0;mask=$m0;else dest=$p1;active=$a1;mask=$m1;fi
            [ "$mask" = 1 ] || continue
            [ "$active" = 0 ] || echo 1 >"$STATE_DIR/active_wan$unit"
            ensure_unit_rules "$unit" "wan$dest" || exit 73
            [ "$active" = 1 ] || rm -f "$STATE_DIR/active_wan$unit" "$STATE_DIR/recovery_wan$unit"
        done
        pinned_dns_good=none
        set -- $(cat "$STATE_DIR/health_wan1");good1=$2
        set -- $(cat "$STATE_DIR/health_wan0");good0=$2
        if [ -f "$STATE_DIR/active_wan0" ] && [ "$good1" = 1 ]; then pinned_dns_good=1
        elif [ -f "$STATE_DIR/active_wan1" ] && [ "$good0" = 1 ]; then pinned_dns_good=0;fi
        case "$pinned_dns_good" in 0|1) apply_overlay "$pinned_dns_good" dns_only || exit 73;;
          *) if [ ! -f "$STATE_DIR/active_wan0" ] && [ ! -f "$STATE_DIR/active_wan1" ]; then remove_overlay || exit 73;fi;; esac
    fi
    # The legacy helper already isolates network/provider work in children;
    # route/ledger mutations in ddns_tick remain serialized in this worker.
    NOW=${WW_SAMPLE_SECONDS:?sample timestamp required}
    if [ "$DDNS_HELPER_LOADED" = 1 ]; then ddns_tick;fi
    ;;
inspect) ;;
cleanup) mutations_allowed;cleanup_owned;exit $?;;
*) exit 72;;
esac

# A complete, bounded, allowlisted native-fact view; no NVRAM dump/secrets.
select_probe_targets || exit 74
failed=$(read_number "$OWNERSHIP_DIR/whole-failover" -1)
case "$failed" in 0|1) ;; *) failed=-1;; esac
printf 'R %s\n' "$failed"
for unit in 0 1; do
    enable=$(nvram get wan${unit}_enable);iface=$(nvram get wan${unit}_ifname)
    addr=$(nvram get wan${unit}_ipaddr);gateway=$(nvram get wan${unit}_gateway)
    case "$enable" in 0|1) ;; *) enable=-1;; esac
    a=-1;b=-1
    if [ "$enable" = 1 ] && [ -n "$iface" ] && [ -n "$addr" ] && [ "$addr" != 0.0.0.0 ]; then
        a=0;b=0
        for t in "$TARGET_A" "$TARGET_B"; do
            valid=0
            ip route get "$t" from "$addr" 2>/dev/null | awk -v iface="$iface" '
                {for(i=1;i<NF;i++)if($i=="dev" && $(i+1)==iface)found=1}END{exit !found}' && valid=1
            if [ "$t" = "$TARGET_A" ];then a=$valid;else b=$valid;fi
        done
    fi
    active=0;[ ! -f "$STATE_DIR/active_wan$unit" ] || active=1
    printf 'F %s %s %s %s %s %s %s %s %s %s\n' "$unit" "$enable" "${iface:--}" "${addr:-0.0.0.0}" "${gateway:-0.0.0.0}" "$active" "$a" "$b" "$TARGET_A" "$TARGET_B"
done
printf 'END\n'
