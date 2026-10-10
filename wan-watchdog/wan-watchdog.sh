#!/bin/sh
# Loss-aware whole-LAN failover, with a configurable pinned-only mode.
# See README.md for thresholds and rollback.

STATE_DIR=${STATE_DIR:-/tmp/wan-watchdog}
CHECK_INTERVAL=2
WINDOW_SECONDS=30
MIN_SAMPLES=10
MIN_SPAN=26
DEGRADED_LOSS=30
BACKUP_LOSS=10
RECOVERY_LOSS=5
RECOVERY_SECONDS=120
HARD_DOWN_SECONDS=12
TARGET_A=8.8.8.8
TARGET_B=9.9.9.9
PID_FILE="$STATE_DIR/wan-watchdog.pid"

# Modes: whole_lan (default) or pinned. These can also be set in the environment.
FAILOVER_MODE=${FAILOVER_MODE:-whole_lan}
FAILOVER_PRIORITY=${FAILOVER_PRIORITY:-88}
GUARD_PRIORITY=$((FAILOVER_PRIORITY - 1))
PROBE_PRIORITY=$((FAILOVER_PRIORITY - 2))
FAILOVER_TABLE=${FAILOVER_TABLE:-20188}
PROBE_TABLE0=${PROBE_TABLE0:-20186}
PROBE_TABLE1=${PROBE_TABLE1:-20187}
OWNERSHIP_DIR=${OWNERSHIP_DIR:-/jffs/wan-watchdog}
OWNED_RULES="$OWNERSHIP_DIR/owned-rules"
OWNED_TABLES="$OWNERSHIP_DIR/owned-tables"
PREFERRED_TARGET_A=$TARGET_A
PREFERRED_TARGET_B=$TARGET_B
PROBE_FALLBACKS=${PROBE_FALLBACKS:-"8.8.4.4 9.9.9.11 1.1.1.2 208.67.222.222"}
# auto covers active LAN bridges; otherwise specify space-separated IPv4 CIDRs.
LAN_NETWORKS=${LAN_NETWORKS:-auto}
# auto reads active resolver configuration; otherwise supply resolver IPv4s.
DNS_SERVERS=${DNS_SERVERS:-auto}
# Optional installed helper coordinates native DDNS only when the UI says Auto.
DDNS_HELPER=${DDNS_HELPER:-/jffs/scripts/wan-watchdog-ddns}
DDNS_HELPER_LOADED=0
if [ -r "$DDNS_HELPER" ]; then
  WAN_WATCHDOG_DDNS_LIBRARY_ONLY=1
  . "$DDNS_HELPER"
  unset WAN_WATCHDOG_DDNS_LIBRARY_ONLY
  DDNS_HELPER_LOADED=1
fi

log() { logger -t wan-watchdog "$*"; }
now_seconds() { awk '{print int($1)}' /proc/uptime; }
read_number() { if [ -f "$1" ]; then cat "$1"; else echo "$2"; fi; }

configured_ips_for_unit() {
  nvram get wans_routing_rulelist | tr '<' '\n' | awk -F'>' -v unit="$1" '
    NF >= 3 && $1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && $3 == unit {print $1}'
}
current_target_for_ip() {
  ip rule show | awk -v ip="$1" '$1 == "100:" && $3 == ip && $4 == "lookup" {print $5; exit}'
}
set_rule_for_ip() {
  # Add first so a failed insertion does not discard the working route.
  if ! ip rule show | awk -v source="$1" -v target="$2" '
      $1=="100:" && $3==source && $4=="lookup" && $5==target {found=1} END {exit !found}'; then
    ip rule add from "$1" lookup "$2" priority 100 || return 1
  fi
  other=wan0
  [ "$2" = wan0 ] && other=wan1
  while ip rule del from "$1" lookup "$other" priority 100 2>/dev/null; do :; done
  log "Set $1 to $2"
}
ensure_unit_rules() {
  rule_unit=$1
  rule_target=$2
  rule_error=0
  rule_changed=0
  for rule_ip in $(configured_ips_for_unit "$rule_unit"); do
    if [ "$(current_target_for_ip "$rule_ip")" != "$rule_target" ]; then
      if set_rule_for_ip "$rule_ip" "$rule_target"; then rule_changed=1; else rule_error=1; fi
    fi
  done
  [ "$rule_changed" -eq 0 ] || ip route flush cache 2>/dev/null
  return "$rule_error"
}

