"""Upgrade/rollback policy with real scripts, private hooks, and no networking."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
CONTROL='''#!/bin/sh
case "$1" in
 stop) mkdir -p "$WA_RUN";touch "$WA_RUN/stopped";;
 start) rm -f "$WA_RUN/stopped";;
esac
printf '%s\\n' "$1" >> "$WA_RUN/events"
'''

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='wa-install-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.base=self.root/'base';self.run=self.root/'run';self.scripts=self.root/'scripts';self.source=self.root/'source';self.bin=self.root/'bin';self.proc=self.root/'proc/self'
        for d in [self.base,self.run,self.scripts,self.source,self.bin,self.proc]:d.mkdir(parents=True)
        (self.proc/'status').write_text('Uid: 0 0 0 0\n')
        self.env=dict(os.environ,WA_BASE=str(self.base),WA_RUN=str(self.run),WA_SCRIPTS=str(self.scripts),WA_PROC=str(self.proc.parent),WA_HOOK_LOCK=str(self.root/'hooks.lock'),WA_INSTALL_LOCK=str(self.root/'install.lock'),PATH=str(self.bin)+os.pathsep+os.environ['PATH'])
        self.write(self.bin/'uname','#!/bin/sh\necho aarch64\n');self.write(self.bin/'nvram','#!/bin/sh\necho lb\n')
        self.write(self.bin/'flock','#!/usr/bin/env python3\nimport fcntl,sys\nfcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX)\n')
        for name in ['install.sh','rollback.sh','hooks.sh','wan-affinity.conf.example']:shutil.copyfile(ROOT/name,self.source/name)
        self.write(self.source/'wan-affinity','#!/bin/sh\nexit 0\n');self.write(self.source/'wan-affinity.sh',CONTROL)
        for name in ['wan-affinity','wan-affinity.sh','hooks.sh','rollback.sh']:shutil.copyfile(self.source/name,self.base/name);(self.base/name).chmod(0o700)
        (self.base/'wan-affinity.conf').write_text('ENABLED=1\n')
        for name in ['services-start','firewall-start']:(self.scripts/name).write_text('#!/bin/sh\necho user-hook\n')

    def write(self,p,s):p.write_text(s);p.chmod(0o700)
    def install(self):return subprocess.run(['sh',str(self.source/'install.sh'),'--upgrade'],env=self.env,capture_output=True,text=True,timeout=10)
    def rollback(self):return subprocess.run(['sh',str(self.base/'rollback.sh'),'--rollback'],env=self.env,capture_output=True,text=True,timeout=10)

    def test_enabled_spellings_resume_even_without_a_helper_pid(self):
        for value in ['ENABLED=1 # on','ENABLED="1"',"export ENABLED='1'",' ENABLED=1','ENABLED=1  ']:
            with self.subTest(value=value):
                (self.base/'wan-affinity.conf').write_text(value+'\n')
                p=self.install();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertFalse((self.run/'stopped').exists())
                p=self.rollback();self.assertEqual(p.returncode,0,p.stdout+p.stderr)
                self.assertFalse((self.run/'stopped').exists())

    def test_manual_stop_is_retained(self):
        (self.run/'stopped').touch();p=self.install();self.assertEqual(p.returncode,0,p.stderr)
        self.assertTrue((self.run/'stopped').exists());self.assertEqual((self.run/'events').read_text(),'stop\n')

    def test_refused_second_hook_leaves_service_and_both_hooks_untouched(self):
        (self.scripts/'firewall-start').write_text('#!/usr/bin/python3\nprint(1)\n')
        before={p.name:p.read_bytes() for p in self.scripts.iterdir()}
        p=self.install();self.assertNotEqual(p.returncode,0);self.assertIn('unsupported hook',p.stderr)
        self.assertFalse((self.run/'events').exists());self.assertFalse((self.base/'previous').exists())
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.scripts.iterdir()})

    def test_rollback_restarts_service_even_if_hook_is_now_refused(self):
        self.assertEqual(self.install().returncode,0)
        (self.scripts/'services-start').write_text('#!/usr/bin/python3\nprint(1)\n')
        p=self.rollback();self.assertNotEqual(p.returncode,0)
        self.assertIn('enabled service restarted',p.stdout);self.assertIn('hooks need attention',p.stderr)
        self.assertFalse((self.run/'stopped').exists());self.assertEqual((self.run/'events').read_text().splitlines()[-2:],['stop','start'])

    def test_hook_check_is_read_only_and_leaves_no_staging_files(self):
        before={p.name:p.read_bytes() for p in self.scripts.iterdir()}
        p=subprocess.run(['sh',str(self.source/'hooks.sh'),'check'],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(before,{p.name:p.read_bytes() for p in self.scripts.iterdir()})

    def test_busybox_shell_hook_preserves_original_interpreter(self):
        busybox=self.bin/'busybox';self.write(busybox,'#!/bin/sh\nshift\nexec sh "$@"\n')
        body='#!'+str(busybox)+' sh\necho original\n';(self.scripts/'services-start').write_text(body)
        for action in ['install','remove']:
            p=subprocess.run(['sh',str(self.source/'hooks.sh'),action],env=self.env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual((self.scripts/'services-start').read_text(),body)

if __name__=='__main__':unittest.main()
