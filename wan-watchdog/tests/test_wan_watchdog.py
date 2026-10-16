"""State-machine tests with synthetic probes, clock and routing (no live changes)."""
import os
from pathlib import Path
import subprocess
import shlex
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'wan-watchdog-legacy.sh'

class WatchdogTests(unittest.TestCase):
    def run_case(self, body):
        with tempfile.TemporaryDirectory() as state:
            env = dict(os.environ, STATE_DIR=state, WAN_WATCHDOG_LIBRARY_ONLY='1', WATCHDOG_SOURCE=str(SCRIPT), OWNERSHIP_DIR=state+'/ownership')
            shell = '''
. "$WATCHDOG_SOURCE"
mkdir -p "$OWNERSHIP_DIR"
touch "$OWNED_RULES" "$OWNED_TABLES"
log() { echo "$*" >> "$STATE_DIR/events"; }
ensure_unit_rules() { echo "$1 $2" >> "$STATE_DIR/moves"; return 0; }
assert_eq() { [ "$1" = "$2" ] || { echo "expected $2, got $1" >&2; exit 1; }; }
make_window() {
  # $1 unit, $2 target-a loss count, $3 target-b loss count
  echo 1 > "$STATE_DIR/enabled_wan$1"
  echo "$((NOW-30))" > "$STATE_DIR/window_start_wan$1"
  for t in a b; do
    case "$t" in a) losses=$2;; b) losses=$3;; esac
    : > "$STATE_DIR/samples_${1}_$t"
    i=0
    while [ "$i" -lt 15 ]; do
      value=1; [ "$i" -ge "$losses" ] || value=0
      echo "$((NOW-28+2*i)) $value" >> "$STATE_DIR/samples_${1}_$t"
      i=$((i+1))
    done
    echo 1 > "$STATE_DIR/probe_${1}_$t"
  done
}
field() { awk -v field="$2" '{print $field}' "$STATE_DIR/health_wan$1"; }
NOW=100
''' + body
            router = os.environ.get('WAN_WATCHDOG_TEST_ROUTER')
            if router:
                remote_state = '/tmp/wan-watchdog-test-' + Path(state).name
                remote_source = os.environ.get('WAN_WATCHDOG_TEST_SCRIPT', '/jffs/scripts/wan-watchdog')
                remote_command = (
                    'umask 077; mkdir -p ' + shlex.quote(remote_state) + '; '
                    'trap ' + shlex.quote('rm -rf ' + shlex.quote(remote_state)) + ' EXIT; '
                    'STATE_DIR=' + shlex.quote(remote_state) + ' WAN_WATCHDOG_LIBRARY_ONLY=1 '
                    'WATCHDOG_SOURCE=' + shlex.quote(remote_source) + ' OWNERSHIP_DIR=' + shlex.quote(remote_state+'/ownership') + ' sh -s'
                )
                result = subprocess.run(['ssh', '-o', 'BatchMode=yes', router, remote_command],
                                        input=shell, text=True, capture_output=True)
            else:
                result = subprocess.run(['sh'], input=shell, text=True, env=env, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_degradation_and_healthy_backup_both_directions(self):
        for unit in (0, 1):
            self.run_case(f'''
make_window {unit} 7 9; make_window {1-unit} 0 0
classify_wan 0; classify_wan 1; handle_wan {unit}
assert_eq "$(cat "$STATE_DIR/moves")" "{unit} wan{1-unit}"
assert_eq "$(field {unit} 4)" degraded
''')

    def test_one_broken_target_does_not_trigger_failover(self):
        self.run_case('''
make_window 0 15 0; make_window 1 0 0
classify_wan 0; classify_wan 1; handle_wan 0
assert_eq "$(field 0 1)" 0
assert_eq "$([ -f "$STATE_DIR/active_wan0" ] && echo yes || echo no)" no
''')

    def test_both_bad_keep_routes(self):
        self.run_case('''
make_window 0 9 9; make_window 1 9 9
classify_wan 0; classify_wan 1; handle_wan 0; handle_wan 1
assert_eq "$([ -f "$STATE_DIR/moves" ] && echo yes || echo no)" no
''')

    def test_14_percent_backup_loss_rejects_failover(self):
        self.run_case('''
make_window 0 9 9; make_window 1 2 2
classify_wan 0; classify_wan 1; handle_wan 0
assert_eq "$(field 1 2)" 0
assert_eq "$([ -f "$STATE_DIR/moves" ] && echo yes || echo no)" no
''')

    def test_recovery_requires_full_120_seconds(self):
        self.run_case('''
echo 1 > "$STATE_DIR/active_wan0"
make_window 0 0 0; make_window 1 0 0
classify_wan 0; classify_wan 1
assert_eq "$(field 0 3)" 0
NOW=219; make_window 0 0 0; classify_wan 0
assert_eq "$(field 0 3)" 0
NOW=220; make_window 0 0 0; classify_wan 0; handle_wan 0
assert_eq "$(cat "$STATE_DIR/moves")" '0 wan0'
assert_eq "$([ -f "$STATE_DIR/active_wan0" ] && echo yes || echo no)" no
''')

    def test_any_new_loss_resets_recovery(self):
        self.run_case('''
echo 1 > "$STATE_DIR/active_wan0"
make_window 0 0 0; classify_wan 0
NOW=180; make_window 0 0 0; echo 0 > "$STATE_DIR/probe_0_a"; classify_wan 0
assert_eq "$([ -f "$STATE_DIR/recovery_wan0" ] && echo yes || echo no)" no
NOW=182; make_window 0 0 0; classify_wan 0
assert_eq "$(cat "$STATE_DIR/recovery_wan0")" 182
NOW=220; make_window 0 0 0; classify_wan 0
assert_eq "$(field 0 3)" 0
''')

    def test_disabled_never_restores(self):
        self.run_case('''
echo 1 > "$STATE_DIR/active_wan0"
echo 0 > "$STATE_DIR/recovery_wan0"
make_window 0 0 0; make_window 1 0 0
NOW=500; echo 0 > "$STATE_DIR/enabled_wan0"
classify_wan 0; classify_wan 1; handle_wan 0
assert_eq "$(field 0 4)" disabled
assert_eq "$(field 0 3)" 0
assert_eq "$(cat "$STATE_DIR/moves")" '0 wan1'
''')

    def test_unknown_route_blocks_recovery_and_backup(self):
        self.run_case('''
echo 1 > "$STATE_DIR/active_wan0"
make_window 0 0 0; echo -1 > "$STATE_DIR/probe_0_b"
classify_wan 0
assert_eq "$(field 0 4)" unknown
assert_eq "$(field 0 2)" 0
assert_eq "$(field 0 3)" 0
''')

    def test_hard_down_after_12_seconds(self):
        self.run_case('''
make_window 0 0 0
# Avoid degradation threshold so this specifically tests consecutive no replies.
echo 0 > "$STATE_DIR/probe_0_a"; echo 0 > "$STATE_DIR/probe_0_b"
classify_wan 0; assert_eq "$(field 0 1)" 0
NOW=111; classify_wan 0; assert_eq "$(field 0 1)" 0
NOW=112; classify_wan 0; assert_eq "$(field 0 4)" unreachable
''')

    def test_rolling_window_keeps_successes_without_erasing_losses(self):
        self.run_case('''
make_window 0 7 7
NOW=102; record_sample "$STATE_DIR/samples_0_a" 1
set -- $(window_stats "$STATE_DIR/samples_0_a")
assert_eq "$1" 15; assert_eq "$2" 40; assert_eq "$3" 1
''')

    def test_startup_incomplete_window_not_healthy(self):
        self.run_case('''
make_window 0 0 0
sed '10,$d' "$STATE_DIR/samples_0_a" > "$STATE_DIR/trimmed"
mv "$STATE_DIR/trimmed" "$STATE_DIR/samples_0_a"
classify_wan 0
assert_eq "$(field 0 2)" 0
''')

    def test_timeout_jitter_does_not_hide_healthy_backup(self):
        self.run_case('''
make_window 0 0 0
for t in a b; do
  : > "$STATE_DIR/samples_0_$t"
  for age in 28 26 23 21 19 17 15 13 10 8 6 4 2 0; do
    echo "$((NOW-age)) 1" >> "$STATE_DIR/samples_0_$t"
  done
 done
classify_wan 0
assert_eq "$(field 0 2)" 1
''')

    def test_time_based_warmup_requires_30_seconds(self):
        self.run_case('''
make_window 0 0 0
echo "$((NOW-29))" > "$STATE_DIR/window_start_wan0"
classify_wan 0
assert_eq "$(field 0 2)" 0
NOW=101; make_window 0 0 0; echo 71 > "$STATE_DIR/window_start_wan0"
classify_wan 0
assert_eq "$(field 0 2)" 1
''')

    def test_route_validation_never_pings_wrong_interface(self):
        self.run_case('''
ip() { echo '8.8.8.8 from 198.51.100.10 via 203.0.113.1 dev eth2'; }
ping() { echo invoked > "$STATE_DIR/ping_called"; return 0; }
probe_target 198.51.100.10 eth0 8.8.8.8 "$STATE_DIR/result"
assert_eq "$(cat "$STATE_DIR/result")" -1
assert_eq "$([ -f "$STATE_DIR/ping_called" ] && echo yes || echo no)" no
''')

    def test_unknown_history_prevents_healthy_classification(self):
        self.run_case('''
make_window 0 0 0
sed '1s/1$/-1/' "$STATE_DIR/samples_0_a" > "$STATE_DIR/changed"
mv "$STATE_DIR/changed" "$STATE_DIR/samples_0_a"
classify_wan 0
assert_eq "$(field 0 2)" 0
''')

    def test_partial_routing_failure_keeps_marker_for_retry(self):
        self.run_case('''
make_window 0 9 9; make_window 1 0 0
classify_wan 0; classify_wan 1
ensure_unit_rules() { return 1; }
handle_wan 0
assert_eq "$([ -f "$STATE_DIR/active_wan0" ] && echo yes || echo no)" yes
''')

    def test_recovery_failure_retains_failover_state(self):
        self.run_case('''
echo 1 > "$STATE_DIR/active_wan0"
make_window 0 0 0; make_window 1 0 0
classify_wan 0; classify_wan 1
NOW=220; make_window 0 0 0; classify_wan 0
ensure_unit_rules() { return 1; }
handle_wan 0
assert_eq "$([ -f "$STATE_DIR/active_wan0" ] && echo yes || echo no)" yes
''')

    def test_missing_lease_uses_outage_samples(self):
        self.run_case('''
 nvram() {
   case "$2" in
     wan0_enable|wan1_enable) echo 1;;
     wan0_ifname|wan1_ifname) echo eth0;;
     wan0_ipaddr|wan1_ipaddr) echo 0.0.0.0;;
   esac
 }
 prepare_probes
 assert_eq "$(cat "$STATE_DIR/probe_0_a")" 0
 assert_eq "$(cat "$STATE_DIR/probe_1_b")" 0
''')

    def test_failed_route_insertion_keeps_old_rule(self):
        self.run_case('''
ip() { [ "$1" = rule ] && [ "$2" = show ] && return 0; echo "$*" >> "$STATE_DIR/ip_calls"; return 1; }
set_rule_for_ip 192.0.2.10 wan1 && exit 1
assert_eq "$(wc -l < "$STATE_DIR/ip_calls" | tr -d ' ')" 1
''')

    def test_whole_lan_default_and_priority_88(self):
        self.run_case('''
assert_eq "$FAILOVER_MODE" whole_lan
assert_eq "$FAILOVER_PRIORITY" 88
assert_eq "$GUARD_PRIORITY" 87
''')

    def test_rule_intent_saved_before_add(self):
        self.run_case('''
ip() {
  [ "$2 $3" = 'rule show' ] && return 0
  if [ "$2 $3" = 'rule add' ]; then
    grep -qxF '88 from 192.0.2.0/24 lookup 20188' "$OWNED_RULES" || return 1
    echo recorded > "$STATE_DIR/added"
  fi
}
owned_rule_add 88 from 192.0.2.0/24 lookup 20188
assert_eq "$(cat "$STATE_DIR/added")" recorded
''')

    def test_existing_identical_rule_is_not_adopted(self):
        self.run_case('''
ip() { echo '88: from 192.0.2.0/24 lookup 20188'; }
owned_rule_add 88 from 192.0.2.0/24 lookup 20188 && exit 1
assert_eq "$(cat "$OWNED_RULES")" ''
''')

    def test_exact_cleanup_preserves_foreign_same_priority(self):
        self.run_case('''
echo '88: from 192.0.2.0/24 lookup 20188' > "$STATE_DIR/kernel"
echo '88: from 198.51.100.0/24 lookup 77' >> "$STATE_DIR/kernel"
echo '88 from 192.0.2.0/24 lookup 20188' > "$OWNED_RULES"
echo 20188 > "$OWNED_TABLES"
ip() {
  case "$2 $3" in
    'rule show') cat "$STATE_DIR/kernel";;
    'rule del')
      assert_eq "$*" '-4 rule del priority 88 from 192.0.2.0/24 lookup 20188'
      sed '1d' "$STATE_DIR/kernel" > "$STATE_DIR/new"; mv "$STATE_DIR/new" "$STATE_DIR/kernel";;
    'route flush') echo "$*" >> "$STATE_DIR/flushed";;
  esac
}
cleanup_owned
assert_eq "$(cat "$STATE_DIR/kernel")" '88: from 198.51.100.0/24 lookup 77'
assert_eq "$(cat "$STATE_DIR/flushed")" '-4 route flush table 20188'
assert_eq "$(cat "$OWNED_RULES")" ''
''')

    def test_cleanup_removes_one_owned_instance_only(self):
        self.run_case('''
echo '88: from 192.0.2.0/24 lookup 20188' > "$STATE_DIR/kernel"
echo '88: from 192.0.2.0/24 lookup 20188' >> "$STATE_DIR/kernel"
echo '88 from 192.0.2.0/24 lookup 20188' > "$OWNED_RULES"
echo 20188 > "$OWNED_TABLES"
ip() {
  case "$2 $3" in
    'rule show') cat "$STATE_DIR/kernel";;
    'rule del') sed '1d' "$STATE_DIR/kernel" > "$STATE_DIR/new"; mv "$STATE_DIR/new" "$STATE_DIR/kernel";;
    'route flush') exit 1;;
  esac
}
cleanup_owned && exit 1
assert_eq "$(wc -l < "$STATE_DIR/kernel" | tr -d ' ')" 1
assert_eq "$(cat "$OWNED_RULES")" ''
''')

    def test_missing_ledger_refuses_existing_table(self):
        self.run_case('''
ip() { [ "$2 $3" != 'route show' ] || echo 'default via 203.0.113.1 dev eth0'; }
claim_table 20188 && exit 1
assert_eq "$(cat "$OWNED_TABLES")" ''
''')

    def test_foreign_namespace_collision_is_detected(self):
        self.run_case('''
ip() { echo '88: from 198.51.100.0/24 lookup 77'; }
namespace_safe && exit 1
true
''')

    def test_whole_lan_failover_and_restoration(self):
        self.run_case('''
apply_overlay() { echo "$*" >> "$STATE_DIR/overlays"; }
remove_overlay() { echo cleaned >> "$STATE_DIR/overlays"; }
make_window 0 9 9; make_window 1 0 0
classify_wan 0; classify_wan 1; whole_lan_decision
assert_eq "$(cat "$OWNERSHIP_DIR/whole-failover")" 0
assert_eq "$(cat "$STATE_DIR/overlays")" '1 whole_lan'
NOW=102; make_window 0 0 0; classify_wan 0
NOW=222; make_window 0 0 0; classify_wan 0; whole_lan_decision
assert_eq "$(tail -1 "$STATE_DIR/overlays")" cleaned
assert_eq "$([ -f "$OWNERSHIP_DIR/whole-failover" ] && echo yes || echo no)" no
''')

    def test_whole_lan_keeps_routes_when_both_bad(self):
        self.run_case('''
echo 0 > "$OWNERSHIP_DIR/whole-failover"
apply_overlay() { exit 1; }
remove_overlay() { exit 1; }
make_window 0 9 9; make_window 1 9 9
classify_wan 0; classify_wan 1; whole_lan_decision
assert_eq "$(cat "$OWNERSHIP_DIR/whole-failover")" 0
''')

    def test_whole_lan_reverses_if_destination_fails(self):
        self.run_case('''
echo 0 > "$OWNERSHIP_DIR/whole-failover"
echo 1 > "$STATE_DIR/active_wan0"
apply_overlay() { echo "$*" > "$STATE_DIR/overlays"; }
make_window 0 0 0; make_window 1 9 9
classify_wan 0; classify_wan 1; whole_lan_decision
assert_eq "$(cat "$OWNERSHIP_DIR/whole-failover")" 1
assert_eq "$(cat "$STATE_DIR/overlays")" '0 whole_lan'
''')

    def test_vpn_guards_jump_to_original_rules(self):
        self.run_case('''
ip() {
  cat <<'RULES'
0: from all lookup local
90: from all to 10.6.0.2 lookup main
99: from 192.0.2.10 lookup ovpnc1
99: from all fwmark 0x1000/0xf000 lookup 117
100: from 192.0.2.20 lookup wan0
150: from all fwmark 0x80000000/0xf0000000 lookup wan0
RULES
}
vpn_guards > "$STATE_DIR/guards"
grep -qxF '87 from all to 10.6.0.2 goto 90' "$STATE_DIR/guards" || exit 1
grep -qxF '87 from 192.0.2.10 goto 99' "$STATE_DIR/guards" || exit 1
grep -qxF '87 from all fwmark 0x1000/0xf000 goto 99' "$STATE_DIR/guards" || exit 1
assert_eq "$(wc -l < "$STATE_DIR/guards" | tr -d ' ')" 3
''')

    def test_local_throws_exclude_wan_dns_routes(self):
        self.run_case('''
ip() {
  cat <<'ROUTES'
default via 203.0.113.1 dev eth0
1.1.1.1 via 203.0.113.1 dev eth0
192.0.2.0/24 dev br0 scope link
10.6.0.2 dev wgs1 scope link
10.20.0.0/16 via 10.7.0.1 dev wgc1
ROUTES
}
local_throws > "$STATE_DIR/throws"
assert_eq "$(wc -l < "$STATE_DIR/throws" | tr -d ' ')" 3
grep -q '1.1.1.1' "$STATE_DIR/throws" && exit 1
true
''')

    def test_lan_auto_discovery_and_cidr_normalization(self):
        self.run_case('''
nvram() { echo br0; }
ip() {
  cat <<'ADDRS'
1: lo inet 127.0.0.1/8
12: eth0 inet 203.0.113.10/24
24: br0 inet 192.0.2.1/24
25: br1 inet 198.51.100.1/24
26: wgs1 inet 10.6.0.1/32
ADDRS
}
lan_networks > "$STATE_DIR/networks"
assert_eq "$(wc -l < "$STATE_DIR/networks" | tr -d ' ')" 2
grep -qxF '192.0.2.0/24' "$STATE_DIR/networks" || exit 1
grep -qxF '198.51.100.0/24' "$STATE_DIR/networks" || exit 1
''')

    def test_failed_add_keeps_write_ahead_intent(self):
        self.run_case('''
ip() { [ "$2 $3" = 'rule show' ] && return 0; return 1; }
owned_rule_add 88 from 192.0.2.0/24 lookup 20188 && exit 1
grep -qxF '88 from 192.0.2.0/24 lookup 20188' "$OWNED_RULES" || exit 1
''')

    def test_probe_selection_excludes_configured_dns(self):
        self.run_case('''
DNS_SERVERS="8.8.8.8 9.9.9.9"
select_probe_targets
assert_eq "$TARGET_A" 8.8.4.4
assert_eq "$TARGET_B" 9.9.9.11
''')

    def test_probe_selection_refuses_insufficient_candidates(self):
        self.run_case('''
DNS_SERVERS="8.8.8.8 9.9.9.9 8.8.4.4 9.9.9.11 1.1.1.2 208.67.222.222"
select_probe_targets && exit 1
true
''')

    def test_ledger_literal_beginning_with_dash(self):
        self.run_case('''
ledger_add "$OWNED_RULES" '-example literal'
ledger_add "$OWNED_RULES" '-example literal'
assert_eq "$(wc -l < "$OWNED_RULES" | tr -d ' ')" 1
''')

if __name__ == '__main__':
    unittest.main()
