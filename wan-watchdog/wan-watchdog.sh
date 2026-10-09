#!/bin/sh
# Loss-aware watchdog for GUI-configured, source-IP Dual-WAN pins.
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
    probe_config="$probe_enabled:$probe_iface:$probe_ip:$probe_gateway"
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
    echo "uptime_seconds=$NOW targets=$TARGET_A,$TARGET_B"
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
  flock -n 9 || { log "Watchdog already running"; return 0; }
  echo $$ > "$PID_FILE"
  trap 'rm -f "$PID_FILE"; exit 0' TERM INT
  # Keep active failover markers during a watchdog restart; discard measurements.
  rm -f "$STATE_DIR"/samples_* "$STATE_DIR"/recovery_wan* "$STATE_DIR"/down_wan* \
    "$STATE_DIR"/decision_wan* "$STATE_DIR"/config_wan*
  # Recover markers from policy rules if /tmp state was lost or cleared.
  for startup_unit in 0 1; do
    for startup_ip in $(configured_ips_for_unit "$startup_unit"); do
      if [ "$(current_target_for_ip "$startup_ip")" = "wan$((1-startup_unit))" ]; then
        echo 1 > "$STATE_DIR/active_wan$startup_unit"
      fi
    done
  done
  log "Loss-aware watchdog v2 started; window=${WINDOW_SECONDS}s fail=${DEGRADED_LOSS}% backup<=${BACKUP_LOSS}% recovery<=${RECOVERY_LOSS}%/${RECOVERY_SECONDS}s"
  while :; do
    cycle_start=$(now_seconds)
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
    handle_wan 0
    handle_wan 1
    write_status
    cycle_elapsed=$(( $(now_seconds) - cycle_start ))
    cycle_sleep=$((CHECK_INTERVAL - cycle_elapsed))
    [ "$cycle_sleep" -le 0 ] || sleep "$cycle_sleep"
  done
}

# Test harness sources functions without starting a daemon or changing routes.
[ "${WAN_WATCHDOG_LIBRARY_ONLY:-0}" = 1 ] || watchdog_main
