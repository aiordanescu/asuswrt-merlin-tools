"""Lifecycle regressions: real processes/locks, private /proc, no networking."""
from pathlib import Path
import os, shutil, signal, subprocess, tempfile, time, unittest

ROOT=Path(__file__).resolve().parents[1]
DAEMON=r'''#!/usr/bin/env python3
import fcntl,os,signal,time
from pathlib import Path
r=Path(os.environ['STATE_DIR']);proc=Path(os.environ['WW_PROC']);base=Path(os.environ['WW_BASE'])
kind=KIND
if (r/'fail-start').exists():raise SystemExit(1)
lock=open(r/'lock','w');fcntl.flock(lock,fcntl.LOCK_EX)
p=str(os.getpid());d=proc/p;d.mkdir()
(d/'cmdline').write_bytes((str(base/'wan-watchdog')+'\0').encode() if kind=='monitor' else ('/bin/sh\0'+os.environ['WW_ENTRY']+'\0').encode())
keys=['FAILOVER_MODE','FAILOVER_PRIORITY','PROBE_FALLBACKS','LAN_NETWORKS','DNS_SERVERS','SECRET_SENTINEL']
(d/'environ').write_bytes(b'\0'.join((k+'='+os.environ[k]).encode() for k in keys if k in os.environ)+b'\0')
(r/'wan-watchdog.pid').write_text(p+'\n')
(r/'started-policy').write_text(os.environ.get('FAILOVER_MODE','whole_lan')+' '+os.environ.get('FAILOVER_PRIORITY','88'))
running=True
def stop(*args):
 global running
 running=False
signal.signal(signal.SIGTERM,stop)
n=0
while running:
 n+=1
 # The shell's status mirrors wan-watchdog-legacy.sh write_status.
 text=('monitor=1 publisher_ready=1 routing_error=0 desired=-1 applied=-1 progress='+str(n)+'\n') if kind=='monitor' else ('uptime_seconds='+str(n)+' mode='+os.environ.get('FAILOVER_MODE','whole_lan')+' targets=198.18.0.1,198.18.0.2\nrule_priority='+os.environ.get('FAILOVER_PRIORITY','88')+' ownership=owner whole_failed=none\nWAN0 usable loss=0%/0% samples=15/15 failed_over=no recovery_since=none\nWAN1 usable loss=0%/0% samples=15/15 failed_over=no recovery_since=none\n')
 (r/'status.new').write_text(text);(r/'status.new').replace(r/'status')
 time.sleep(.02)
if (r/'inherit-lock').exists():
 if os.fork()==0:time.sleep(.5);os._exit(0)
(d/'cmdline').unlink(missing_ok=True)
try:
 if (r/'wan-watchdog.pid').read_text().strip()==p:(r/'wan-watchdog.pid').unlink()
except FileNotFoundError:pass
'''

