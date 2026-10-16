#!/bin/sh
# Supply the watchdog's Auto selection to Merlin's firmware Inadyn updater.
# Never extract credentials or change ddns_wan_unit, wan_primary, or WAN services.
STATE_DIR=${STATE_DIR:-/tmp/wan-watchdog}
OWNERSHIP_DIR=${OWNERSHIP_DIR:-/jffs/wan-watchdog}
DDNS_HELPER=${DDNS_HELPER:-/jffs/scripts/wan-watchdog-ddns}
DDNS_POSTCONF=${DDNS_POSTCONF:-/jffs/scripts/inadyn.postconf}
DDNS_CACHE_DIR=${DDNS_CACHE_DIR:-/var/cache/inadyn}
DDNS_CUSTOM_CONFIG_DIR=${DDNS_CUSTOM_CONFIG_DIR:-/jffs/configs}
DDNS_RETRY_SECONDS=${DDNS_RETRY_SECONDS:-60}
DDNS_RECOVERY_SECONDS=${DDNS_RECOVERY_SECONDS:-120}
DDNS_LOOKUP_SECONDS=${DDNS_LOOKUP_SECONDS:-8}

ddns_log() { logger -t wan-watchdog-ddns "$*"; }
ddns_clock() { awk '{print int($1)}' /proc/uptime; }
ddns_lookup() {
  # This firmware has neither timeout nor the command builtin.
  # Bound nslookup using child PIDs, with no new package dependency.
  ddns_lookup_file="$STATE_DIR/ddns-lookup-$$"
  nslookup "$1" "$2" > "$ddns_lookup_file" 2>/dev/null &
  ddns_lookup_pid=$!
  ( exec 8>&-; sleep "$DDNS_LOOKUP_SECONDS"; kill "$ddns_lookup_pid" 2>/dev/null ) >/dev/null 2>&1 &
  ddns_lookup_timer=$!
  wait "$ddns_lookup_pid" 2>/dev/null
  ddns_lookup_result=$?
  kill "$ddns_lookup_timer" 2>/dev/null
  wait "$ddns_lookup_timer" 2>/dev/null
  cat "$ddns_lookup_file"
  rm -f "$ddns_lookup_file"
  return "$ddns_lookup_result"
}
ddns_read() { if [ -f "$1" ]; then cat "$1"; else printf '%s\n' "$2"; fi; }
ddns_public_ip() {
  printf '%s\n' "$1" | awk -F. '
    NF!=4 {exit 1}
    {for(i=1;i<=4;i++) if($i !~ /^[0-9]+$/ || $i>255 || (length($i)>1 && substr($i,1,1)=="0")) exit 1}
    $1==0 || $1==10 || $1==127 || $1>=224 {exit 1}
    $1==100 && $2>=64 && $2<=127 {exit 1}
    $1==169 && $2==254 {exit 1}
    $1==172 && $2>=16 && $2<=31 {exit 1}
    $1==192 && ($2==168 || ($2==0 && ($3==0 || $3==2))) {exit 1}
    $1==198 && ($2==18 || $2==19 || ($2==51 && $3==100)) {exit 1}
    $1==203 && $2==0 && $3==113 {exit 1}
    $1==255 {exit 1}
  '
}
# Endpoints from Merlin's provider implementations. Unknown providers stay firmware.
ddns_api_host() {
  case "$(nvram get ddns_server_x)" in
    WWW.NAMECHEAP.COM) echo dynamicdns.park-your-domain.com ;;
    WWW.NO-IP.COM) echo dynupdate.no-ip.com ;;
    WWW.DYNDNS.ORG*) echo members.dyndns.org ;;
    CLOUDFLARE.COM) echo api.cloudflare.com ;;
    WWW.ZONEEDIT.COM) echo dynamic.zoneedit.com ;;
    WWW.EASYDNS.COM) echo api.cp.easydns.com ;;
    WWW.DNSOMATIC.COM) echo updates.dnsomatic.com ;;
    WWW.SELFHOST.DE) echo carol.selfhost.de ;;
    FREEDNS.AFRAID.ORG) echo freedns.afraid.org ;;
    FREEMYIP.COM) echo freemyip.com ;;
    DNSPOD.CN) echo dnsapi.cn ;;
    WWW.ASUS.COM) [ "$(nvram get ddns_replace_status)" != 1 ] && echo ns1.asuscomm.com ;;
    WWW.ASUS.COM.CN) [ "$(nvram get ddns_replace_status)" != 1 ] && echo ns1.asuscomm.cn ;;
    *) return 1 ;;
  esac
}
# Only the main daemon changes routing/ownership. Workers only resolve DNS.
ddns_routes_clear() {
  [ -f "$OWNED_RULES" ] || return 0
  cp "$OWNED_RULES" "$STATE_DIR/ddns-old-rules"
  while IFS= read -r ddns_record; do
    case "$ddns_record" in
      "$PROBE_PRIORITY from all to "*" iif lo lookup $PROBE_TABLE0"|"$PROBE_PRIORITY from all to "*" iif lo lookup $PROBE_TABLE1")
        owned_rule_remove "$ddns_record" || return 1 ;;
    esac
  done < "$STATE_DIR/ddns-old-rules"
}
ddns_routes() {
  ddns_host=$(ddns_api_host) || return 1
  ddns_resolved_at=$(ddns_read "$STATE_DIR/ddns-api-at" 0)
  if [ "$(ddns_read "$STATE_DIR/ddns-api-host" '')" != "$ddns_host" ] ||
     [ "$((NOW-ddns_resolved_at))" -ge 60 ]; then
    ddns_resolve_last=$(ddns_read "$STATE_DIR/ddns-resolve-last" 0)
    if [ "$((NOW-ddns_resolve_last))" -ge 10 ]; then
      ddns_atomic "$STATE_DIR/ddns-resolve-last" "$NOW"
      ( ( exec 9>&-; exec 8>"$STATE_DIR/ddns-resolve-lock"
        flock -n 8 || exit 0
        "$DDNS_HELPER" --resolve-api "$ddns_host"
      ) >/dev/null 2>&1 & )
    fi
  fi
  [ "$(ddns_read "$STATE_DIR/ddns-api-host" '')" = "$ddns_host" ] || return 1
  [ -s "$STATE_DIR/ddns-api-ips" ] || return 1
  ddns_route_table=$PROBE_TABLE0; [ "$1" = 0 ] || ddns_route_table=$PROBE_TABLE1
  : > "$STATE_DIR/ddns-wanted-rules"
  for ddns_api_ip in $(cat "$STATE_DIR/ddns-api-ips"); do
    ddns_public_ip "$ddns_api_ip" || return 1
    [ "$ddns_api_ip" != "$TARGET_A" ] && [ "$ddns_api_ip" != "$TARGET_B" ] || return 1
    ddns_api_record=$(rule_record "$PROBE_PRIORITY from all to $ddns_api_ip/32 iif lo lookup $ddns_route_table")
    owned_rule_add $ddns_api_record || return 1
    echo "$ddns_api_record" >> "$STATE_DIR/ddns-wanted-rules"
  done
  cp "$OWNED_RULES" "$STATE_DIR/ddns-old-rules"
  while IFS= read -r ddns_record; do
    case "$ddns_record" in
      "$PROBE_PRIORITY from all to "*" iif lo lookup $PROBE_TABLE0"|"$PROBE_PRIORITY from all to "*" iif lo lookup $PROBE_TABLE1")
        grep -qxF -e "$ddns_record" "$STATE_DIR/ddns-wanted-rules" || owned_rule_remove "$ddns_record" || return 1 ;;
    esac
  done < "$STATE_DIR/ddns-old-rules"
}
ddns_resolve_api() {
  [ "$1" = "$(ddns_api_host)" ] || return 1
  # Bounded, outside the probe loop; query only the router's local resolver.
  ddns_lookup "$1" 127.0.0.1 | awk '
    /^Name:/ {answer=1; next}
    answer && /^Address([[:space:]]+[0-9]+)?:/ {
      for(i=2;i<=NF;i++) if($i ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/) print $i
    }' | sort -u > "$STATE_DIR/ddns-api-ips.new"
  [ -s "$STATE_DIR/ddns-api-ips.new" ] || { rm -f "$STATE_DIR/ddns-api-ips.new"; return 1; }
  mv "$STATE_DIR/ddns-api-ips.new" "$STATE_DIR/ddns-api-ips"
  ddns_atomic "$STATE_DIR/ddns-api-at" "$(ddns_clock)"
  ddns_atomic "$STATE_DIR/ddns-api-host" "$1"
}
ddns_supported() {
  [ "$(nvram get ddns_enable_x)" = 1 ] && [ "$(nvram get wans_mode)" = lb ] || return 1
  # API routing is IPv4-only too; an active IPv6 path is outside this release.
  case "$(nvram get ipv6_service)" in ''|disabled) ;; *) return 1 ;; esac
  ddns_api_host >/dev/null || return 1
  [ ! -f "$DDNS_CUSTOM_CONFIG_DIR/inadyn.conf" ] && [ ! -f "$DDNS_CUSTOM_CONFIG_DIR/inadyn.conf.add" ] || return 1
  [ -x "$DDNS_HELPER" ] || return 1
  # Do not launch updates when postconf integration is missing.
  grep -q '^# wan-watchdog DDNS Auto integration$' "$DDNS_POSTCONF" 2>/dev/null
}
ddns_unit_ip() {
  case "$1" in 0|1) ;; *) return 1 ;; esac
  ddns_ip=$(nvram get wan${1}_ipaddr)
  ddns_public_ip "$ddns_ip" || return 1
  printf '%s\n' "$ddns_ip"
}
ddns_unit_ready() {
  [ "$(nvram get wan${1}_enable)" = 1 ] && [ "$(nvram get wan${1}_state_t)" = 2 ] || return 1
  ddns_unit_ip "$1" >/dev/null
}
ddns_atomic() {
  printf '%s\n' "$2" > "$1.new" && mv "$1.new" "$1"
}
ddns_report() {
  [ "$(ddns_read "$STATE_DIR/ddns-status" '')" = "$1" ] && return 0
  ddns_atomic "$STATE_DIR/ddns-status" "$1"
  ddns_log "$1"
}
ddns_live() {
  # Persisted Auto selection is useful across restarts, but authorizes no
  # redirection without a live routing owner and recent main-loop progress.
  [ -s "$STATE_DIR/ddns-status" ] || return 1
  ddns_at=$(ddns_read "$STATE_DIR/ddns-status-at" '')
  case "$ddns_at" in ''|*[!0-9]*) return 1 ;; esac
  ddns_age=$(($(ddns_clock)-ddns_at))
  [ "$ddns_age" -ge 0 ] && [ "$ddns_age" -le 30 ] || return 1
  [ -f "$STATE_DIR/lock" ] && ! flock -n "$STATE_DIR/lock" true
}
ddns_address() {
  [ "$(nvram get ddns_wan_unit)" = -1 ] && ddns_supported && ddns_live || return 1
  ddns_unit_ip "$(ddns_read "$OWNERSHIP_DIR/ddns-auto-unit" none)"
}
ddns_status() { printf 'DDNS %s\n' "$(ddns_read "$STATE_DIR/ddns-status" 'helper installed; awaiting measurements')"; }
ddns_firmware_success() {
  [ "$(nvram get ddns_updated)" = 1 ] && [ "$(nvram get ddns_ipaddr)" = "$1" ] &&
  [ "$(nvram get ddns_server_x_old)" = "$(nvram get ddns_server_x)" ] &&
  [ "$(nvram get ddns_hostname_old)" = "$(nvram get ddns_hostname_x)" ]
}

