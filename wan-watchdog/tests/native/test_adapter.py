"""Exercise the new actuator with real legacy policy and mocked mutations."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]

class Adapter(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);(self.root/'owner').mkdir()
        self.legacy=self.root/'legacy.sh'
        self.legacy.write_text('. "'+str(ROOT/'wan-watchdog.sh')+'"\n'+r'''
nvram() { case "$2" in wan0_enable|wan1_enable) echo 1;; wan0_ifname|wan1_ifname) echo lo;; wan0_ipaddr|wan1_ipaddr|wan0_gateway|wan1_gateway) echo 127.0.0.1;; esac; }
ip() { echo 'local 127.0.0.1 dev lo'; }
namespace_safe() { return 0; }
prepare_probe_routes() { return 0; }
select_probe_targets() { TARGET_A=198.18.0.1; TARGET_B=198.18.0.2; }
apply_overlay() {
 [ -f "$STATE_DIR/active_wan$((1-$1))" ] || return 91
 echo "apply $1 $2" >>"$STATE_DIR/actions"
 [ "${FAIL_APPLY:-0}" = 0 ]
}
remove_overlay() { echo remove >>"$STATE_DIR/actions"; [ "${FAIL_REMOVE:-0}" = 0 ]; }
ensure_unit_rules() {
 echo "pin $1 $2" >>"$STATE_DIR/actions"
 [ "${FAIL_PIN:-0}" = 0 ]
}
''')
        self.env=dict(os.environ,WW_LEGACY=str(self.legacy),STATE_DIR=str(self.root),OWNERSHIP_DIR=str(self.root/'owner'),
            DDNS_HELPER='/nonexistent',WW_SAMPLE_SECONDS='100',WW_SHADOW='0',FAILOVER_MODE='whole_lan')
        self.health(0,'1 0 0 degraded 50 50 15 15');self.health(1,'0 1 0 usable 0 0 15 15')

    def health(self,u,content):(self.root/f'health_wan{u}').write_text(content+'\n')
    def run_apply(self,wanted=0,restore=0,p0=1,p1=1,a0=1,a1=0,m0=1,m1=0,**env):
        return subprocess.run(['sh',str(ROOT/'lib/ww-adapter.sh'),'apply',*map(str,[wanted,restore,p0,p1,a0,a1,m0,m1])],
            env=dict(self.env,**env),text=True,capture_output=True)
    def test_whole_failover_records_intent_before_apply(self):
        r=self.run_apply();self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual((self.root/'owner/whole-failover').read_text().strip(),'0')
        self.assertTrue((self.root/'active_wan0').exists())
        self.assertEqual((self.root/'actions').read_text(),'apply 1 whole_lan\n')
    def test_failed_apply_keeps_marker_and_ledger(self):
        r=self.run_apply(FAIL_APPLY='1');self.assertNotEqual(r.returncode,0)
        self.assertTrue((self.root/'active_wan0').exists())
        self.assertEqual((self.root/'owner/whole-failover').read_text().strip(),'0')
    def test_restore_failure_preserves_state(self):
        self.assertEqual(self.run_apply().returncode,0)
        self.assertNotEqual(self.run_apply(wanted=-1,restore=1,FAIL_REMOVE='1').returncode,0)
        self.assertTrue((self.root/'active_wan0').exists())
        self.assertEqual(self.run_apply(wanted=-1,restore=1).returncode,0)
        self.assertFalse((self.root/'active_wan0').exists())
        self.assertFalse((self.root/'owner/whole-failover').exists())
    def test_emergency_reversal_changes_only_owned_intent(self):
        self.assertEqual(self.run_apply().returncode,0)
        self.health(0,'0 1 0 usable 0 0 15 15');self.health(1,'1 0 0 unreachable 100 100 15 15')
        self.assertEqual(self.run_apply(wanted=1).returncode,0)
        self.assertEqual((self.root/'owner/whole-failover').read_text().strip(),'1')
        self.assertFalse((self.root/'active_wan0').exists());self.assertTrue((self.root/'active_wan1').exists())
    def test_pinned_failure_and_restore_failure_keep_recovery_marker(self):
        r=self.run_apply(FAILOVER_MODE='pinned');self.assertEqual(r.returncode,0,r.stderr)
        self.assertTrue((self.root/'active_wan0').exists())
        r=self.run_apply(p0=0,a0=0,FAILOVER_MODE='pinned',FAIL_PIN='1');self.assertNotEqual(r.returncode,0)
        self.assertTrue((self.root/'active_wan0').exists())
    def test_shadow_cannot_mutate(self):
        self.assertNotEqual(self.run_apply(WW_SHADOW='1').returncode,0)
        self.assertFalse((self.root/'actions').exists())
    def test_no_healthy_backup_keeps_existing_route(self):
        self.health(1,'1 0 0 unreachable 100 100 15 15')
        self.assertEqual(self.run_apply().returncode,0)
        self.assertFalse((self.root/'actions').exists())
    def test_argument_text_is_rejected(self):
        r=self.run_apply(wanted='0; touch bad');self.assertNotEqual(r.returncode,0)
        self.assertFalse((self.root/'actions').exists())
    def test_invalid_boolean_and_contradictory_restore_are_rejected(self):
        self.assertEqual(self.run_apply(restore=-1).returncode,72)
        self.assertEqual(self.run_apply(p0=-1).returncode,72)
        self.assertEqual(self.run_apply(wanted=0,restore=1).returncode,72)
    def test_obsolete_lease_cannot_drive_routing(self):
        r=self.run_apply(WW_EXPECT0='1:198.18.0.9:198.18.0.1:lo')
        self.assertEqual(r.returncode,75)
        self.assertFalse((self.root/'actions').exists())

if __name__=='__main__':unittest.main()
