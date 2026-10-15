#!/usr/bin/env python3
"""Real-clock native daemon/ICMP tests with a FILE-ONLY routing actuator.

All probe sources/targets are loopback; the actuator never invokes ip/nvram.
The production router service and rules remain untouched.
"""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import time
from router_ssh import MUX

def ssh(cmd,body=None):
    p=subprocess.run(['ssh','-o','BatchMode=yes',*MUX,'router',cmd],input=body,text=True,capture_output=True,timeout=12)
    if p.returncode:raise RuntimeError((cmd,p.stdout,p.stderr))
    return p.stdout

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True)
    ap.add_argument('--mode',choices=['whole_lan','pinned'],default='whole_lan')
    ap.add_argument('--reversal',action='store_true');a=ap.parse_args()
    assert not a.reversal or a.mode=='whole_lan'
    a.evidence.mkdir(parents=True,exist_ok=True)
    root='/tmp/ww-orch-'+datetime.datetime.now().strftime('%H%M%S')+('-rev' if a.reversal else '-'+a.mode)
    source=Path(__file__).resolve().parents[1]
    ssh('umask 077; mkdir '+root+'; mkdir '+root+'/run '+root+'/owner; touch '+root+'/legacy; echo 1 >'+root+'/enable0; echo 1 >'+root+'/enable1')
    for local,target in [(source/'build/wan-watchdog-native','native'),(source/'build/wan-healthctl','healthctl')]:
        with local.open('rb') as data:subprocess.run(['ssh',*MUX,'router','cat >'+root+'/'+target],stdin=data,check=True)
    script='''#!/bin/sh
set -u
r=ROOT
phase=$1
[ ! -f "$r/hang" ] || sleep 10
if [ "$phase" = apply ];then
 shift
 wanted=$1;restore=$2;p0=$3;p1=$4;a0=$5;a1=$6;m0=$7;m1=$8
 if [ "$FAILOVER_MODE" = whole_lan ];then
  if [ "$restore" = 1 ];then
   [ ! -f "$r/fail-restore" ] || exit 73
   echo -1 >"$r/failed";rm -f "$r/active0" "$r/active1"
  elif [ "$wanted" != -1 ];then
   echo "$wanted" >"$r/failed";rm -f "$r/active0" "$r/active1";touch "$r/active$wanted"
  fi
 else
  for u in 0 1;do
   if [ "$u" = 0 ];then active=$a0;mask=$m0;else active=$a1;mask=$m1;fi
   [ "$mask" = 1 ] || continue
   if [ "$active" = 1 ];then touch "$r/active$u"
   else
    if [ -f "$r/active$u" ] && [ -f "$r/fail-restore" ];then exit 73;fi
    rm -f "$r/active$u"
   fi
  done
 fi
fi
failed=-1;[ ! -f "$r/failed" ] || read failed <"$r/failed"
printf 'R %s\n' "$failed"
for u in 0 1;do
 read en <"$r/enable$u";active=0;[ ! -f "$r/active$u" ] || active=1
 printf 'F %s %s lo 127.0.0.1 127.0.0.1 %s 1 1 127.0.0.1 127.0.0.2\n' "$u" "$en" "$active"
done
printf 'END\n'
'''.replace('ROOT',root)
    ssh('cat >'+root+'/adapter',script)
    ssh('chmod 700 '+root+'/native '+root+'/healthctl; sh -n '+root+'/adapter')
    prod=ssh('cat /tmp/wan-watchdog/wan-watchdog.pid')
    ssh(f'nohup {root}/native --active --run-dir {root}/run --ownership {root}/owner --adapter {root}/adapter --legacy {root}/legacy --mode {a.mode} --duration 360 </dev/null >{root}/native.log 2>&1 &')
    results={};start=time.monotonic()
    def state():return json.loads(ssh(root+'/healthctl --socket '+root+'/run/health.sock --count 1 --timeout 4'))
    def wait(name,predicate,timeout):
        end=time.monotonic()+timeout;last_print=0
        while time.monotonic()<end:
            s=state()
            if predicate(s):
                results[name]={'elapsed_s':round(time.monotonic()-start,3),'state':s}
                (a.evidence/'results.json').write_text(json.dumps(results,indent=2)+'\n')
                print(json.dumps({'phase':name,'elapsed_s':results[name]['elapsed_s'],'state':s}),flush=True);return s
            if time.monotonic()-last_print>15:
                print(json.dumps({'waiting':name,'elapsed_s':round(time.monotonic()-start)}),flush=True);last_print=time.monotonic()
            time.sleep(1)
        raise AssertionError((name,s))
    try:
        wait('warmed',lambda s:all(w['eligible'] for w in s['wan']),40)
        ssh('echo 0 >'+root+'/enable0')
        wait('failed',lambda s:s['wan'][0]['bad'] and not s['wan'][0]['eligible'] and s['wan'][1]['eligible'] and (a.mode=='pinned' or s['applied']==0),10)
        ssh('echo 1 >'+root+'/enable0')
        wait('clean_but_held',lambda s:s['wan'][0]['acceptable'] and not s['wan'][0]['eligible'],40)
        if a.reversal:
            ssh('echo 0 >'+root+'/enable1')
            wait('reversed_before_regular_hold',lambda s:s['applied']==1 and s['wan'][0]['eligible'] and s['wan'][1]['bad'],10)
        else:
            ssh('touch '+root+'/fail-restore')
            wait('failed_restoration_stays_held',lambda s:s['routing_error']==1 and s['wan'][0]['acceptable'] and not s['wan'][0]['eligible'],135)
            ssh('rm '+root+'/fail-restore')
            wait('restored_after_ack',lambda s:s['wan'][0]['eligible'] and not s['routing_error'] and (a.mode=='pinned' or s['applied']==-1),12)
            old=state()
            ssh(f'kill -STOP $(cat {root}/run/wan-watchdog.pid)')
            time.sleep(6)
            ssh(f'kill -CONT $(cat {root}/run/wan-watchdog.pid)')
            wait('stall_resets_windows',lambda s:all(not w['eligible'] and w['config']>old['wan'][u]['config'] for u,w in enumerate(s['wan'])),5)
            wait('stall_warmup_complete',lambda s:all(w['eligible'] for w in s['wan']),40)
            old=state();ssh('touch '+root+'/hang')
            wait('hung_worker_monitor_continues',lambda s:s['routing_error'] and s['progress']>old['progress']+2 and all(not w['eligible'] for w in s['wan']),18)
            ssh('rm '+root+'/hang')
            wait('worker_recovered',lambda s:all(w['eligible'] for w in s['wan']) and not s['routing_error'],45)
        assert prod==ssh('cat /tmp/wan-watchdog/wan-watchdog.pid')
    finally:
        for name in ['run/status','native.log']:
            try:(a.evidence/name.replace('/','-')).write_text(ssh('cat '+root+'/'+name))
            except Exception:pass
        ssh(f'''if [ -r {root}/run/wan-watchdog.pid ];then
read p <{root}/run/wan-watchdog.pid
first=$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)
[ "$first" != {root}/native ] || kill -TERM "$p"
fi''')
        time.sleep(.5);ssh('rm -rf '+root)

if __name__=='__main__':main()