# Main watchdog calls this after classification; it never waits for an updater.
ddns_tick() {
  # Refresh even when the message is unchanged or update requests are throttled.
  ddns_atomic "$STATE_DIR/ddns-status-at" "$NOW" || return 1
  if ! ddns_supported; then
    ddns_routes_clear
    if [ -f "$OWNERSHIP_DIR/ddns-auto-unit" ]; then
      ddns_atomic "$STATE_DIR/ddns-firmware-pending" 1
      rm -f "$OWNERSHIP_DIR/ddns-auto-unit" "$STATE_DIR/ddns-recovery" "$STATE_DIR/ddns-requested"
      ddns_request firmware
    fi
    if [ -f "$STATE_DIR/ddns-firmware-pending" ]; then
      if [ "$(nvram get ddns_enable_x)" != 1 ] || { [ "$(ddns_read "$STATE_DIR/ddns-requested" '')" = firmware ] && [ "$(nvram get ddns_updated)" = 1 ]; }; then
        rm -f "$STATE_DIR/ddns-firmware-pending"
      else ddns_request firmware; fi
    fi
    ddns_report 'firmware selection (disabled, unsupported configuration, or missing postconf hook)'
    return
  fi
  case "$(nvram get ddns_wan_unit)" in
    0|1)
      ddns_routes_clear
      ddns_fixed=$(nvram get ddns_wan_unit)
      if [ -f "$OWNERSHIP_DIR/ddns-auto-unit" ]; then
        ddns_atomic "$STATE_DIR/ddns-firmware-pending" 1
        rm -f "$OWNERSHIP_DIR/ddns-auto-unit" "$STATE_DIR/ddns-recovery" "$STATE_DIR/ddns-requested"
        ddns_request firmware
      fi
      if [ -f "$STATE_DIR/ddns-firmware-pending" ]; then
        ddns_fixed_ip=$(ddns_unit_ip "$ddns_fixed" 2>/dev/null)
        if [ -n "$ddns_fixed_ip" ] && ddns_firmware_success "$ddns_fixed_ip"; then
          rm -f "$STATE_DIR/ddns-firmware-pending"
        else ddns_request firmware; fi
      fi
      ddns_report "UI fixed WAN$ddns_fixed; watchdog does not redirect DDNS"
      return ;;
    -1) rm -f "$STATE_DIR/ddns-firmware-pending" ;;
    *) ddns_report 'invalid UI interface selection; no DDNS action'; return ;;
  esac
  ddns_preferred=none
  for ddns_scan in 0 1; do
    if ddns_unit_ip "$ddns_scan" >/dev/null; then ddns_preferred=$ddns_scan; break; fi
  done
  if [ "$ddns_preferred" = none ]; then
    ddns_report 'Auto: no directly assigned public IPv4; keeping firmware DDNS behavior'
    return
  fi
  ddns_current=$(ddns_read "$OWNERSHIP_DIR/ddns-auto-unit" "$ddns_preferred")
  case "$ddns_current" in 0|1) ;; *) ddns_current=$ddns_preferred ;; esac
  ddns_wanted=$ddns_current
  set -- $(ddns_read "$STATE_DIR/health_wan$ddns_current" '0 0 0 unknown 100 100')
  ddns_bad=$1
  ddns_other=$((1-ddns_current))
  set -- $(ddns_read "$STATE_DIR/health_wan$ddns_other" '0 0 0 unknown 100 100')
  ddns_other_good=$2
  if { [ "$ddns_bad" = 1 ] || ! ddns_unit_ready "$ddns_current"; } &&
     [ "$ddns_other_good" = 1 ] && ddns_unit_ready "$ddns_other"; then
    ddns_wanted=$ddns_other
    rm -f "$STATE_DIR/ddns-recovery"
  elif [ "$ddns_current" != "$ddns_preferred" ]; then
    set -- $(ddns_read "$STATE_DIR/health_wan$ddns_preferred" '0 0 0 unknown 100 100')
    if [ "$2" = 1 ] && [ "$5" -le 5 ] && [ "$6" -le 5 ] && ddns_unit_ready "$ddns_preferred"; then
      ddns_since=$(ddns_read "$STATE_DIR/ddns-recovery" "$NOW")
      [ -f "$STATE_DIR/ddns-recovery" ] || ddns_atomic "$STATE_DIR/ddns-recovery" "$ddns_since"
      if [ "$((NOW-ddns_since))" -ge "$DDNS_RECOVERY_SECONDS" ]; then
        ddns_wanted=$ddns_preferred
        rm -f "$STATE_DIR/ddns-recovery"
      fi
    else rm -f "$STATE_DIR/ddns-recovery"; fi
  else rm -f "$STATE_DIR/ddns-recovery"; fi
  set -- $(ddns_read "$STATE_DIR/health_wan$ddns_wanted" '0 0 0 unknown 100 100')
  if [ "$2" != 1 ] || ! ddns_unit_ready "$ddns_wanted"; then
    ddns_report "Auto: no measured healthy public destination; retaining WAN$ddns_current selection"
    return
  fi
  if [ "$(ddns_read "$OWNERSHIP_DIR/ddns-auto-unit" none)" != "$ddns_wanted" ]; then
    ddns_atomic "$OWNERSHIP_DIR/ddns-auto-unit" "$ddns_wanted" || return 1
    rm -f "$STATE_DIR/ddns-verified"
  fi
  ddns_expected=$(ddns_unit_ip "$ddns_wanted") || return 1
  if ! ddns_routes "$ddns_wanted"; then
    ddns_report "Auto WAN$ddns_wanted; waiting for provider IPv4 route resolution"
    return
  fi
  ddns_signature="$ddns_wanted:$ddns_expected"
  if [ "$(ddns_read "$STATE_DIR/ddns-requested" '')" != "$ddns_signature" ] ||
     ! ddns_firmware_success "$ddns_expected"; then
    rm -f "$STATE_DIR/ddns-verified"
    ddns_request "$ddns_signature"
  else
    ddns_verify_signature="$ddns_signature:$(nvram get ddns_hostname_x):$(nvram get ddns_server_x)"
    if [ "$(ddns_read "$STATE_DIR/ddns-verified" '')" = "$ddns_verify_signature" ]; then
      ddns_report "Auto WAN$ddns_wanted $ddns_expected; firmware updater reports success; public DNS verified"
    else
      ddns_report "Auto WAN$ddns_wanted $ddns_expected; firmware updater reports success; public DNS verification pending"
      ddns_verify_last=$(ddns_read "$STATE_DIR/ddns-verify-last" 0)
      if [ "$((NOW-ddns_verify_last))" -ge 60 ]; then
        ddns_atomic "$STATE_DIR/ddns-verify-last" "$NOW"
        ( ( exec 9>&-; exec 8>"$STATE_DIR/ddns-verify-lock"
          flock -n 8 || exit 0
          "$DDNS_HELPER" --verify "$ddns_verify_signature"
        ) >/dev/null 2>&1 & )
      fi
    fi
  fi
}
ddns_request() {
  ddns_last=$(ddns_read "$STATE_DIR/ddns-last-attempt" 0)
  [ "$((NOW-ddns_last))" -ge "$DDNS_RETRY_SECONDS" ] || return 0
  ddns_atomic "$STATE_DIR/ddns-last-attempt" "$NOW"
  ddns_atomic "$STATE_DIR/ddns-requested" "$1"
  ddns_report "update requested ($1); waiting for provider success"
  # Subshell closes the daemon flock FD. Service/DNS work cannot delay probes.
  ( ( exec 9>&-; exec 8>"$STATE_DIR/ddns-update-lock"
    flock -n 8 || exit 0
    "$DDNS_HELPER" --update "$1"
  ) >/dev/null 2>&1 & )
}

