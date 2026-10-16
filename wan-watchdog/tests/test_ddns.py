"""DDNS Auto decisions and firmware postconf/update boundaries; all effects mocked."""
import os
from pathlib import Path
import shlex
import unittest
import test_wan_watchdog

HELPER = Path(__file__).resolve().parents[1] / 'wan-watchdog-ddns.sh'


class DDNSTests(unittest.TestCase):
    run_case_base = test_wan_watchdog.WatchdogTests.run_case

    def run_case(self, body):
        helper = os.environ.get('WAN_WATCHDOG_TEST_DDNS_HELPER', str(HELPER))
        self.run_case_base('''
WAN_WATCHDOG_DDNS_LIBRARY_ONLY=1
DDNS_HELPER=''' + shlex.quote(helper) + '''
. "$DDNS_HELPER"
unset WAN_WATCHDOG_DDNS_LIBRARY_ONLY
DDNS_CACHE_DIR="$STATE_DIR/cache"
DDNS_POSTCONF="$STATE_DIR/postconf"
mkdir -p "$STATE_DIR/nvram" "$DDNS_CACHE_DIR"
echo '# wan-watchdog DDNS Auto integration' > "$DDNS_POSTCONF"
nvset() { printf '%s\\n' "$2" > "$STATE_DIR/nvram/$1"; }
nvram() { [ "$1" = get ] || return 99; cat "$STATE_DIR/nvram/$2" 2>/dev/null || :; }
ddns_log() { echo "$*" >> "$STATE_DIR/events"; }
ddns_routes() { return 0; }
DDNS_CUSTOM_CONFIG_DIR="$STATE_DIR/custom"
mkdir -p "$DDNS_CUSTOM_CONFIG_DIR"
echo 100 > "$STATE_DIR/ddns-verify-last"
# Private owner lease: no real process or production routing lock is used.
ddns_clock() { echo "$NOW"; }
flock() { return 1; }
touch "$STATE_DIR/lock"
echo active > "$STATE_DIR/ddns-status"
echo "$NOW" > "$STATE_DIR/ddns-status-at"
ddns_request() { echo "$1" >> "$STATE_DIR/requests"; ddns_atomic "$STATE_DIR/ddns-requested" "$1"; }
nvset ddns_enable_x 1; nvset wans_mode lb; nvset ddns_wan_unit -1
nvset ddns_server_x WWW.NO-IP.COM; nvset ddns_server_x_old WWW.NO-IP.COM; nvset ddns_ipv6_update 0; nvset ipv6_service disabled
nvset wan0_enable 1; nvset wan1_enable 1
nvset wan0_state_t 2; nvset wan1_state_t 2
nvset wan0_ipaddr 45.1.1.1; nvset wan1_ipaddr 46.1.1.1
nvset wan0_ifname eth0; nvset wan1_ifname eth2
nvset wan0_proto dhcp; nvset wan1_proto dhcp
echo '0 1 0 usable 0 0 15 15' > "$STATE_DIR/health_wan0"
echo '0 1 0 usable 0 0 15 15' > "$STATE_DIR/health_wan1"
''' + body)

    def test_auto_prefers_first_public_healthy_wan(self):
        self.run_case('''
ddns_tick
assert_eq "$(cat "$OWNERSHIP_DIR/ddns-auto-unit")" 0
assert_eq "$(cat "$STATE_DIR/requests")" '0:45.1.1.1'
''')

    def test_soft_failure_and_emergency_reverse(self):
        self.run_case('''
echo '1 0 0 degraded 50 50' > "$STATE_DIR/health_wan0"
ddns_tick; assert_eq "$(cat "$OWNERSHIP_DIR/ddns-auto-unit")" 1
echo '0 1 0 usable 0 0' > "$STATE_DIR/health_wan0"
echo '1 0 0 degraded 50 50' > "$STATE_DIR/health_wan1"
ddns_tick; assert_eq "$(cat "$OWNERSHIP_DIR/ddns-auto-unit")" 0
''')

    def test_return_waits_120_seconds_and_resets_on_loss(self):
        self.run_case('''
echo 1 > "$OWNERSHIP_DIR/ddns-auto-unit"
ddns_tick
NOW=219; ddns_tick; assert_eq "$(cat "$OWNERSHIP_DIR/ddns-auto-unit")" 1
echo '0 1 0 usable 7 0' > "$STATE_DIR/health_wan0"
ddns_tick; assert_eq "$([ -f "$STATE_DIR/ddns-recovery" ] && echo yes || echo no)" no
echo '0 1 0 usable 0 0' > "$STATE_DIR/health_wan0"
NOW=220; ddns_tick
NOW=339; ddns_tick; assert_eq "$(cat "$OWNERSHIP_DIR/ddns-auto-unit")" 1
NOW=340; ddns_tick; assert_eq "$(cat "$OWNERSHIP_DIR/ddns-auto-unit")" 0
''')

    def test_unknown_or_both_bad_does_not_republish(self):
        self.run_case('''
echo 0 > "$OWNERSHIP_DIR/ddns-auto-unit"
echo '1 0 0 degraded 50 50' > "$STATE_DIR/health_wan0"
echo '0 0 0 unknown 0 0' > "$STATE_DIR/health_wan1"
ddns_tick; assert_eq "$([ -f "$STATE_DIR/requests" ] && echo yes || echo no)" no
echo '1 0 0 degraded 50 50' > "$STATE_DIR/health_wan1"
ddns_tick; assert_eq "$(cat "$OWNERSHIP_DIR/ddns-auto-unit")" 0
''')

    def test_fixed_ui_choices_never_failover(self):
        for unit in (0, 1):
            self.run_case(f'''
nvset ddns_wan_unit {unit}
echo '1 0 0 degraded 50 50' > "$STATE_DIR/health_wan{unit}"
ddns_tick
assert_eq "$([ -f "$STATE_DIR/requests" ] && echo yes || echo no)" no
assert_eq "$([ -f "$OWNERSHIP_DIR/ddns-auto-unit" ] && echo yes || echo no)" no
''')

    def test_ui_auto_to_fixed_withdraws_override_and_retries_firmware(self):
        self.run_case('''
echo 1 > "$OWNERSHIP_DIR/ddns-auto-unit"
nvset ddns_wan_unit 0
ddns_tick; ddns_tick
assert_eq "$([ -f "$OWNERSHIP_DIR/ddns-auto-unit" ] && echo yes || echo no)" no
assert_eq "$(wc -l < "$STATE_DIR/requests" | tr -d ' ')" 3
nvset ddns_updated 1; nvset ddns_ipaddr 45.1.1.1
ddns_tick
assert_eq "$([ -f "$STATE_DIR/ddns-firmware-pending" ] && echo yes || echo no)" no
''')

    def test_cgnat_private_and_disabled_backups_not_destinations(self):
        for address in ('192.168.12.133', '100.70.1.2', '10.0.0.2', '169.254.1.2'):
            self.run_case(f'''
echo '1 0 0 degraded 50 50' > "$STATE_DIR/health_wan0"
nvset wan1_ipaddr {address}
ddns_tick
assert_eq "$([ -f "$STATE_DIR/requests" ] && echo yes || echo no)" no
''')
        self.run_case('''
echo '1 0 0 degraded 50 50' > "$STATE_DIR/health_wan0"
nvset wan1_enable 0; ddns_tick
assert_eq "$([ -f "$STATE_DIR/requests" ] && echo yes || echo no)" no
''')

    def test_public_filter_rejects_reserved_and_malformed_addresses(self):
        self.run_case('''
for address in 127.0.0.1 0.0.0.0 224.1.1.1 198.18.0.1 192.0.2.1 203.0.113.1 198.51.100.1 01.2.3.4 999.1.1.1 1.2.3 abc; do
  if ddns_public_ip "$address"; then exit 1; fi
done
ddns_public_ip 45.1.1.1
''')

    def test_restart_preserves_selection_but_requires_new_recovery(self):
        self.run_case('''
echo 1 > "$OWNERSHIP_DIR/ddns-auto-unit"
rm -f "$STATE_DIR/ddns-recovery"
NOW=900; ddns_tick
assert_eq "$(cat "$OWNERSHIP_DIR/ddns-auto-unit")" 1
assert_eq "$(cat "$STATE_DIR/ddns-recovery")" 900
''')

    def test_ip_lease_change_requests_new_publication(self):
        self.run_case('''
ddns_tick
nvset ddns_updated 1; nvset ddns_ipaddr 45.1.1.1
ddns_tick; assert_eq "$(wc -l < "$STATE_DIR/requests" | tr -d ' ')" 1
nvset wan0_ipaddr 45.1.1.2
ddns_tick; assert_eq "$(tail -1 "$STATE_DIR/requests")" '0:45.1.1.2'
''')

    def test_disabled_ddns_and_unsupported_modes_do_not_update(self):
        for key, value in [('ddns_enable_x', '0'), ('wans_mode', 'fo'),
                           ('ipv6_service', '6in4'), ('ddns_server_x', 'CUSTOM'),
                           ('ddns_wan_unit', 'bogus')]:
            self.run_case(f'''
nvset {key} {value}; ddns_tick
assert_eq "$([ -f "$STATE_DIR/requests" ] && echo yes || echo no)" no
''')

    def test_missing_hook_does_not_start_override(self):
        self.run_case('''
rm "$DDNS_POSTCONF"; ddns_tick
assert_eq "$([ -f "$OWNERSHIP_DIR/ddns-auto-unit" ] && echo yes || echo no)" no
''')

    def test_postconf_preserves_provider_credentials_and_namecheap_fix(self):
        self.run_case('''
echo 1 > "$OWNERSHIP_DIR/ddns-auto-unit"
cat > "$STATE_DIR/config" <<'EOF'
custom namecheap {
hostname = example
username = 'dummy.example'
password = 'DUMMY TEST ONLY'
ddns-path = "/update?domain=%u&password=%p&host=%h&ip=%i"
checkip-command = 'getrealip.sh'
}
iterations = 1
iface = eth0
EOF
ddns_postconf "$STATE_DIR/config"
grep -q '^iface = eth2$' "$STATE_DIR/config" || exit 1
grep -q "DUMMY TEST ONLY" "$STATE_DIR/config" || exit 1
grep -q '&host=%h&ip=%i' "$STATE_DIR/config" || exit 1
grep -q -- '--address' "$STATE_DIR/config" || exit 1
assert_eq "$(grep -c checkip-command "$STATE_DIR/config")" 1
ddns_postconf "$STATE_DIR/config"
assert_eq "$(grep -c '^iface =' "$STATE_DIR/config")" 1
''')

    def test_postconf_is_noop_for_fixed_ui_and_multiple_provider_refused(self):
        self.run_case('''
echo 1 > "$OWNERSHIP_DIR/ddns-auto-unit"
printf 'provider a {\\n}\\niface = eth0\\n' > "$STATE_DIR/config"
cp "$STATE_DIR/config" "$STATE_DIR/before"
nvset ddns_wan_unit 0; ddns_postconf "$STATE_DIR/config"
cmp "$STATE_DIR/config" "$STATE_DIR/before" || exit 1
nvset ddns_wan_unit -1
printf 'provider b {\\n}\\n' >> "$STATE_DIR/config"
if ddns_postconf "$STATE_DIR/config"; then exit 1; fi
''')

    def test_firmware_update_rechecks_ui_and_invalidates_only_host_cache(self):
        self.run_case('''
echo 1 > "$OWNERSHIP_DIR/ddns-auto-unit"
nvset ddns_hostname_x example.test
pidof() { return 1; }
service() { echo "$*" >> "$STATE_DIR/services"; }
touch "$DDNS_CACHE_DIR/example.test.cache" "$DDNS_CACHE_DIR/unrelated.cache"
nvset ddns_wan_unit 0; ddns_update '1:46.1.1.1'
assert_eq "$([ -f "$STATE_DIR/services" ] && echo yes || echo no)" no
nvset ddns_wan_unit -1; ddns_update '1:46.1.1.1'
assert_eq "$(cat "$STATE_DIR/services")" restart_ddns
assert_eq "$([ -f "$DDNS_CACHE_DIR/example.test.cache" ] && echo yes || echo no)" no
assert_eq "$([ -f "$DDNS_CACHE_DIR/unrelated.cache" ] && echo yes || echo no)" yes
''')

    def test_updater_failure_is_not_reported_as_success(self):
        self.run_case('''
nvset ddns_updated 0; nvset ddns_ipaddr 45.1.1.1
ddns_tick
assert_eq "$(cat "$STATE_DIR/requests")" '0:45.1.1.1'
nvset ddns_updated 1; ddns_tick
grep -q 'firmware updater reports success' "$STATE_DIR/ddns-status" || exit 1
''')

    def test_provider_routes_switch_and_cleanup_preserve_probe_rules(self):
        self.run_case('''
WAN_WATCHDOG_DDNS_LIBRARY_ONLY=1; . "$DDNS_HELPER"; unset WAN_WATCHDOG_DDNS_LIBRARY_ONLY
canonical_rules() { cat "$STATE_DIR/kernel"; }
: > "$STATE_DIR/kernel"
ip() {
  op=$3; shift 4; pref=$1; shift
  record="$pref $*"
  case "$op" in
    add) echo "$record" >> "$STATE_DIR/kernel" ;;
    del) awk -v row="$record" '$0!=row' "$STATE_DIR/kernel" > "$STATE_DIR/new"; mv "$STATE_DIR/new" "$STATE_DIR/kernel" ;;
    *) exit 1 ;;
  esac
}
echo '86 from 45.1.1.1 to 8.8.8.8 lookup 20186' > "$OWNED_RULES"
cp "$OWNED_RULES" "$STATE_DIR/kernel"
echo dynupdate.no-ip.com > "$STATE_DIR/ddns-api-host"
echo 100 > "$STATE_DIR/ddns-api-at"
echo 47.1.1.1 > "$STATE_DIR/ddns-api-ips"
ddns_routes 1
grep -qxF '86 from all to 47.1.1.1 iif lo lookup 20187' "$OWNED_RULES" || exit 1
ddns_routes 0
grep -qxF '86 from all to 47.1.1.1 iif lo lookup 20186' "$OWNED_RULES" || exit 1
assert_eq "$(wc -l < "$OWNED_RULES" | tr -d ' ')" 2
ddns_routes_clear
assert_eq "$(cat "$OWNED_RULES")" '86 from 45.1.1.1 to 8.8.8.8 lookup 20186'
assert_eq "$(cat "$STATE_DIR/kernel")" "$(cat "$OWNED_RULES")"
''')

    def test_provider_route_failures_block_update_request(self):
        self.run_case('''
ddns_routes() { return 1; }
ddns_tick
assert_eq "$([ -f "$STATE_DIR/requests" ] && echo yes || echo no)" no
grep -q 'waiting for provider' "$STATE_DIR/ddns-status" || exit 1
''')

    def test_probe_repair_keeps_ddns_provider_rules(self):
        self.run_case('''
echo '86 from all to 47.1.1.1 iif lo lookup 20186' > "$OWNED_RULES"
echo '86 from 45.1.1.1 to 8.8.8.8 lookup 20186' >> "$OWNED_RULES"
setup_wan_table() { return 0; }
ip() { return 0; }
owned_rule_add() { return 0; }
owned_rule_remove() { echo "$*" >> "$STATE_DIR/removed"; }
prepare_probe_routes
assert_eq "$([ -f "$STATE_DIR/removed" ] && echo yes || echo no)" no
''')

    def test_api_resolution_excludes_dns_server_and_ipv6(self):
        self.run_case('''
ddns_lookup() {
  cat <<'EOF'
Server: 127.0.0.1
Address: 127.0.0.1#53
Name: dynupdate.no-ip.com
Address 1: 47.1.1.1
Address 2: 47.1.1.2
Address 3: 2001:db8::1
EOF
}
ddns_resolve_api dynupdate.no-ip.com
assert_eq "$(cat "$STATE_DIR/ddns-api-ips")" '47.1.1.1
47.1.1.2'
''')

    def test_unsupported_provider_and_custom_config_leave_firmware(self):
        self.run_case('''
nvset ddns_server_x WWW.ORAY.COM; ddns_tick
assert_eq "$([ -f "$STATE_DIR/requests" ] && echo yes || echo no)" no
nvset ddns_server_x WWW.NO-IP.COM
touch "$DDNS_CUSTOM_CONFIG_DIR/inadyn.conf.add"; ddns_tick
assert_eq "$([ -f "$STATE_DIR/requests" ] && echo yes || echo no)" no
''')

    def test_public_dns_verification_uses_namecheap_root_domain(self):
        self.run_case('''
echo 1 > "$OWNERSHIP_DIR/ddns-auto-unit"
nvset ddns_server_x WWW.NAMECHEAP.COM
nvset ddns_hostname_x @; nvset ddns_username_x example.test
ddns_lookup() {
  assert_eq "$1" example.test
  printf 'Server: resolver\\nAddress: 1.1.1.1\\nName: example.test\\nAddress 1: 46.1.1.1\\n'
}
ddns_verify '1:46.1.1.1:@:WWW.NAMECHEAP.COM'
assert_eq "$(cat "$STATE_DIR/ddns-verified")" '1:46.1.1.1:@:WWW.NAMECHEAP.COM'
''')

    def test_dns_timeout_or_wrong_answer_does_not_confirm_update(self):
        self.run_case('''
echo 0 > "$OWNERSHIP_DIR/ddns-auto-unit"
nvset ddns_hostname_x example.test
ddns_lookup() { printf 'Name: example.test\\nAddress 1: 46.1.1.1\\n'; }
if ddns_verify '0:45.1.1.1:example.test:WWW.NO-IP.COM'; then exit 1; fi
assert_eq "$([ -f "$STATE_DIR/ddns-verified" ] && echo yes || echo no)" no
ddns_lookup() { return 124; }
if ddns_verify '0:45.1.1.1:example.test:WWW.NO-IP.COM'; then exit 1; fi
''')

    def test_previous_dns_confirmation_cannot_verify_a_return_transition(self):
        self.run_case('''
echo 1 > "$OWNERSHIP_DIR/ddns-auto-unit"
echo '0:45.1.1.1:example.test:WWW.NO-IP.COM' > "$STATE_DIR/ddns-verified"
echo '1 0 0 degraded 50 50' > "$STATE_DIR/health_wan1"
ddns_tick
assert_eq "$(cat "$OWNERSHIP_DIR/ddns-auto-unit")" 0
assert_eq "$([ -f "$STATE_DIR/ddns-verified" ] && echo yes || echo no)" no
''')

    def test_provider_or_hostname_change_invalidates_old_success(self):
        self.run_case('''
nvset ddns_updated 1; nvset ddns_ipaddr 45.1.1.1
nvset ddns_server_x_old WWW.NAMECHEAP.COM
ddns_tick
assert_eq "$(cat "$STATE_DIR/requests")" '0:45.1.1.1'
nvset ddns_server_x_old WWW.NO-IP.COM
nvset ddns_hostname_x new.example.test
if ddns_firmware_success 45.1.1.1; then exit 1; fi
''')

    def test_update_requests_are_rate_limited_and_do_not_hold_probe_lock(self):
        self.run_case('''
WAN_WATCHDOG_DDNS_LIBRARY_ONLY=1; . "$DDNS_HELPER"; unset WAN_WATCHDOG_DDNS_LIBRARY_ONLY
DDNS_HELPER="$STATE_DIR/mock-updater"
cat > "$DDNS_HELPER" <<'EOF'
#!/bin/sh
echo "$*" >> "$STATE_DIR/worker-calls"
EOF
chmod 755 "$DDNS_HELPER"
flock() { return 0; }
ddns_request '1:46.1.1.1'
i=0;while [ ! -f "$STATE_DIR/worker-calls" ] && [ "$i" -lt 3 ];do sleep 1;i=$((i+1));done
NOW=110; ddns_request '1:46.1.1.1'
NOW=160; ddns_request '1:46.1.1.1'
i=0;while [ "$(wc -l < "$STATE_DIR/worker-calls" | tr -d ' ')" -lt 2 ] && [ "$i" -lt 3 ];do sleep 1;i=$((i+1));done
assert_eq "$(jobs -p)" ''
assert_eq "$(wc -l < "$STATE_DIR/worker-calls" | tr -d ' ')" 2
''')

    def test_stale_selection_requires_live_lock_and_recent_progress(self):
        self.run_case('''
echo 1 > "$OWNERSHIP_DIR/ddns-auto-unit"
printf 'provider a {\\n}\\niface = eth0\\n' > "$STATE_DIR/config"
cp "$STATE_DIR/config" "$STATE_DIR/before"
assert_eq "$(ddns_address)" 46.1.1.1
NOW=131
if ddns_address;then exit 1;fi
ddns_postconf "$STATE_DIR/config";cmp "$STATE_DIR/config" "$STATE_DIR/before" || exit 1
NOW=100;flock() { return 0; }
if ddns_address;then exit 1;fi
ddns_postconf "$STATE_DIR/config";cmp "$STATE_DIR/config" "$STATE_DIR/before" || exit 1
flock() { return 1; };NOW=99
if ddns_address;then exit 1;fi
NOW=105;ddns_tick
assert_eq "$(cat "$STATE_DIR/ddns-status-at")" 105
assert_eq "$(ddns_address)" 46.1.1.1
''')

    def test_main_status_includes_ddns_without_command_builtin(self):
        self.run_case('''
DDNS_HELPER_LOADED=1
nvset ddns_wan_unit 0
ddns_tick
command() { return 127; }
write_status
grep -q '^DDNS UI fixed WAN0' "$STATE_DIR/status" || exit 1
''')

    def test_dns_lookup_is_bounded_without_timeout_utility(self):
        self.run_case('''
mkdir -p "$STATE_DIR/bin"
cat > "$STATE_DIR/bin/nslookup" <<'EOF'
#!/bin/sh
exec sleep 20
EOF
chmod 755 "$STATE_DIR/bin/nslookup"
PATH="$STATE_DIR/bin:$PATH"
DDNS_LOOKUP_SECONDS=1
before=$(date +%s)
if ddns_lookup example.test 127.0.0.1; then exit 1; fi
after=$(date +%s)
[ "$((after-before))" -le 5 ] || exit 1
''')

    def test_fast_dns_lookup_does_not_wait_for_timeout_child(self):
        self.run_case('''
nslookup() { echo 'Name: example.test'; echo 'Address 1: 45.1.1.1'; }
DDNS_LOOKUP_SECONDS=5
before=$(date +%s)
ddns_lookup example.test 127.0.0.1 | awk '/45.1.1.1/ {found=1} END {exit !found}' || exit 1
after=$(date +%s)
[ "$((after-before))" -le 2 ] || exit 1
''')


if __name__ == '__main__':
    unittest.main()
