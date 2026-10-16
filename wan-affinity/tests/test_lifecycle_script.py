"""Host tests for the wan-affinity lifecycle controller.

These run wan-affinity.sh with mock iptables/nvram/conntrack/id binaries so that
no real networking commands execute. They assert ownership boundaries: only our
chain and jump are touched, conntrack is never flushed, and unsupported or
disabled configurations refuse to activate.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import signal

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(TESTS_DIR)
SCRIPT = os.path.join(REPO, "wan-affinity.sh")
MOCK_IPT = os.path.join(TESTS_DIR, "mock", "iptables")


class LifecycleScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wa-lifecycle-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        dest = os.path.join(self.bin, "iptables")
        shutil.copy(MOCK_IPT, dest)
        os.chmod(dest, 0o755)
        self._mock("id", "#!/bin/sh\necho 0\n")
        self._mock("flock", "#!/usr/bin/env python3\nimport fcntl,sys\ntry: fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError: sys.exit(1)\n")
        self._mock(
            "nvram",
            "#!/bin/sh\ncase \"$2\" in\n"
            "  wans_mode) echo lb;;\n"
            "  lan_ipaddr) echo 192.168.1.1;;\n"
            "  lan_netmask) echo 255.255.255.0;;\n"
            "  lan_ifname) echo br0;;\n"
            "  et0macaddr) echo aa:bb:cc:dd:ee:ff;;\n"
            "  *) echo \"\";;\nesac\n",
        )
        self._mock("conntrack", "#!/bin/sh\necho \"conntrack $@\" >> \"$MOCK_LOG\"\n")

        self.proc = os.path.join(self.tmp, "proc")
        os.makedirs(os.path.join(self.proc, "net", "netfilter"))
        with open(os.path.join(self.proc, "net", "ip_tables_targets"), "w") as fh:
            fh.write("NFQUEUE\n")
        open(os.path.join(self.proc, "net", "netfilter", "nfnetlink_queue"), "w").close()

        self.base = os.path.join(self.tmp, "jffs", "wan-affinity")
        os.makedirs(self.base)
        self.run_dir = os.path.join(self.tmp, "run")
        self.cfg = os.path.join(self.base, "wan-affinity.conf")
        self.state = os.path.join(self.tmp, "ipt_state.json")
        self.log = os.path.join(self.tmp, "mock.log")
        open(self.log, "w").close()
        self._write_config(enabled=0)

    def _mock(self, name, body):
        path = os.path.join(self.bin, name)
        with open(path, "w") as fh:
            fh.write(body)
        os.chmod(path, 0o755)

    def _fake_binary(self):
        path = os.path.join(self.base, "wan-affinity")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(path, 0o755)

    def _write_config(self, enabled):
        with open(self.cfg, "w") as fh:
            fh.write(
                "ENABLED=%d\nMODE=sticky\nCLIENT=192.168.1.100\n"
                "LAN_SCOPE=0\nQUEUE_NUM=20880\n" % enabled
            )

    def _seed(self, prerouting, extra_chains=None, owned=True):
        chains = {"PREROUTING": prerouting}
        chains.update(extra_chains or {})
        with open(self.state, "w") as fh:
            json.dump({"chains": chains}, fh)
        if owned and "WA_ADMIT" in chains:
            os.makedirs(self.run_dir,exist_ok=True)
            with open(os.path.join(self.run_dir,"owned-chain"),"w") as fh:fh.write("WA_ADMIT\n")
            ours=[r for r in prerouting if r[-2:]==["-j","WA_ADMIT"]]
            if ours:
                with open(os.path.join(self.run_dir,"owned-jump"),"w") as fh:fh.write(" ".join(ours[0])+"\n")

    def _chains(self):
        with open(self.state) as fh:
            return json.load(fh)["chains"]

    def _run(self, *args, extra_env=None):
        env = dict(os.environ)
        env["PATH"] = self.bin + os.pathsep + env["PATH"]
        env["WA_BASE"] = self.base
        env["WA_RUN"] = self.run_dir
        env["WA_PROC"] = self.proc
        env["IPT_STATE"] = self.state
        env["MOCK_LOG"] = self.log
        if extra_env:env.update(extra_env)
        return subprocess.run(
            ["sh", SCRIPT] + list(args),
            capture_output=True,
            text=True,
            env=env,
            cwd=self.tmp,
        )

    def _running_binary(self):
        path=os.path.join(self.base,"wan-affinity")
        body='''#!/usr/bin/env python3
import os,sys,time,signal
args=sys.argv
run=args[args.index('--status-dir')+1]
queue=args[args.index('--queue')+1]
proc=os.environ['WA_PROC']
pid=str(os.getpid())
os.makedirs(proc+'/'+pid,exist_ok=True)
open(proc+'/'+pid+'/cmdline','w').write(sys.argv[0]+'\\0')
open(proc+'/net/netfilter/nfnetlink_queue','w').write(queue+' '+pid+' 0 2 128 0 0 0 1\\n')
open(run+'/status','w').write('mode=sticky ready=yes\\n')
running=True
def stop(*args):
 global running
 running=False
signal.signal(signal.SIGTERM,stop)
while running:time.sleep(.02)
open(proc+'/net/netfilter/nfnetlink_queue','w').close()
'''
        with open(path,"w") as fh:fh.write(body)
        os.chmod(path,0o755)

        def stop_leftover():
            pf=os.path.join(self.run_dir,"wan-affinity.pid")
            try:
                with open(pf) as fh:pid=int(fh.read())
                os.kill(pid,signal.SIGTERM)
            except (OSError,ValueError):pass
        self.addCleanup(stop_leftover)

    def test_disabled_refuses_and_touches_nothing(self):
        self._seed([])
        r = self._run("start")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("disabled", r.stderr)
        self.assertNotIn("WA_ADMIT", self._chains())

    def test_missing_firmware_balance_refuses(self):
        self._write_config(enabled=1)
        self._fake_binary()
        self._seed([])
        r = self._run("start")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("balance", r.stderr)
        self.assertNotIn("WA_ADMIT", self._chains())

    def test_cleanup_on_empty_state_is_safe_and_never_flushes_conntrack(self):
        self._seed([])
        r = self._run("cleanup")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.log) as fh:
            invocations = fh.read()
        self.assertNotIn("conntrack", invocations)
        self.assertEqual(self._chains()["PREROUTING"], [])

    def test_cleanup_removes_only_owned_objects(self):
        balance = ["-i", "br0", "-m", "state", "--state", "NEW", "-j", "balance"]
        ours = ["-i", "br0", "-m", "state", "--state", "NEW", "-s", "192.168.1.100", "-j",
                "WA_ADMIT"]
        ttl = ["-o", "eth2", "-j", "TTL", "--ttl-set", "65"]
        self._seed(
            [balance, ours, ttl],
            extra_chains={
                "WA_ADMIT": [["-m", "mark", "--mark", "0x80000000/0xf0000000", "-j", "CONNMARK"]],
                "OTHER": [["-j", "RETURN"]],
            },
        )
        r = self._run("cleanup")
        self.assertEqual(r.returncode, 0, r.stderr)
        chains = self._chains()
        self.assertEqual(chains["PREROUTING"], [balance, ttl])
        self.assertNotIn("WA_ADMIT", chains)
        self.assertIn("OTHER", chains)

    def test_disable_sets_enabled_zero_and_removes_our_rules(self):
        self._write_config(enabled=1)
        balance = ["-i", "br0", "-m", "state", "--state", "NEW", "-j", "balance"]
        ours = ["-i", "br0", "-j", "WA_ADMIT"]
        self._seed([balance, ours], extra_chains={"WA_ADMIT": [["-j", "RETURN"]]})
        r = self._run("disable")
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.cfg) as fh:
            self.assertIn("ENABLED=0", fh.read())
        self.assertEqual(self._chains()["PREROUTING"], [balance])

    def test_disable_does_not_require_a_valid_or_present_config(self):
        for contents in ["ENABLED=1\nMODE=Sticky\n", "ENABLED=1\nMODE='broken\n", None]:
            with self.subTest(contents=contents):
                if contents is None:
                    os.unlink(self.cfg)
                else:
                    with open(self.cfg,"w") as fh:fh.write(contents)
                self._seed([["-j","balance"],["-i","br0","-j","WA_ADMIT"]],extra_chains={"WA_ADMIT":[["-j","RETURN"]]})
                r=self._run("disable",extra_env={"SSH_CONNECTION":"192.168.1.100 1234 192.168.1.1 22"})
                self.assertEqual(r.returncode,0,r.stderr)
                self.assertEqual(self._chains()["PREROUTING"],[["-j","balance"]])
                with open(self.cfg) as fh:self.assertIn("ENABLED=0",fh.read())

    def test_disabled_reconcile_stops_even_with_broken_shell_syntax(self):
        for enabled in ["0", "'0'", '"0"']:
            with self.subTest(enabled=enabled):
                with open(self.cfg,"w") as fh:fh.write("ENABLED="+enabled+"\nMODE='broken\n")
                self._seed([["-j","balance"],["-i","br0","-j","WA_ADMIT"]],extra_chains={"WA_ADMIT":[["-j","RETURN"]]})
                self.assertEqual(self._run("reconcile").returncode,0)
                self.assertEqual(self._chains()["PREROUTING"],[["-j","balance"]])

    def test_missing_helper_removes_abandoned_rules_before_config_validation(self):
        self._write_config(enabled=1)
        with open(self.cfg,'a') as fh:fh.write('MODE=Sticky\n')
        self._seed([["-j","balance"],["-i","br0","-j","WA_ADMIT"]],extra_chains={"WA_ADMIT":[["-j","RETURN"]]})
        r=self._run('reconcile')
        self.assertNotEqual(r.returncode,0)
        self.assertIn('invalid MODE',r.stderr)
        self.assertEqual(self._chains()['PREROUTING'],[["-j","balance"]])

    def test_disable_from_management_scope(self):
        self._write_config(enabled=1)
        self._seed([["-j","balance"],["-i","br0","-j","WA_ADMIT"]],extra_chains={"WA_ADMIT":[["-j","RETURN"]]})
        r=self._run("disable",extra_env={"SSH_CONNECTION":"192.168.1.100 1234 192.168.1.1 22"})
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self._chains()["PREROUTING"],[["-j","balance"]])

    def test_uninstall_removes_only_its_own_directories(self):
        self._seed([])
        os.makedirs(self.run_dir, exist_ok=True)
        with open(os.path.join(self.base, "wan-affinity"), "w") as fh:
            fh.write("")
        r = self._run("uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(os.path.exists(self.base))
        self.assertFalse(os.path.exists(self.run_dir))

    def test_complete_start_and_stop_preserves_firmware_order(self):
        self._write_config(enabled=1);self._running_binary()
        balance=["-i","br0","-m","state","--state","NEW","-j","balance"]
        before=["-i","wgs1","-j","MARK","--set-xmark","0x1/0x1"]
        self._seed([before,balance])
        r=self._run("start")
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertGreater(len(self._chains()["WA_ADMIT"]),3)
        self.assertEqual(self._chains()["PREROUTING"][0],before)
        self.assertEqual(self._chains()["PREROUTING"][2],balance)
        r=self._run("stop")
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(self._chains()["PREROUTING"],[before,balance])
        self.assertNotIn("WA_ADMIT",self._chains())

    def test_failed_insertion_rolls_back_chain_and_helper(self):
        self._write_config(enabled=1);self._running_binary()
        balance=["-i","br0","-m","state","--state","NEW","-j","balance"]
        self._seed([balance])
        r=self._run("start",extra_env={"IPT_FAIL_CONTAINS":"-I PREROUTING"})
        self.assertNotEqual(r.returncode,0)
        self.assertNotIn("WA_ADMIT",self._chains())
        self.assertEqual(self._chains()["PREROUTING"],[balance])
        self.assertFalse(os.path.exists(os.path.join(self.run_dir,"wan-affinity.pid")))

    def test_router_without_cksum_uses_md5sum_and_preserves_helper(self):
        self._write_config(enabled=1);self._running_binary()
        self._seed([["-i","br0","-j","balance"]])
        self._mock("cksum","#!/bin/sh\nexit 127\n")
        self._mock("md5sum","#!/usr/bin/env python3\nimport hashlib,sys\np=sys.argv[1]\nprint(hashlib.md5(open(p,'rb').read()).hexdigest()+'  '+p)\n")
        r=self._run("start")
        self.assertEqual(r.returncode,0,r.stderr)
        pf=os.path.join(self.run_dir,"wan-affinity.pid")
        with open(pf) as fh:pid=fh.read()
        self.assertEqual(self._run("reconcile").returncode,0)
        with open(pf) as fh:self.assertEqual(fh.read(),pid)
        self.assertEqual(self._run("stop").returncode,0)

    def test_unreadable_config_fingerprint_refuses_activation(self):
        self._write_config(enabled=1);self._running_binary()
        self._seed([["-i","br0","-j","balance"]])
        self._mock("md5sum","#!/bin/sh\nexit 1\n")
        r=self._run("start")
        self.assertNotEqual(r.returncode,0)
        self.assertIn("cannot fingerprint config",r.stderr)
        self.assertNotIn("WA_ADMIT",self._chains())

    def test_reconcile_retains_helper_and_rebuilds_only_lost_owned_rules(self):
        self._write_config(enabled=1);self._running_binary()
        balance=["-i","br0","-m","state","--state","NEW","-j","balance"]
        self._seed([balance])
        self.assertEqual(self._run("start").returncode,0)
        pf=os.path.join(self.run_dir,"wan-affinity.pid")
        with open(pf) as fh:pid=fh.read()
        self.assertEqual(self._run("reconcile").returncode,0)
        with open(pf) as fh:self.assertEqual(fh.read(),pid)
        # A firmware rebuild loses kernel objects but leaves our RAM records.
        self._seed([balance],owned=False)
        r=self._run("reconcile")
        self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual(len(self._chains()["PREROUTING"]),2)
        with open(pf) as fh:self.assertEqual(fh.read(),pid)
        self.assertEqual(self._run("stop").returncode,0)

    def test_stopped_service_is_not_restarted_by_firewall_hook(self):
        self._write_config(enabled=1);self._running_binary()
        self._seed([["-i","br0","-j","balance"]])
        self.assertEqual(self._run("start").returncode,0)
        self.assertEqual(self._run("stop").returncode,0)
        self.assertEqual(self._run("reconcile").returncode,0)
        self.assertNotIn("WA_ADMIT",self._chains())
        self.assertEqual(self._run("start").returncode,0)
        self.assertEqual(self._run("stop").returncode,0)

    def test_hook_install_is_additive_idempotent_and_removable(self):
        scripts=os.path.join(self.tmp,"scripts");os.mkdir(scripts)
        existing={"services-start":"#!/bin/sh\n/jffs/scripts/wan-watchdog &\nexit 0\n",
                  "firewall-start":"#!/bin/sh\niptables -t mangle -A POSTROUTING -o eth2 -j TTL --ttl-set 65\n"}
        for name,body in existing.items():
            with open(os.path.join(scripts,name),"w") as fh:fh.write(body)
        hooks=os.path.join(REPO,"hooks.sh")
        env=dict(os.environ,PATH=self.bin+os.pathsep+os.environ['PATH'],WA_BASE=self.base,WA_SCRIPTS=scripts)
        for action in ["install","install"]:
            r=subprocess.run(["sh",hooks,action],env=env,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
        for name,body in existing.items():
            with open(os.path.join(scripts,name)) as fh:text=fh.read()
            self.assertEqual(text.count('# BEGIN wan-affinity'),1)
            self.assertIn(body.split('\n',1)[1],text)
        r=subprocess.run(["sh",hooks,"remove"],env=env,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
        for name,body in existing.items():
            with open(os.path.join(scripts,name)) as fh:self.assertEqual(fh.read(),body)

    def test_hooks_preserve_bash_shebang_options_and_syntax(self):
        scripts=os.path.join(self.tmp,"scripts");os.mkdir(scripts)
        body='#!'+shutil.which('bash')+' -e\nvalues=(one two)\n[[ ${values[1]} == two ]]\n'
        hook=os.path.join(scripts,"firewall-start")
        with open(hook,"w") as fh:fh.write(body)
        env=dict(os.environ,PATH=self.bin+os.pathsep+os.environ['PATH'],WA_BASE=self.base,WA_SCRIPTS=scripts)
        for action in ['install','install','remove']:
            r=subprocess.run(['sh',os.path.join(REPO,'hooks.sh'),action],env=env,capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr)
            with open(hook) as fh:contents=fh.read()
            self.assertEqual(contents.splitlines()[0],body.splitlines()[0])
        self.assertEqual(contents,body)

    def test_foreign_chain_is_never_flushed(self):
        self._seed([],extra_chains={"WA_ADMIT":[["-j","RETURN"]]},owned=False)
        r=self._run("cleanup")
        self.assertNotEqual(r.returncode,0)
        self.assertEqual(self._chains()["WA_ADMIT"],[["-j","RETURN"]])

    def test_client_cidr_cannot_broaden_single_device_scope(self):
        self._write_config(enabled=1)
        with open(self.cfg,"a") as fh:fh.write("CLIENT=192.168.1.0/24\n")
        self._seed([])
        r=self._run("start")
        self.assertNotEqual(r.returncode,0)
        self.assertIn("exactly one IPv4",r.stderr)

    def test_ssh_management_client_is_rejected(self):
        self._write_config(enabled=1);self._seed([])
        r=self._run("start",extra_env={"SSH_CONNECTION":"192.168.1.100 1234 192.168.1.1 22"})
        self.assertNotEqual(r.returncode,0)
        self.assertIn("management",r.stderr)


if __name__ == "__main__":
    unittest.main()
