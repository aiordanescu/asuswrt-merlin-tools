#!/usr/bin/env python3
"""Read-only shadow qualification; production watchdog remains the only owner."""
import argparse
import datetime
import json
import re
from pathlib import Path
import shlex
import subprocess
import time

from router_ssh import MUX

def ssh(command,body=None):
    r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',*MUX,'router',command],
                     input=body,text=True,capture_output=True,timeout=15)
    if r.returncode:raise RuntimeError((command,r.stdout,r.stderr))
    return r.stdout

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--evidence',type=Path,required=True)
    ap.add_argument('--seconds',type=int,default=70)
    a=ap.parse_args();assert 40<=a.seconds<=120
    a.evidence.mkdir(parents=True,exist_ok=True)
    root='/tmp/ww-shadow-'+datetime.datetime.now().strftime('%H%M%S')
    source=Path(__file__).resolve().parents[1]
    ssh('umask 077; mkdir '+root+'; mkdir '+root+'/run')
    for f,target in [(source/'build/wan-watchdog','monitor'),(source/'build/wan-healthctl','healthctl'),
                     (source/'lib/ww-adapter.sh','adapter'),(source/'wan-watchdog-legacy.sh','legacy')]:
        with f.open('rb') as data:subprocess.run(['ssh',*MUX,'router','cat >'+root+'/'+target],stdin=data,check=True)
    ssh('chmod 700 '+root+'/monitor '+root+'/healthctl')
    prodpid=ssh('cat /tmp/wan-watchdog/wan-watchdog.pid').strip()
    signatures='md5sum /jffs/scripts/wan-watchdog /jffs/scripts/wan-watchdog-ddns /jffs/scripts/firewall-start /jffs/scripts/services-start'
    before=ssh(signatures);rules_before=ssh('iptables-save')
    (a.evidence/'firewall.before').write_text(rules_before)
    def normalized_rules(text):
        return [re.sub(r'\[\d+:\d+\]','[0:0]',line) for line in text.splitlines()
                if line and not line.startswith('#')]
    guard=f'''#!/bin/sh
sleep 160
if [ -r {root}/run/wan-watchdog.pid ]; then
 read p <{root}/run/wan-watchdog.pid
 case "$p" in ''|*[!0-9]*) exit 0;; esac
 first=$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)
 [ "$first" != {root}/monitor ] || kill -TERM "$p"
fi
'''
    ssh('cat >'+root+'/guard',guard)
    ssh(f'nohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\necho $! >{root}/guard.pid\nnohup {root}/monitor --shadow --run-dir {root}/run --adapter {root}/adapter --legacy {root}/legacy --duration {a.seconds+5} </dev/null >{root}/monitor.log 2>&1 &')
    result={'root':root,'production_pid':prodpid}
    try:
        end=time.monotonic()+10
        while time.monotonic()<end:
            p=ssh(f'cat {root}/run/wan-watchdog.pid 2>/dev/null || true').strip()
            if p:break
            time.sleep(.2)
        else:raise AssertionError('monitor did not start')
        hz=int(ssh(root+'/monitor --system-info').split('=')[1])
        def sample():
            data=ssh(f'cat /proc/uptime; cat /proc/{p}/stat; cat /proc/{prodpid}/stat')
            uptime,monitor,legacy=data.splitlines();rows=[]
            for row in [monitor,legacy]:
                fields=row[row.rindex(')')+2:].split()
                rows.append({'cpu_ticks':sum(int(fields[i]) for i in [11,12,13,14]),'rss_pages':int(fields[21])})
            return {'uptime':float(uptime.split()[0]),'monitor':rows[0],'legacy':rows[1]}
        first=sample();print(json.dumps({'started':root,'sample':first}),flush=True)
        start=time.monotonic()
        while time.monotonic()-start<a.seconds:
            time.sleep(max(.01,min(15,a.seconds-(time.monotonic()-start))))
            print(json.dumps({'elapsed_s':round(time.monotonic()-start),'status':ssh('cat '+root+'/run/status')}),flush=True)
        last=sample();elapsed=last['uptime']-first['uptime']
        result.update(first=first,last=last,seconds=elapsed,clk_tck=hz)
        for name in ['monitor','legacy']:
            result[name+'_core_percent']=100*(last[name]['cpu_ticks']-first[name]['cpu_ticks'])/hz/elapsed
        result['health']=json.loads(ssh(root+'/healthctl --socket '+root+'/run/health.sock --count 1 --timeout 5'))
        assert all(w['eligible'] for w in result['health']['wan']),result
        assert before==ssh(signatures),'production scripts changed'
        rules_after=ssh('iptables-save');(a.evidence/'firewall.after').write_text(rules_after)
        assert normalized_rules(rules_before)==normalized_rules(rules_after),'firewall rules changed'
        assert prodpid==ssh('cat /tmp/wan-watchdog/wan-watchdog.pid').strip(),'production watchdog restarted'
        result['production_preserved']=True
        print(json.dumps(result),flush=True)
    finally:
        for name,command in [('status','cat '+root+'/run/status'),('monitor.log','cat '+root+'/monitor.log'),('production.status','cat /tmp/wan-watchdog/status')]:
            try:(a.evidence/name).write_text(ssh(command))
            except Exception:pass
        ssh(f'''if [ -r {root}/run/wan-watchdog.pid ]; then
 read p <{root}/run/wan-watchdog.pid
 first=$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)
 [ "$first" != {root}/monitor ] || kill -TERM "$p"
fi
read p <{root}/guard.pid
first=$(tr '\\000' ' ' </proc/$p/cmdline 2>/dev/null)
case "$first" in *'{root}/guard'*) kill "$p";; esac''')
        time.sleep(.5)
        (a.evidence/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        ssh('rm -rf '+shlex.quote(root))

if __name__=='__main__':main()
