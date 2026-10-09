"""State-machine tests with synthetic probes, clock and routing (no live changes)."""
import os
from pathlib import Path
import subprocess
import shlex
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'wan-watchdog.sh'

class WatchdogTests(unittest.TestCase):
    def run_case(self, body):
        with tempfile.TemporaryDirectory() as state:
            env = dict(os.environ, STATE_DIR=state, WAN_WATCHDOG_LIBRARY_ONLY='1', WATCHDOG_SOURCE=str(SCRIPT))
            shell = '''
. "$WATCHDOG_SOURCE"
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
                    'WATCHDOG_SOURCE=' + shlex.quote(remote_source) + ' sh -s'
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

if __name__ == '__main__':
    unittest.main()