class Service(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='ww-service-');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.base=self.root/'service';self.run=self.root/'run';self.proc=self.root/'proc';self.owner=self.root/'owner';self.entry=self.root/'scripts/wan-watchdog';self.bin=self.root/'bin'
        for d in [self.run,self.proc/'self',self.owner,self.entry.parent,self.bin]:d.mkdir(parents=True)
        (self.proc/'self/status').write_text('Uid: 0 0 0 0\n');(self.proc/'uptime').write_text('100 100\n')
        self.env=dict(os.environ,WW_BASE=str(self.base),STATE_DIR=str(self.run),OWNERSHIP_DIR=str(self.owner),WW_ENTRY=str(self.entry),WW_PROC=str(self.proc),PATH=str(self.bin)+os.pathsep+os.environ['PATH'])
        self.write(self.bin/'flock',r'''#!/usr/bin/env python3
import fcntl,os,sys
a=sys.argv[1:];nonblock='-n' in a;a=[s for s in a if s not in ['-n','-x']]
f=int(a[0]) if a[0].isdigit() else os.open(a[0],os.O_CREAT|os.O_RDWR,0o600)
try:fcntl.flock(f,fcntl.LOCK_EX|(fcntl.LOCK_NB if nonblock else 0))
except BlockingIOError:raise SystemExit(1)
if len(a)>1:os.execvp(a[1],a[1:])
''')
        self.write(self.bin/'sleep',r'''#!/usr/bin/env python3
from pathlib import Path
import fcntl,os,sys,time
n=float(sys.argv[1])
if n>=120:time.sleep(30)
else:
 time.sleep(.02)
 p=Path(os.environ['WW_PROC'])/'uptime'
 with open(str(p)+'.lock','w') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX);v=float(p.read_text().split()[0])+n;p.write_text(str(v)+' 100\n')
''')
        self.write(self.bin/'md5sum',r'''#!/usr/bin/env python3
import hashlib,sys
print(hashlib.md5(open(sys.argv[1],'rb').read()).hexdigest()+'  '+sys.argv[1])
''')
        self.source=self.root/'bundle';(self.source/'lib').mkdir(parents=True)
        for name in ['install.sh','rollback.sh','wan-watchdog.sh','lib/ww-service.sh','lib/ww-adapter.sh']:shutil.copyfile(ROOT/name,self.source/name)
        self.write(self.source/'wan-watchdog',DAEMON.replace('KIND',repr('monitor')))
        self.write(self.source/'wan-watchdog-legacy.sh',DAEMON.replace('KIND',repr('shell')))
        self.write(self.source/'wan-healthctl','#!/bin/sh\nprintf \'%s\\n\' \'{"fresh":true,"routing_error":0,"wan":[{"eligible":1},{"eligible":1}]}\'\n')
        self.addCleanup(self.cleanup_processes)

    def write(self,path,text):path.write_text(text);path.chmod(0o700)
    def cleanup_processes(self):
        for p in self.proc.iterdir():
            if p.name.isdigit():
                try:os.kill(int(p.name),signal.SIGTERM)
                except ProcessLookupError:pass
        for g in self.base.glob('rollback/*/guard.pid'):
            try:os.kill(int(g.read_text()),signal.SIGTERM)
            except ProcessLookupError:pass
        for process in getattr(self,'processes',[]):
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    def start_old(self,kind='monitor',**settings):
        if kind=='monitor':
            shutil.copytree(self.source,self.base);shutil.copyfile(self.source/'wan-watchdog.sh',self.entry);path=self.base/'wan-watchdog'
        else:
            shutil.copyfile(self.source/'wan-watchdog-legacy.sh',self.entry);self.entry.chmod(0o700);path=self.entry
        (self.owner/'mode').write_text(settings.get('FAILOVER_MODE','whole_lan')+'\n')
        p=subprocess.Popen([str(path)],env=dict(self.env,**settings),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.processes=[p]
        end=time.monotonic()+4
        while not (self.run/'status').exists() and time.monotonic()<end:time.sleep(.01)
        self.assertIsNone(p.poll());return p
    def install(self,action='--upgrade'):
        return subprocess.run(['sh',str(self.source/'install.sh'),action],env=self.env,capture_output=True,text=True,timeout=15)
    def rollback(self,*args):
        return subprocess.run(['sh',str(self.base/'rollback.sh'),*(args or ['--rollback'])],env=self.env,capture_output=True,text=True,timeout=15)

    def test_upgrade_and_rollback_preserve_pinned_policy_and_exclude_secrets(self):
        self.start_old(FAILOVER_MODE='pinned',FAILOVER_PRIORITY='78',SECRET_SENTINEL='must-not-copy')
        r=self.install();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        config=(self.owner/'service.conf').read_text();self.assertNotIn('SECRET_SENTINEL',config);self.assertNotIn('must-not-copy',config)
        self.assertEqual((self.run/'started-policy').read_text(),'pinned 78')
        r=self.rollback();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.run/'started-policy').read_text(),'pinned 78')

    def test_first_install_waits_for_inherited_shell_lock(self):
        self.start_old(kind='shell',FAILOVER_MODE='pinned',FAILOVER_PRIORITY='78');(self.run/'inherit-lock').touch()
        r=self.install('--install');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.run/'started-policy').read_text(),'pinned 78')

    def test_first_install_rollback_confirms_restored_shell(self):
        self.start_old(kind='shell',FAILOVER_MODE='pinned',FAILOVER_PRIORITY='78')
        r=self.install('--install');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        r=self.rollback();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('Previous shell watchdog restored and monitoring.',r.stdout)
        token=(self.base/'rollback/latest').read_text().strip()
        self.assertTrue((self.base/'rollback'/token/'restored').exists())
        self.assertEqual((self.run/'started-policy').read_text(),'pinned 78')

    def test_shell_readiness_matches_real_status_format(self):
        rendered=subprocess.run(['sh','-c','. "$1"; NOW=1234; TARGET_A=198.18.0.1; TARGET_B=198.18.0.2; FAILOVER_MODE=pinned; write_status','sh',str(ROOT/'wan-watchdog-legacy.sh')],
            env=dict(self.env,WAN_WATCHDOG_LIBRARY_ONLY='1'),capture_output=True,text=True)
        self.assertEqual(rendered.returncode,0,rendered.stderr)
        monitor=self.root/'monitor-status'
        monitor.write_text('monitor=1 shadow=0 uptime_seconds=1234 mode=pinned seq=1\nWAN0 eligible=1\nWAN1 eligible=1\n')
        check='. "$1"; ww_shell_status_ok "$2"'
        for status,ready in [(self.run/'status',0),(monitor,1)]:
            p=subprocess.run(['sh','-c',check,'sh',str(ROOT/'lib/ww-service.sh'),str(status)],env=self.env,capture_output=True,text=True)
            self.assertEqual(p.returncode,ready,status.read_text())

    def test_first_install_captures_running_shell_over_stale_config(self):
        (self.owner/'service.conf').write_text('FAILOVER_MODE=whole_lan\nFAILOVER_PRIORITY=99\n')
        self.start_old(kind='shell',FAILOVER_MODE='pinned',FAILOVER_PRIORITY='78')
        r=self.install('--install');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.run/'started-policy').read_text(),'pinned 78')
        self.assertNotIn('FAILOVER_PRIORITY=99',(self.owner/'service.conf').read_text())

    def test_upgrade_keeps_saved_monitor_configuration(self):
        (self.owner/'service.conf').write_text('FAILOVER_MODE=pinned\nFAILOVER_PRIORITY=77\n')
        self.start_old()
        r=self.install();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((self.run/'started-policy').read_text(),'pinned 77')

    def test_stale_pid_cannot_make_failed_replacement_look_successful(self):
        self.start_old();self.assertEqual(self.install().returncode,0)
        pid=int((self.run/'wan-watchdog.pid').read_text());os.kill(pid,signal.SIGTERM)
        for _ in range(200):
            if not (self.proc/str(pid)/'cmdline').exists():break
            time.sleep(.01)
        (self.run/'wan-watchdog.pid').write_text('999999999\n');(self.run/'fail-start').touch()
        r=self.rollback();self.assertNotEqual(r.returncode,0)
        self.assertNotIn('restored and',r.stdout);self.assertFalse((self.run/'wan-watchdog.pid').exists())

    def test_guard_cannot_reverse_a_committed_install(self):
        self.start_old();self.assertEqual(self.install().returncode,0)
        pid=(self.run/'wan-watchdog.pid').read_text();token=(self.base/'rollback/latest').read_text().strip()
        r=self.rollback('--guard',token);self.assertEqual(r.returncode,0,r.stderr)
        self.assertEqual((self.run/'wan-watchdog.pid').read_text(),pid)

    def test_unknown_live_owner_is_preserved(self):
        self.start_old();self.assertEqual(self.install().returncode,0)
        pid=(self.run/'wan-watchdog.pid').read_text().strip();(self.proc/pid/'cmdline').write_bytes(b'/foreign/owner\0')
        r=self.rollback();self.assertNotEqual(r.returncode,0);self.assertIn('unrecognized live owner',r.stderr)
        self.assertTrue((self.proc/pid/'cmdline').exists())

    def test_first_install_can_retry_after_rollback(self):
        self.start_old(kind='shell',FAILOVER_MODE='pinned',FAILOVER_PRIORITY='78')
        self.assertEqual(self.install('--install').returncode,0)
        self.assertEqual(self.rollback().returncode,0)
        previous=(self.base/'rollback/latest').read_text()
        r=self.install('--install');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertNotEqual((self.base/'rollback/latest').read_text(),previous)
        self.assertEqual((self.run/'started-policy').read_text(),'pinned 78')

    def test_capture_failure_preserves_owner_and_creates_no_install_tree(self):
        old=self.start_old(kind='shell',FAILOVER_PRIORITY='78')
        self.write(self.bin/'hexdump','#!/bin/sh\nexit 17\n')
        r=self.install('--install');self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIsNone(old.poll());self.assertFalse(self.base.exists())
        self.assertEqual(list(self.run.glob('install-policy.*')),[])

    def test_reused_pid_with_free_lock_does_not_block_rollback(self):
        old=self.start_old();self.assertEqual(self.install().returncode,0)
        pid=int((self.run/'wan-watchdog.pid').read_text());os.kill(pid,signal.SIGTERM)
        end=time.monotonic()+2
        while (self.proc/str(pid)/'cmdline').exists() and time.monotonic()<end:time.sleep(.01)
        foreign=self.proc/str(old.pid);foreign.mkdir(exist_ok=True)
        (foreign/'cmdline').write_bytes(b'/unrelated/process\0')
        (self.run/'wan-watchdog.pid').write_text(str(old.pid)+'\n')
        r=self.rollback();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual((foreign/'cmdline').read_bytes(),b'/unrelated/process\0')

    def test_runtime_modes_are_enforced_on_existing_directory(self):
        self.start_old();self.run.chmod(0o777)
        r=self.install();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertEqual(self.run.stat().st_mode & 0o777,0o700)
        self.assertEqual((self.run/'monitor.log').stat().st_mode & 0o777,0o600)

    def test_multiline_policy_preserves_values_and_nul_boundaries(self):
        value='192.168.1.0/24\n10.0.0.0/8'
        self.start_old(kind='shell',LAN_NETWORKS=value,DNS_SERVERS='1.1.1.1\n8.8.8.8',SECRET_SENTINEL='secret\nFAILOVER_PRIORITY=99')
        r=self.install('--install');self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        p=subprocess.run(['sh','-c','. "$1"; printf "%s" "$LAN_NETWORKS"','sh',str(self.owner/'service.conf')],capture_output=True,text=True)
        self.assertEqual(p.stdout,value);self.assertNotIn('99',(self.owner/'service.conf').read_text())

    def test_optional_config_edits_are_exported_to_the_program(self):
        (self.owner/'service.conf').write_text('DDNS_LOOKUP_SECONDS=3\n')
        p=subprocess.run(['sh','-c','. "$1"; ww_policy_load; sh -c \'printf "%s" "$DDNS_LOOKUP_SECONDS"\'','sh',str(ROOT/'lib/ww-service.sh')],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout,'3')

    def test_policy_serialization_quotes_shell_metacharacters(self):
        self.start_old(PROBE_FALLBACKS="one'two $(touch should-not-exist)")
        r=self.install();self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        p=subprocess.run(['sh','-c','. "$1"; printf "%s" "$PROBE_FALLBACKS"','sh',str(self.owner/'service.conf')],cwd=self.root,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout,"one'two $(touch should-not-exist)")
        self.assertFalse((self.root/'should-not-exist').exists())

    def test_owner_lost_after_health_leaves_guard_armed(self):
        self.start_old()
        self.write(self.source/'wan-healthctl','''#!/bin/sh
read p <"$STATE_DIR/wan-watchdog.pid"
kill -TERM "$p"
/bin/sleep 0.1
printf '%s\\n' '{"fresh":true,"routing_error":0,"wan":[{"eligible":1},{"eligible":1}]}'
''')
        r=self.install();self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
        token=(self.base/'rollback/latest').read_text().strip()
        self.assertTrue((self.base/'rollback'/token/'armed').exists())
        self.assertFalse((self.base/'rollback'/token/'committed').exists())

    def test_guard_restores_an_armed_transaction(self):
        self.start_old(FAILOVER_MODE='pinned');self.assertEqual(self.install().returncode,0)
        token=(self.base/'rollback/latest').read_text().strip();transaction=self.base/'rollback'/token
        (transaction/'armed').touch()
        r=self.rollback('--guard',token);self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertTrue((transaction/'guard-claimed').exists());self.assertTrue((transaction/'restored').exists())
        self.assertEqual((self.run/'started-policy').read_text(),'pinned 88')

    def test_interrupted_rollback_does_not_leave_a_permanent_mutex(self):
        self.start_old();self.assertEqual(self.install().returncode,0)
        (self.run/'fail-start').touch()
        p=subprocess.Popen(['sh',str(self.base/'rollback.sh'),'--rollback'],env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.processes.append(p)
        end=time.monotonic()+3
        while (self.run/'status').exists() and time.monotonic()<end:time.sleep(.01)
        p.terminate();p.wait(timeout=2);time.sleep(.1)
        r=self.rollback();self.assertNotEqual(r.returncode,0)
        self.assertNotIn('another rollback is running',r.stderr)

if __name__=='__main__':unittest.main()