# Persistent write-ahead ownership ledger. No eval: records are ip argv tokens.
# Rule priorities alone are never used as a deletion selector.
ledger_add() {
  ledger_file=$1; ledger_line=$2
  grep -qxF -e "$ledger_line" "$ledger_file" 2>/dev/null && return 0
  { cat "$ledger_file" 2>/dev/null; printf '%s\n' "$ledger_line"; } > "$ledger_file.new" || return 1
  mv "$ledger_file.new" "$ledger_file"
}
ledger_remove() {
  ledger_file=$1; ledger_line=$2
  awk -v row="$ledger_line" '$0 != row' "$ledger_file" > "$ledger_file.new" || return 1
  mv "$ledger_file.new" "$ledger_file"
}
canonical_rules() {
  ip -4 rule show | awk '
    {pref=$1; sub(/:$/, "", pref); if(pref !~ /^[0-9]+$/) next;
     text=""; for(i=2;i<=NF;i++) {
       if($i=="[unresolved]") continue;
       if($i=="proto" || $i=="protocol") {i++; continue}
       word=$i; sub(/\/32$/, "", word);
       text=text (text ? " " : "") word;
     }
     print pref " " text}'
}
rule_record() {
  printf '%s\n' "$*" | awk '{for(i=1;i<=NF;i++) {sub(/\/32$/, "", $i); if($i=="0.0.0.0/0") $i="all"} print}'
}
owned_rule_add() {
  own_record=$(rule_record "$@")
  own_pref=$1; shift
  if canonical_rules | grep -qxF -e "$own_record"; then
    # A rule which existed before its intent was recorded is not ours.
    grep -qxF -e "$own_record" "$OWNED_RULES" 2>/dev/null
    return $?
  fi
  ledger_add "$OWNED_RULES" "$own_record" || return 1
  ip -4 rule add priority "$own_pref" "$@"
}
owned_rule_remove() {
  own_record=$1
  grep -qxF -e "$own_record" "$OWNED_RULES" 2>/dev/null || return 1
  set -- $own_record
  own_pref=$1; shift
  if canonical_rules | grep -qxF -e "$own_record"; then
    ip -4 rule del priority "$own_pref" "$@" || return 1
  fi
  ledger_remove "$OWNED_RULES" "$own_record"
}
claim_table() {
  claim_id=$1
  grep -qxF -e "$claim_id" "$OWNED_TABLES" 2>/dev/null && return 0
  # Refuse pre-existing table contents or references before claiming the ID.
  [ -z "$(ip -4 route show table "$claim_id" 2>/dev/null)" ] || return 1
  if canonical_rules | awk -v id="$claim_id" '
      {for(i=1;i<NF;i++) if($i=="lookup" && $(i+1)==id) found=1} END {exit !found}'; then
    return 1
  fi
  ledger_add "$OWNED_TABLES" "$claim_id"
}
namespace_safe() {
  canonical_rules > "$STATE_DIR/kernel-rules"
  awk -v probe="$PROBE_PRIORITY" -v guard="$GUARD_PRIORITY" -v override="$FAILOVER_PRIORITY" \
      -v t0="$PROBE_TABLE0" -v t1="$PROBE_TABLE1" -v tf="$FAILOVER_TABLE" '
    FILENAME==ARGV[1] {owned[$0]=1; next}
    !owned[$0] {
      if($1==probe || $1==guard || $1==override) bad=1;
      for(i=1;i<NF;i++) if($i=="lookup" && ($(i+1)==t0 || $(i+1)==t1 || $(i+1)==tf)) bad=1;
    }
    END {exit bad}' "$OWNED_RULES" "$STATE_DIR/kernel-rules"
}
routing_init() {
  select_probe_targets || return 1
  mkdir -p "$OWNERSHIP_DIR" || return 1
  chmod 700 "$OWNERSHIP_DIR"
  touch "$OWNED_RULES" "$OWNED_TABLES"
  namespace_safe || return 1
  for init_table in "$FAILOVER_TABLE" "$PROBE_TABLE0" "$PROBE_TABLE1"; do
    claim_table "$init_table" || return 1
  done
}
remove_rule_set() {
  remove_kind=$1
  cp "$OWNED_RULES" "$STATE_DIR/remove-rules"
  while IFS= read -r remove_record; do
    [ -n "$remove_record" ] || continue
    remove_table=$(printf '%s\n' "$remove_record" | awk '{for(i=1;i<NF;i++) if($i=="lookup") print $(i+1)}')
    case "$remove_kind:$remove_table" in
      overlay:"$PROBE_TABLE0"|overlay:"$PROBE_TABLE1") continue ;;
    esac
    owned_rule_remove "$remove_record" || return 1
  done < "$STATE_DIR/remove-rules"
}
cleanup_owned() {
  [ -f "$OWNED_RULES" ] || return 0
  remove_rule_set all || return 1
  # Only flush table IDs we successfully leased, and never foreign references.
  while IFS= read -r cleanup_table; do
    case "$cleanup_table" in ''|*[!0-9]*) return 1 ;; esac
    if canonical_rules | awk -v id="$cleanup_table" '
        {for(i=1;i<NF;i++) if($i=="lookup" && $(i+1)==id) found=1} END {exit !found}'; then
      log "Table $cleanup_table has a foreign reference; leaving its routes intact"
      return 1
    fi
    ip -4 route flush table "$cleanup_table" || return 1
  done < "$OWNED_TABLES"
  : > "$OWNED_TABLES"
  rm -f "$OWNERSHIP_DIR/whole-failover" "$STATE_DIR/overlay-signature"
  if [ -f "$OWNERSHIP_DIR/ddns-auto-unit" ]; then
    rm -f "$OWNERSHIP_DIR/ddns-auto-unit" "$STATE_DIR/ddns-recovery"
    if [ -x "$DDNS_HELPER" ]; then
      ( exec 9>&-; "$DDNS_HELPER" --update native ) >/dev/null 2>&1 &
    fi
  fi
}
setup_wan_table() {
  setup_unit=$1; setup_table=$2
  setup_iface=$(nvram get wan${setup_unit}_ifname)
  setup_ip=$(nvram get wan${setup_unit}_ipaddr)
  setup_gw=$(nvram get wan${setup_unit}_gateway)
  [ "$(nvram get wan${setup_unit}_enable)" = 1 ] || return 1
  [ -n "$setup_iface" ] && [ -n "$setup_ip" ] && [ "$setup_ip" != 0.0.0.0 ] || return 1
  case "$setup_gw" in ''|0.0.0.0) return 1 ;; esac
  grep -qxF -e "$setup_table" "$OWNED_TABLES" || return 1
  ip -4 route replace "$setup_gw/32" dev "$setup_iface" scope link table "$setup_table" || return 1
  ip -4 route replace default via "$setup_gw" dev "$setup_iface" table "$setup_table"
}
prepare_probe_routes() {
  : > "$STATE_DIR/wanted-probes"
  for pr_unit in 0 1; do
    pr_table=$PROBE_TABLE0; [ "$pr_unit" = 0 ] || pr_table=$PROBE_TABLE1
    pr_ip=$(nvram get wan${pr_unit}_ipaddr)
    pr_signature="$(nvram get wan${pr_unit}_enable):$pr_ip:$(nvram get wan${pr_unit}_gateway):$(nvram get wan${pr_unit}_ifname)"
    if [ "$(cat "$STATE_DIR/probe-routing$pr_unit" 2>/dev/null)" != "$pr_signature" ] || \
        ! ip -4 route show table "$pr_table" | grep -q '^default '; then
      if setup_wan_table "$pr_unit" "$pr_table"; then
        echo "$pr_signature" > "$STATE_DIR/probe-routing$pr_unit"
      else
        continue
      fi
    fi
    [ "$(nvram get wan${pr_unit}_enable)" = 1 ] || continue
    for pr_target in "$TARGET_A" "$TARGET_B"; do
      pr_record=$(rule_record "$PROBE_PRIORITY from $pr_ip/32 to $pr_target/32 lookup $pr_table")
      echo "$pr_record" >> "$STATE_DIR/wanted-probes"
      owned_rule_add $pr_record || return 1
    done
  done
  cp "$OWNED_RULES" "$STATE_DIR/old-probes"
  while IFS= read -r pr_record; do
    case "$pr_record" in
      "$PROBE_PRIORITY from all to "*" iif lo lookup $PROBE_TABLE0"|"$PROBE_PRIORITY from all to "*" iif lo lookup $PROBE_TABLE1") continue ;;
      *" lookup $PROBE_TABLE0"|*" lookup $PROBE_TABLE1")
      grep -qxF -e "$pr_record" "$STATE_DIR/wanted-probes" || owned_rule_remove "$pr_record" || return 1 ;;
    esac
  done < "$STATE_DIR/old-probes"

}
network_cidr() {
  awk -v value="$1" 'BEGIN {
    split(value,p,"/"); n=split(p[1],a,"."); bits=p[2];
    if(n!=4 || bits !~ /^[0-9]+$/ || bits<0 || bits>32) exit 1;
    ip=0; for(i=1;i<=4;i++) {if(a[i] !~ /^[0-9]+$/ || a[i]>255) exit 1; ip=ip*256+a[i]}
    block=2^(32-bits); ip=int(ip/block)*block;
    for(i=4;i>=1;i--) {octet[i]=ip%256; ip=int(ip/256)}
    printf "%d.%d.%d.%d/%d\n",octet[1],octet[2],octet[3],octet[4],bits;
  }'
}
lan_networks() {
  if [ "$LAN_NETWORKS" = auto ]; then
    lan_iface=$(nvram get lan_ifname)
    ip -o -4 addr show | awk -v lan="$lan_iface" '$2==lan || $2 ~ /^br[0-9]+$/ {print $4}'
  else
    printf '%s\n' $LAN_NETWORKS
  fi | while IFS= read -r lan_cidr; do network_cidr "$lan_cidr" || exit 1; done | sort -u
}
dns_servers() {
  if [ "$DNS_SERVERS" = auto ]; then
    {
      for dns_unit in 0 1; do nvram get wan${dns_unit}_dns; done
      awk '$1=="nameserver" {print $2}' /etc/resolv.conf /tmp/resolv.dnsmasq 2>/dev/null
      awk '$1=="-" && $2=="address_data:" {gsub(/["\047]/,"",$3); print $3}' /etc/stubby/stubby.yml 2>/dev/null
    }
  else
    printf '%s\n' $DNS_SERVERS
  fi | tr ' ' '\n' | awk -F. '
    NF==4 {valid=1; for(i=1;i<=4;i++) if($i !~ /^[0-9]+$/ || $i>255) valid=0;
      if(valid && $1!=0 && $1!=127 && $1<224) print}' | sort -u
}
select_probe_targets() {
  dns_servers > "$STATE_DIR/probe-dns-exclusions"
  selected_a=; selected_b=
  for candidate in "$PREFERRED_TARGET_A" "$PREFERRED_TARGET_B" $PROBE_FALLBACKS; do
    grep -qxF -e "$candidate" "$STATE_DIR/probe-dns-exclusions" && continue
    [ "$candidate" != "$selected_a" ] || continue
    if [ -z "$selected_a" ]; then selected_a=$candidate
    else selected_b=$candidate; break; fi
  done
  [ -n "$selected_a" ] && [ -n "$selected_b" ] || return 1
  TARGET_A=$selected_a; TARGET_B=$selected_b
}