ddns_postconf() {
  [ "$(nvram get ddns_wan_unit)" = -1 ] && ddns_supported || return 0
  ddns_live || return 0
  ddns_selected=$(ddns_read "$OWNERSHIP_DIR/ddns-auto-unit" none)
  ddns_selected_ip=$(ddns_unit_ip "$ddns_selected") || return 0
  ddns_iface=$(nvram get wan${ddns_selected}_ifname)
  # Prefer PPP's active interface when present.
  ddns_ppp=$(nvram get wan${ddns_selected}_pppoe_ifname)
  case "$(nvram get wan${ddns_selected}_proto)" in pppoe|pptp|l2tp) ddns_iface=$ddns_ppp ;; esac
  case "$ddns_iface" in ''|*[!a-zA-Z0-9_.:-]*) return 1 ;; esac
  [ -f "$1" ] || return 1
  # Support one generated provider block; refuse custom/multiple-provider configs.
  [ "$(grep -Ec '^[[:space:]]*(provider|custom)[[:space:]]' "$1")" = 1 ] || return 1
  case "$DDNS_HELPER" in *[!a-zA-Z0-9_./-]*) return 1 ;; esac
  umask 077
  ddns_rewritten=$(awk -v iface="$ddns_iface" -v helper="$DDNS_HELPER" '
    /^[[:space:]]*iface[[:space:]]*=/ {next}
    /^[[:space:]]*checkip-command[[:space:]]*=/ {next}
    /^[[:space:]]*(provider|custom)[[:space:]]/ {
      print; print "checkip-command = \047" helper " --address\047"; next
    }
    {print}
    END {print "iface = " iface}
  ' "$1") || return 1
  # Rewrite the existing firmware file; do not create another credentials file.
  printf '%s\n' "$ddns_rewritten" > "$1"
  ddns_result=$?
  unset ddns_rewritten
  return "$ddns_result"
}
ddns_update() {
  # Recheck the UI immediately before any external update. No WAN writes.
  if [ "$1" != firmware ]; then
    [ "$(nvram get ddns_wan_unit)" = -1 ] && ddns_supported || return 0
    ddns_selected=$(ddns_read "$OWNERSHIP_DIR/ddns-auto-unit" none)
    ddns_expected=$(ddns_unit_ip "$ddns_selected") || return 0
    [ "$1" = "$ddns_selected:$ddns_expected" ] || return 0
    # An already correct successful publication requires no provider call.
    if ddns_firmware_success "$ddns_expected"; then return 0; fi
  fi
  pidof inadyn >/dev/null 2>&1 && return 0
  [ "$(nvram get ddns_enable_x)" = 1 ] || return 0
  ddns_host=$(nvram get ddns_hostname_x)
  case "$ddns_host" in ''|*[!a-zA-Z0-9_.@-]*) return 1 ;; esac
  # The firmware pre-hook no-change check would otherwise skip regeneration.
  rm -f "$DDNS_CACHE_DIR/$ddns_host.cache"
  service restart_ddns
}

