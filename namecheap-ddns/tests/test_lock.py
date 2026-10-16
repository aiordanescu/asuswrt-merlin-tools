"""A killed checker must not leave a stale mutex or a child-held flock."""
from pathlib import Path
import os, subprocess, tempfile, time, unittest
SCRIPT=Path(__file__).resolve().parents[1]/'namecheap-ddns-check'
class CheckLock(unittest.TestCase):
    def test_sigkill_releases_lock_even_with_lookup_still_running(self):
        with tempfile.TemporaryDirectory(prefix='nc-check-') as tmp:
            root=Path(tmp);bin=root/'bin';bin.mkdir();state=root/'state';state.mkdir();(state/'lock').mkdir() # Historical stale mkdir lock.
            def tool(name,body):
                p=bin/name;p.write_text(body);p.chmod(0o700)
            tool('nvram','''#!/bin/sh
case "$2" in ddns_enable_x|wan0_primary|ddns_regular_check) echo 1;;ddns_server_x) echo WWW.NAMECHEAP.COM;;wans_mode) echo lb;;ddns_wan_unit) echo 0;;wan0_state_t) echo 2;;wan0_ipaddr) echo 45.1.1.1;;ddns_hostname_x) echo test;;ddns_username_x) echo example.test;;esac
''')
            tool('pidof','#!/bin/sh\nexit 1\n')
            tool('flock','#!/usr/bin/env python3\nimport fcntl,sys\ntry:fcntl.flock(int(sys.argv[-1]),fcntl.LOCK_EX|fcntl.LOCK_NB)\nexcept BlockingIOError:sys.exit(1)\n')
            tool('nslookup','''#!/usr/bin/env python3
import os,time
from pathlib import Path
r=Path(os.environ['NAMECHEAP_DDNS_STATE']);p=r/('pid-'+str(os.getpid()));p.write_text(str(os.getpid()));p.replace(r/'lookup-pid')
while not (r/'release').exists():time.sleep(.01)
print('Name: test.example.test\\nAddress 1: 45.1.1.1')
''')
            env=dict(os.environ,NAMECHEAP_DDNS_STATE=str(state),PATH=str(bin)+os.pathsep+os.environ['PATH'])
            first=subprocess.Popen(['sh',str(SCRIPT),'--check-now'],env=env,start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            second=None
            try:
                end=time.monotonic()+3
                while not (state/'lookup-pid').exists() and time.monotonic()<end:time.sleep(.01)
                self.assertTrue((state/'lookup-pid').exists())
                inode=(state/'check.lock').stat().st_ino;pid=int((state/'lookup-pid').read_text())
                first.kill();first.wait(timeout=2)
                # Keep old lookup alive: it must not inherit descriptor 9.
                second=subprocess.Popen(['sh',str(SCRIPT),'--check-now'],env=env,start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                end=time.monotonic()+3
                while int((state/'lookup-pid').read_text())==pid and time.monotonic()<end:time.sleep(.01)
                self.assertNotEqual(int((state/'lookup-pid').read_text()),pid)
                (state/'release').touch();self.assertEqual(second.wait(timeout=3),0)
                self.assertEqual((state/'check.lock').stat().st_ino,inode)
                self.assertEqual(state.stat().st_mode & 0o777,0o700)
            finally:
                (state/'release').touch()
                for proc in [first,second]:
                    if proc:
                        if proc.poll() is None:proc.kill()
                        proc.wait(timeout=3)
if __name__=='__main__':unittest.main()