vpn_guards() {
  # Match existing VPN selectors, then jump directly to their original priority.
  # No VPN table copy or edit; its routing/killswitch decisions remain native.
  canonical_rules | awk -v override="$FAILOVER_PRIORITY" -v guard="$GUARD_PRIORITY" '
    $1>=override && $1<32766 {
      table=""; lookup=0; has_to=0;
      for(i=2;i<NF;i++) {if($i=="lookup") {table=$(i+1); lookup=i} if($i=="to" && $(i+1)!="all") has_to=1}
      if(table ~ /^(ovpnc|wgc)[0-9]+$/ || (table>=111 && table<=120) || (table=="main" && has_to)) {
        selector=""; for(i=2;i<lookup;i++) selector=selector (selector ? " " : "") $i;
        print guard " " selector " goto " $1;
      }
    }' | sort -u
}
local_throws() {
  # Only direct/VPN routes, not DNS host routes via a WAN gateway.
  ip -4 route show table main | awk '
    $1!="default" && $1 ~ /^[0-9]/ {
      via=0; dev=""; for(i=2;i<NF;i++) {if($i=="via") via=1; if($i=="dev") dev=$(i+1)}
      if(!via || dev ~ /^(br|tun|tap|wg)/) print $1;
    }' | sort -u
}
apply_overlay() {
  overlay_good=$1; overlay_mode=$2
  namespace_safe || return 1
  lan_networks > "$STATE_DIR/lan-networks" || return 1
  [ "$overlay_mode" != whole_lan ] || [ -s "$STATE_DIR/lan-networks" ] || return 1
  dns_servers > "$STATE_DIR/dns-servers"
  vpn_guards > "$STATE_DIR/vpn-guards"
  local_throws | awk -v gw="$(nvram get wan${overlay_good}_gateway)" '$0!=gw && $0!=gw"/32"' > "$STATE_DIR/local-throws"
  overlay_signature="$overlay_good:$overlay_mode:$(nvram get wan${overlay_good}_ipaddr):$(nvram get wan${overlay_good}_gateway):$(nvram get wan${overlay_good}_ifname):$(cat "$STATE_DIR/lan-networks" "$STATE_DIR/dns-servers" "$STATE_DIR/vpn-guards" "$STATE_DIR/local-throws")"
  if [ "$(cat "$STATE_DIR/overlay-signature" 2>/dev/null)" != "$overlay_signature" ] || \
      ! ip -4 route show table "$FAILOVER_TABLE" | grep -q '^default '; then
    # Gateway replacement first. Keep the previous default if new setup fails.
    setup_wan_table "$overlay_good" "$FAILOVER_TABLE" || return 1
    # Delete stale throw routes only in our leased table.
    ip -4 route show table "$FAILOVER_TABLE" | awk '$1=="throw" {print $2}' > "$STATE_DIR/old-throws"
    while IFS= read -r throw_cidr; do
      grep -qxF -e "$throw_cidr" "$STATE_DIR/local-throws" || ip -4 route del throw "$throw_cidr" table "$FAILOVER_TABLE" || return 1
    done < "$STATE_DIR/old-throws"
    while IFS= read -r throw_cidr; do
      ip -4 route replace throw "$throw_cidr" table "$FAILOVER_TABLE" || return 1
    done < "$STATE_DIR/local-throws"
  fi
  : > "$STATE_DIR/wanted-overlay"
  # Guards must exist before priority-88 rules. goto's original target exists.
  while IFS= read -r guard_record; do
    [ -n "$guard_record" ] || continue
    echo "$guard_record" >> "$STATE_DIR/wanted-overlay"
    owned_rule_add $guard_record || return 1
  done < "$STATE_DIR/vpn-guards"
  if [ "$overlay_mode" = whole_lan ]; then
    while IFS= read -r overlay_cidr; do
      overlay_record=$(rule_record "$FAILOVER_PRIORITY from $overlay_cidr lookup $FAILOVER_TABLE")
      echo "$overlay_record" >> "$STATE_DIR/wanted-overlay"
      owned_rule_add $overlay_record || return 1
    done < "$STATE_DIR/lan-networks"
  fi
  # Router-originated traffic to resolver addresses. Older kernels ignore port
  # selectors. Probe addresses are kept separate from resolver addresses.
  while IFS= read -r overlay_dns; do
    overlay_record=$(rule_record "$FAILOVER_PRIORITY from all to $overlay_dns/32 iif lo lookup $FAILOVER_TABLE")
    echo "$overlay_record" >> "$STATE_DIR/wanted-overlay"
    owned_rule_add $overlay_record || return 1
  done < "$STATE_DIR/dns-servers"
  cp "$OWNED_RULES" "$STATE_DIR/old-overlay"
  while IFS= read -r overlay_record; do
    case "$overlay_record" in *" lookup $PROBE_TABLE0"|*" lookup $PROBE_TABLE1") continue ;; esac
    grep -qxF -e "$overlay_record" "$STATE_DIR/wanted-overlay" || owned_rule_remove "$overlay_record" || return 1
  done < "$STATE_DIR/old-overlay"
  printf '%s\n' "$overlay_signature" > "$STATE_DIR/overlay-signature"
  ip -4 route flush cache 2>/dev/null
}
remove_overlay() {
  remove_rule_set overlay || return 1
  if canonical_rules | awk -v id="$FAILOVER_TABLE" '
      {for(i=1;i<NF;i++) if($i=="lookup" && $(i+1)==id) found=1} END {exit !found}'; then
    log "Failover table has a foreign reference; preserving its routes"
    return 1
  fi
  ip -4 route flush table "$FAILOVER_TABLE" || return 1
  rm -f "$STATE_DIR/overlay-signature"
}
whole_lan_decision() {
  set -- $(cat "$STATE_DIR/health_wan0"); wl_bad0=$1; wl_good0=$2; wl_recovered0=$3
  set -- $(cat "$STATE_DIR/health_wan1"); wl_bad1=$1; wl_good1=$2; wl_recovered1=$3
  wl_failed=$(read_number "$OWNERSHIP_DIR/whole-failover" none)
  case "$wl_failed" in 0|1) ;; *) wl_failed=none ;; esac
  # A newly failed destination can reverse failover if the other WAN is eligible.
  if [ "$wl_bad0" = 1 ] && [ "$wl_good1" = 1 ]; then wl_wanted=0
  elif [ "$wl_bad1" = 1 ] && [ "$wl_good0" = 1 ]; then wl_wanted=1
  else wl_wanted=$wl_failed; fi
  case "$wl_wanted" in 0|1)
    wl_good=$((1-wl_wanted))
    wl_destination_ok=$wl_good0; [ "$wl_good" = 0 ] || wl_destination_ok=$wl_good1
    wl_recovered=$wl_recovered0; [ "$wl_wanted" = 0 ] || wl_recovered=$wl_recovered1
    if [ "$wl_wanted" = "$wl_failed" ] && [ "$wl_recovered" = 1 ]; then
      if remove_overlay; then
        rm -f "$OWNERSHIP_DIR/whole-failover" "$STATE_DIR/active_wan0" "$STATE_DIR/active_wan1"
        transition_log "$wl_wanted" "recovered for ${RECOVERY_SECONDS}s; removed whole-LAN/DNS overrides"
      fi
    elif [ "$wl_destination_ok" = 1 ]; then
      # Write state before installing, preserving the recovery requirement on restart.
      if [ "$wl_failed" != "$wl_wanted" ]; then
        echo "$wl_wanted" > "$OWNERSHIP_DIR/whole-failover.new"
        mv "$OWNERSHIP_DIR/whole-failover.new" "$OWNERSHIP_DIR/whole-failover"
        rm -f "$STATE_DIR/active_wan0" "$STATE_DIR/active_wan1" "$STATE_DIR/recovery_wan0" "$STATE_DIR/recovery_wan1"
        echo 1 > "$STATE_DIR/active_wan$wl_wanted"
      fi
      if apply_overlay "$wl_good" whole_lan; then
        # Restore preferences changed by v2 only after a whole-LAN route covers them.
        if [ -f "$STATE_DIR/migrate-pins" ]; then
          if ensure_unit_rules 0 wan0 && ensure_unit_rules 1 wan1; then rm -f "$STATE_DIR/migrate-pins"; fi
        fi
        transition_log "$wl_wanted" "whole-LAN and router DNS redirected to WAN$wl_good"
      else transition_log "$wl_wanted" "whole-LAN rule update failed; preserving state for retry"; fi
    else
      transition_log "$wl_wanted" "no healthy destination; keeping existing whole-LAN routes"
    fi
    ;;
  esac
}