ddns_verify() {
  [ "$(nvram get ddns_wan_unit)" = -1 ] && ddns_supported || return 1
  ddns_selected=$(ddns_read "$OWNERSHIP_DIR/ddns-auto-unit" none)
  ddns_expected=$(ddns_unit_ip "$ddns_selected") || return 1
  ddns_host=$(nvram get ddns_hostname_x)
  ddns_provider=$(nvram get ddns_server_x)
  [ "$1" = "$ddns_selected:$ddns_expected:$ddns_host:$ddns_provider" ] || return 1
  if [ "$ddns_provider" = WWW.NAMECHEAP.COM ]; then
    ddns_domain=$(nvram get ddns_username_x)
    case "$ddns_host" in @) ddns_host=$ddns_domain ;;
      "$ddns_domain"|*."$ddns_domain") ;;
      *) ddns_host="$ddns_host.$ddns_domain" ;; esac
  fi
  case "$ddns_host" in ''|*[!a-zA-Z0-9_.-]*) return 1 ;; esac
  for ddns_resolver in 1.1.1.1 8.8.8.8; do
    if ddns_lookup "$ddns_host" "$ddns_resolver" | awk -v wanted="$ddns_expected" '
      /^Name:/ {answer=1; next}
      answer && /^Address([[:space:]]+[0-9]+)?:/ {for(i=2;i<=NF;i++) if($i==wanted) found=1}
      END {exit !found}'; then
      ddns_atomic "$STATE_DIR/ddns-verified" "$1"
      ddns_log "Public DNS confirms WAN$ddns_selected $ddns_expected"
      return 0
    fi
  done
  ddns_log 'Provider reported success; public DNS has not confirmed the selected address yet'
  return 1
}

if [ "${WAN_WATCHDOG_DDNS_LIBRARY_ONLY:-0}" != 1 ]; then
  case "${1:-}" in
    --postconf) ddns_postconf "$2" ;;
    --resolve-api) ddns_resolve_api "$2" ;;
    --address) ddns_address ;;
    --update) ddns_update "$2" ;;
    --verify) ddns_verify "$2" ;;
    *) exit 1 ;;
  esac
fi
