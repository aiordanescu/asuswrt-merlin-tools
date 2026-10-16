"""Identical synthetic traces through the actual shell and portable C core."""
import os
from pathlib import Path
import random
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]

class Parity(unittest.TestCase):
    def test_classification_trace(self):
        rng=random.Random(20880)
        rows=[]
        for t in range(1,1000,2):
            for u in (0,1):
                a=b=1;enabled=1;active=int(t>=93)
                if 35<=t<51 or 313<=t<335:a=b=0
                if 225<=t<235:a=-1
                if 401<=t<451:a=0
                if 501<=t<521:enabled=0;a=b=-1
                if t>755:a=rng.choice([1,1,1,0,-1]);b=rng.choice([1,1,1,0])
                rows.append((t,u,enabled,active,a,b,int(t in (1,541))))
        trace=''.join(' '.join(map(str,r))+'\n' for r in rows)
        monitor=subprocess.run([str(ROOT/'build/replay')],input=trace,text=True,capture_output=True,check=True).stdout.splitlines()
        with tempfile.TemporaryDirectory() as d:
            commands=['. "$WATCHDOG_SOURCE"','mkdir -p "$STATE_DIR"']
            for t,u,en,active,a,b,reset in rows:
                if reset:
                    commands += [f'rm -f "$STATE_DIR/samples_{u}_a" "$STATE_DIR/samples_{u}_b" "$STATE_DIR/down_wan{u}" "$STATE_DIR/recovery_wan{u}"',f'echo {t} >"$STATE_DIR/window_start_wan{u}"']
                commands += [f'NOW={t}',f'echo {en} >"$STATE_DIR/enabled_wan{u}"',f'echo {a} >"$STATE_DIR/probe_{u}_a"',f'echo {b} >"$STATE_DIR/probe_{u}_b"',
                    f'{"touch" if active else "rm -f"} "$STATE_DIR/active_wan{u}"',
                    f'record_sample "$STATE_DIR/samples_{u}_a" {a}',f'record_sample "$STATE_DIR/samples_{u}_b" {b}',f'classify_wan {u}',f'cat "$STATE_DIR/health_wan{u}"']
            env=dict(os.environ,WAN_WATCHDOG_LIBRARY_ONLY='1',WATCHDOG_SOURCE=str(ROOT/'wan-watchdog-legacy.sh'),STATE_DIR=d,DDNS_HELPER='/nonexistent')
            shell=subprocess.run(['sh'],input='\n'.join(commands)+'\n',env=env,text=True,capture_output=True,check=True).stdout.splitlines()
        self.assertEqual(len(monitor),len(shell))
        for row,n,s in zip(rows,monitor,shell):self.assertEqual(n,s,f'trace row {row}')

if __name__=='__main__':unittest.main()