# An invalid route is an UNKNOWN measurement, never proof of a healthy WAN.
probe_target() {
  probe_ip=$1
  probe_iface=$2
  probe_target_ip=$3
  probe_file=$4
  probe_route=$(ip route get "$probe_target_ip" from "$probe_ip" 2>/dev/null)
  if ! printf '%s\n' "$probe_route" | awk -v iface="$probe_iface" '
      {for(i=1;i<NF;i++) if($i=="dev" && $(i+1)==iface) found=1} END {exit !found}'; then
    echo -1 > "$probe_file"
  elif ping -I "$probe_ip" -c 1 -W 1 -w 1 "$probe_target_ip" >/dev/null 2>&1; then
    echo 1 > "$probe_file"
  else
    echo 0 > "$probe_file"
  fi
}
prepare_probes() {
  for probe_unit in 0 1; do
    probe_enabled=$(nvram get wan${probe_unit}_enable)
    probe_iface=$(nvram get wan${probe_unit}_ifname)
    probe_ip=$(nvram get wan${probe_unit}_ipaddr)
    probe_gateway=$(nvram get wan${probe_unit}_gateway)
    probe_config="$probe_enabled:$probe_iface:$probe_ip:$probe_gateway:$TARGET_A:$TARGET_B"
    old_config=$(cat "$STATE_DIR/config_wan$probe_unit" 2>/dev/null)
    if [ "$old_config" != "$probe_config" ]; then
      rm -f "$STATE_DIR/samples_${probe_unit}_a" "$STATE_DIR/samples_${probe_unit}_b" \
        "$STATE_DIR/recovery_wan$probe_unit" "$STATE_DIR/down_wan$probe_unit"
      echo "${cycle_start:-${NOW:-0}}" > "$STATE_DIR/window_start_wan$probe_unit"
      printf '%s\n' "$probe_config" > "$STATE_DIR/config_wan$probe_unit"
      log "WAN$probe_unit configuration changed; resetting measurement window"
    fi
    echo "$probe_enabled" > "$STATE_DIR/enabled_wan$probe_unit"
    if [ "$probe_enabled" = 0 ]; then
      echo -1 > "$STATE_DIR/probe_${probe_unit}_a"
      echo -1 > "$STATE_DIR/probe_${probe_unit}_b"
    elif [ "$probe_enabled" = 1 ] && { [ -z "$probe_ip" ] || [ "$probe_ip" = 0.0.0.0 ]; }; then
      # Enabled WAN with no lease cannot carry traffic; use the hard-down timer.
      echo 0 > "$STATE_DIR/probe_${probe_unit}_a"
      echo 0 > "$STATE_DIR/probe_${probe_unit}_b"
    elif [ "$probe_enabled" != 1 ] || [ -z "$probe_iface" ]; then
      echo -1 > "$STATE_DIR/probe_${probe_unit}_a"
      echo -1 > "$STATE_DIR/probe_${probe_unit}_b"
    else
      echo -1 > "$STATE_DIR/probe_${probe_unit}_a"
      echo -1 > "$STATE_DIR/probe_${probe_unit}_b"
      probe_target "$probe_ip" "$probe_iface" "$TARGET_A" "$STATE_DIR/probe_${probe_unit}_a" &
      probe_target "$probe_ip" "$probe_iface" "$TARGET_B" "$STATE_DIR/probe_${probe_unit}_b" &
    fi
  done
  wait
}
record_sample() {
  sample_file=$1
  sample_result=$2
  # Preserve unknown samples, so route errors cannot produce a healthy window.
  printf '%s %s\n' "$NOW" "$sample_result" >> "$sample_file"
  awk -v cutoff="$((NOW - WINDOW_SECONDS))" '$1 > cutoff' "$sample_file" > "$sample_file.new"
  mv "$sample_file.new" "$sample_file"
}
window_stats() {
  awk -v minimum="$MIN_SAMPLES" -v span="$MIN_SPAN" '
    NR==1 {first=$1} {last=$1; n++; if($2==0) lost++; if($2<0) unknown++}
    END {printf "%d %d %d\n", n, n ? int(100*lost/n+0.999999) : 100,
      (n>=minimum && last-first>=span && unknown==0)}' "$1"
}
classify_wan() {
  classify_unit=$1
  enabled=$(read_number "$STATE_DIR/enabled_wan$classify_unit" -1)
  latest_a=$(read_number "$STATE_DIR/probe_${classify_unit}_a" -1)
  latest_b=$(read_number "$STATE_DIR/probe_${classify_unit}_b" -1)
  set -- $(window_stats "$STATE_DIR/samples_${classify_unit}_a")
  samples_a=$1; loss_a=$2; ready_a=$3
  set -- $(window_stats "$STATE_DIR/samples_${classify_unit}_b")
  samples_b=$1; loss_b=$2; ready_b=$3
  ready=$((ready_a * ready_b))
  window_start=$(read_number "$STATE_DIR/window_start_wan$classify_unit" "$NOW")
  # Require an actual full warm-up, while allowing 2–3s sampling under load/timeouts.
  [ "$((NOW - window_start))" -ge "$WINDOW_SECONDS" ] || ready=0
  acceptable=0; recoverable=0; bad=0; reason=warming
  if [ "$enabled" = 0 ]; then
    bad=1; reason=disabled
  elif [ "$enabled" != 1 ] || [ "$latest_a" -lt 0 ] || [ "$latest_b" -lt 0 ]; then
    reason=unknown
  else
    if [ "$ready" -eq 1 ]; then
      reason=usable
      if [ "$loss_a" -le "$BACKUP_LOSS" ] && [ "$loss_b" -le "$BACKUP_LOSS" ] && \
          [ "$latest_a" -eq 1 ] && [ "$latest_b" -eq 1 ]; then acceptable=1; fi
      if [ "$loss_a" -le "$RECOVERY_LOSS" ] && [ "$loss_b" -le "$RECOVERY_LOSS" ] && \
          [ "$latest_a" -eq 1 ] && [ "$latest_b" -eq 1 ]; then recoverable=1; fi
      if [ "$loss_a" -ge "$DEGRADED_LOSS" ] && [ "$loss_b" -ge "$DEGRADED_LOSS" ]; then
        bad=1; reason=degraded
      fi
    fi
    if [ "$latest_a" -eq 0 ] && [ "$latest_b" -eq 0 ]; then
      down_since=$(read_number "$STATE_DIR/down_wan$classify_unit" "$NOW")
      echo "$down_since" > "$STATE_DIR/down_wan$classify_unit"
      if [ "$((NOW - down_since))" -ge "$HARD_DOWN_SECONDS" ]; then bad=1; reason=unreachable; fi
    else
      rm -f "$STATE_DIR/down_wan$classify_unit"
    fi
  fi
  if [ "$reason" = unknown ] || [ "$reason" = disabled ]; then
    rm -f "$STATE_DIR/down_wan$classify_unit"
  fi
  # Recovery only accrues while failed over. Even one new lost/unknown probe resets it.
  recovered=0
  if [ -f "$STATE_DIR/active_wan$classify_unit" ] && [ "$recoverable" -eq 1 ]; then
    recovery_since=$(read_number "$STATE_DIR/recovery_wan$classify_unit" "$NOW")
    echo "$recovery_since" > "$STATE_DIR/recovery_wan$classify_unit"
    [ "$((NOW - recovery_since))" -lt "$RECOVERY_SECONDS" ] || recovered=1
  else
    rm -f "$STATE_DIR/recovery_wan$classify_unit"
  fi
  echo "$bad $acceptable $recovered $reason $loss_a $loss_b $samples_a $samples_b" > "$STATE_DIR/health_wan$classify_unit"
}
health_summary() {
  set -- $(cat "$STATE_DIR/health_wan$1")
  if [ "$4" = disabled ] || [ "$4" = unknown ]; then
    printf '%s loss=unavailable samples=%s/%s' "$4" "$7" "$8"
  else
    printf '%s loss=%s%%/%s%% samples=%s/%s' "$4" "$5" "$6" "$7" "$8"
  fi
}
transition_log() {
  message_unit=$1
  message_text=$2
  previous_message=$(cat "$STATE_DIR/decision_wan$message_unit" 2>/dev/null)
  if [ "$previous_message" != "$message_text" ]; then
    log "WAN$message_unit $message_text; $(health_summary "$message_unit"); other WAN $(health_summary "$((1-message_unit))")"
    printf '%s\n' "$message_text" > "$STATE_DIR/decision_wan$message_unit"
  fi
}
handle_wan() {
  handle_unit=$1
  other_unit=$((1-handle_unit))
  set -- $(cat "$STATE_DIR/health_wan$handle_unit")
  home_bad=$1; home_recovered=$3
  set -- $(cat "$STATE_DIR/health_wan$other_unit")
  other_acceptable=$2
  if [ -f "$STATE_DIR/active_wan$handle_unit" ]; then
    if [ "$home_recovered" -eq 1 ]; then
      if ensure_unit_rules "$handle_unit" "wan$handle_unit"; then
        rm -f "$STATE_DIR/active_wan$handle_unit" "$STATE_DIR/recovery_wan$handle_unit"
        transition_log "$handle_unit" "recovered for ${RECOVERY_SECONDS}s; restored preferred pins"
      else transition_log "$handle_unit" "restoration failed; will retry"; fi
    elif [ "$other_acceptable" -eq 1 ]; then
      if ensure_unit_rules "$handle_unit" "wan$other_unit"; then
        transition_log "$handle_unit" "keeping pins on WAN$other_unit; awaiting sustained recovery"
      else transition_log "$handle_unit" "failed to maintain backup rules; will retry"; fi
    else
      transition_log "$handle_unit" "backup unhealthy or unmeasured; keeping existing routes"
    fi
  elif [ "$home_bad" -eq 1 ]; then
    if [ "$other_acceptable" -eq 1 ]; then
      # Set marker first, allowing partial routing errors to be retried safely.
      echo 1 > "$STATE_DIR/active_wan$handle_unit"
      rm -f "$STATE_DIR/recovery_wan$handle_unit"
      if ensure_unit_rules "$handle_unit" "wan$other_unit"; then
        transition_log "$handle_unit" "failed over preferred pins to WAN$other_unit"
      else transition_log "$handle_unit" "failover partially failed; will retry"; fi
    else
      transition_log "$handle_unit" "unhealthy; backup unhealthy or unmeasured; keeping existing routes"
    fi
  else
    # A recovered preferred WAN can also repair rules Merlin removed at boot/restart.
    set -- $(cat "$STATE_DIR/health_wan$handle_unit")
    if [ "$2" -eq 1 ]; then
      ensure_unit_rules "$handle_unit" "wan$handle_unit" || log "WAN$handle_unit preferred rule repair failed"
    fi
    transition_log "$handle_unit" "monitoring preferred WAN"
  fi
}
write_status() {
  {
    echo "uptime_seconds=$NOW mode=$FAILOVER_MODE targets=$TARGET_A,$TARGET_B"
    echo "rule_priority=$FAILOVER_PRIORITY ownership=$OWNERSHIP_DIR whole_failed=$(read_number "$OWNERSHIP_DIR/whole-failover" none)"
    if [ "$DDNS_HELPER_LOADED" = 1 ]; then ddns_status; fi
    for status_unit in 0 1; do
      echo "WAN$status_unit $(health_summary "$status_unit") failed_over=$([ -f "$STATE_DIR/active_wan$status_unit" ] && echo yes || echo no) recovery_since=$(read_number "$STATE_DIR/recovery_wan$status_unit" none)"
    done
  } > "$STATE_DIR/status.new"
  mv "$STATE_DIR/status.new" "$STATE_DIR/status"
}
watchdog_main() {
  umask 077
  mkdir -p "$STATE_DIR"
  exec 9>"$STATE_DIR/lock"
  if ! flock -n 9; then
    log "Watchdog already running"
    [ "${1:-}" != --cleanup ]; return $?
  fi
  case "$FAILOVER_MODE" in whole_lan|pinned) ;; *) log "Invalid FAILOVER_MODE=$FAILOVER_MODE"; return 1 ;; esac
  case "$FAILOVER_PRIORITY" in ''|*[!0-9]*) return 1 ;; esac
  [ "$FAILOVER_PRIORITY" -ge 3 ] && [ "$FAILOVER_PRIORITY" -lt 100 ] || return 1
  for configured_table in "$FAILOVER_TABLE" "$PROBE_TABLE0" "$PROBE_TABLE1"; do
    case "$configured_table" in ''|*[!0-9]*) return 1 ;; esac
    [ "$configured_table" -ge 1000 ] || return 1
  done
  [ "$FAILOVER_TABLE" != "$PROBE_TABLE0" ] && [ "$FAILOVER_TABLE" != "$PROBE_TABLE1" ] && [ "$PROBE_TABLE0" != "$PROBE_TABLE1" ] || return 1
  if [ "${1:-}" = --cleanup ]; then cleanup_owned; return $?; fi
  routing_init || {
    [ ! -f "$OWNED_RULES" ] || remove_rule_set all
    log "Routing namespace conflict; no foreign rules/tables changed"
    return 1
  }
  echo $$ > "$PID_FILE"
  trap 'rm -f "$PID_FILE"' EXIT
  trap 'exit 0' TERM INT
  # Keep active failover markers during a watchdog restart; discard measurements.
  rm -f "$STATE_DIR"/samples_* "$STATE_DIR"/recovery_wan* "$STATE_DIR"/down_wan* \
    "$STATE_DIR"/decision_wan* "$STATE_DIR"/config_wan* "$STATE_DIR"/ddns-recovery
  previous_mode=$(cat "$OWNERSHIP_DIR/mode" 2>/dev/null)
  if [ "$previous_mode" != "$FAILOVER_MODE" ] && [ -n "$previous_mode" ]; then
    remove_overlay || return 1
    rm -f "$OWNERSHIP_DIR/whole-failover"
  fi
  echo "$FAILOVER_MODE" > "$OWNERSHIP_DIR/mode"
  # Restore persistent whole-LAN state; its recovery timer still starts afresh.
  startup_failed=$(read_number "$OWNERSHIP_DIR/whole-failover" none)
  case "$startup_failed" in 0|1) echo 1 > "$STATE_DIR/active_wan$startup_failed" ;; esac
  # Recover markers from v2/pinned rules if /tmp state was lost or cleared.
  for startup_unit in 0 1; do
    for startup_ip in $(configured_ips_for_unit "$startup_unit"); do
      if [ "$(current_target_for_ip "$startup_ip")" = "wan$((1-startup_unit))" ]; then
        echo 1 > "$STATE_DIR/active_wan$startup_unit"
      fi
    done
  done
  if [ "$FAILOVER_MODE" = whole_lan ] && [ "$startup_failed" = none ]; then
    if [ -f "$STATE_DIR/active_wan0" ] && [ ! -f "$STATE_DIR/active_wan1" ]; then
      echo 0 > "$OWNERSHIP_DIR/whole-failover"
      echo 1 > "$STATE_DIR/migrate-pins"
    elif [ -f "$STATE_DIR/active_wan1" ] && [ ! -f "$STATE_DIR/active_wan0" ]; then
      echo 1 > "$OWNERSHIP_DIR/whole-failover"
      echo 1 > "$STATE_DIR/migrate-pins"
    fi
  fi
  log "Loss-aware watchdog v3.1 started; mode=$FAILOVER_MODE priority=$FAILOVER_PRIORITY; window=${WINDOW_SECONDS}s fail=${DEGRADED_LOSS}% backup<=${BACKUP_LOSS}% recovery<=${RECOVERY_LOSS}%/${RECOVERY_SECONDS}s"
  while :; do
    cycle_start=$(now_seconds)
    if ! namespace_safe; then
      remove_rule_set all || log "Could not withdraw all owned rules"
      log "Routing namespace conflict; withdrew owned rules and left foreign rules intact"
      rm -f "$PID_FILE"
      return 1
    fi
    select_probe_targets || { log "No two probe addresses are distinct from DNS servers"; return 1; }
    prepare_probe_routes || log "Probe route repair failed; measurements may be unknown"
    prepare_probes
    # Timestamp dispatch, not variable completion times; keeps window spans stable.
    NOW=$cycle_start
    for sample_unit in 0 1; do
      for sample_target in a b; do
        record_sample "$STATE_DIR/samples_${sample_unit}_$sample_target" "$(read_number "$STATE_DIR/probe_${sample_unit}_$sample_target" -1)"
      done
      classify_wan "$sample_unit"
    done
    # Both decisions use the same measurements, taken before any route changes.
    if [ "$FAILOVER_MODE" = whole_lan ]; then
      whole_lan_decision
    else
      handle_wan 0
      handle_wan 1
      # Pinned mode retains the legacy client policy, with shared DNS failover.
      pinned_dns_good=none
      if [ -f "$STATE_DIR/active_wan0" ] && [ "$(awk '{print $2}' "$STATE_DIR/health_wan1")" = 1 ]; then pinned_dns_good=1
      elif [ -f "$STATE_DIR/active_wan1" ] && [ "$(awk '{print $2}' "$STATE_DIR/health_wan0")" = 1 ]; then pinned_dns_good=0; fi
      case "$pinned_dns_good" in 0|1) apply_overlay "$pinned_dns_good" dns_only || log "Pinned-mode DNS override failed" ;;
        *) if [ ! -f "$STATE_DIR/active_wan0" ] && [ ! -f "$STATE_DIR/active_wan1" ]; then remove_overlay || log "DNS override cleanup failed"; fi ;;
      esac
    fi
    if [ "$DDNS_HELPER_LOADED" = 1 ]; then ddns_tick; fi
    write_status
    cycle_elapsed=$(( $(now_seconds) - cycle_start ))
    cycle_sleep=$((CHECK_INTERVAL - cycle_elapsed))
    [ "$cycle_sleep" -le 0 ] || sleep "$cycle_sleep"
  done
}

# Test harness sources functions without starting a daemon or changing routes.
[ "${WAN_WATCHDOG_LIBRARY_ONLY:-0}" = 1 ] || watchdog_main "$@"
