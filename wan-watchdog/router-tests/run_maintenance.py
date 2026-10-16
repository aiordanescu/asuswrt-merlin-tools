#!/usr/bin/env python3
"""Real monitor regressions: loopback ICMP and file-only routing adapters.

No production settings, routes, firewall rules or service processes are changed.
"""
import argparse, datetime, json, re, subprocess, time
from pathlib import Path
from router_ssh import MUX

def ssh(command,body=None):
    p=subprocess.run(['ssh',*MUX,'-o','BatchMode=yes','-o','ConnectTimeout=5','router',command],input=body,text=True,capture_output=True,timeout=15)
    if p.returncode:raise RuntimeError((command,p.stdout,p.stderr))
    return p.stdout

ADAPTER=r'''#!/bin/sh
set -u
r=ROOT
phase=$1;wanted=${2:--1};restore=${3:-0}
case "$phase:${WW_EXPECT0:-}" in apply:0:*)
 if [ -f "$r/pause" ] && mkdir "$r/paused-once" 2>/dev/null;then
  touch "$r/entered"
  while [ ! -f "$r/release" ];do sleep 1;done
 fi;;esac
[ ! -f "$r/hang" ] || sleep 7
if [ -f "$r/slow" ];then sleep 4;fi
read en <"$r/enable0";read addr <"$r/address0"
printf '%s %s %s %s\n' "$phase" "$wanted" "$restore" "${WW_EXPECT0:--}" >>"$r/calls"
if [ "$phase" = apply ];then
 [ "${WW_EXPECT0:-}" = "$en:$addr:127.0.0.1:lo" ] || exit 75
 [ "${WW_EXPECT1:-}" = '1:127.0.0.1:127.0.0.1:lo' ] || exit 75
 set -- $(cat "$STATE_DIR/health_wan1")
 if [ "$restore" = 1 ];then
  rm -f "$r/failed" "$r/active0"
 elif [ "$wanted" = 0 ] && [ "$2" = 1 ];then
  echo 0 >"$r/failed";touch "$r/active0"
  printf '%s\n' "${WW_EXPECT0:-}" >>"$r/acted"
 fi
fi
failed=-1;[ ! -f "$r/failed" ] || read failed <"$r/failed"
active=0;[ ! -f "$r/active0" ] || active=1
printf 'R %s\n' "$failed"
printf 'F 0 %s lo %s 127.0.0.1 %s 1 1 127.0.0.1 127.0.0.2\n' "$en" "$addr" "$active"
printf 'F 1 1 lo 127.0.0.1 127.0.0.1 0 1 1 127.0.0.1 127.0.0.2\nEND\n'
'''

def normalized(text):
    return [re.sub(r'\[\d+:\d+\]','[0:0]',line) for line in text.splitlines() if line and not line.startswith('#')]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True);a=ap.parse_args()
    a.evidence.mkdir(parents=True,exist_ok=True)
    source=Path(__file__).resolve().parents[1]
    root='/tmp/ww-maintenance-'+datetime.datetime.now().strftime('%H%M%S')
    before=ssh('cat /tmp/wan-watchdog/wan-watchdog.pid;cat /tmp/wan-affinity/wan-affinity.pid;md5sum /jffs/scripts/wan-watchdog /jffs/scripts/services-start /jffs/scripts/firewall-start')
    firewall=ssh('iptables-save');rules=ssh('ip rule show')
    ssh('umask 077;mkdir '+root)
    for name in ['wan-watchdog','wan-healthctl']:
        with (source/'build'/name).open('rb') as data:subprocess.run(['ssh',*MUX,'router','cat >'+root+'/'+name],stdin=data,check=True)
    ssh('chmod 700 '+root+'/wan-watchdog '+root+'/wan-healthctl')
    result={};started=time.monotonic();instances={}
    def state(name):return json.loads(ssh(f'{root}/wan-healthctl --socket {instances[name]}/run/health.sock --timeout 3'))
    def eligible(name):
        s=state(name)
        return s if all(w['eligible'] for w in s['wan']) else None
    def wait(label,predicate,timeout):
        end=time.monotonic()+timeout;last_print=0
        while time.monotonic()<end:
            value=predicate()
            if value:
                result[label]=value;print(json.dumps({'passed':label,'elapsed':round(time.monotonic()-started,2)}),flush=True);return value
            if time.monotonic()-last_print>=15:
                print(json.dumps({'waiting':label,'elapsed':round(time.monotonic()-started,2)}),flush=True);last_print=time.monotonic()
            time.sleep(1)
        raise AssertionError(label)
    try:
        for name in ['slow','restart','race']:
            r=root+'/'+name;instances[name]=r
            ssh(f'mkdir {r} {r}/run {r}/owner;touch {r}/legacy;echo 1 >{r}/enable0;echo 127.0.0.1 >{r}/address0')
            ssh('cat >'+r+'/adapter',ADAPTER.replace('ROOT',r))
            if name=='slow':ssh('touch '+r+'/slow')
            if name=='restart':ssh('echo 0 >'+r+'/failed;touch '+r+'/active0')
            mode='--shadow' if name=='slow' else '--active'
            ssh(f'nohup {root}/wan-watchdog {mode} --adapter {r}/adapter --legacy {r}/legacy --run-dir {r}/run --ownership {r}/owner --duration 110 </dev/null >{r}/monitor.log 2>&1 &')
        time.sleep(5)
        wait('restart_unacknowledged',lambda:(s if (s:=state('restart'))['desired']==0 and s['applied']==-1 and s['routing_error']==1 else None),8)
        time.sleep(5)
        s=state('restart');assert s['applied']==-1 and s['routing_error']==1,s
        result['restart_still_unacknowledged']=s
        wait('slow_worker_eligible',lambda:eligible('slow'),45)
        status=ssh('cat '+instances['slow']+'/run/status');assert 'stale_fact_cycles=0' in status,status;result['slow_status']=status
        wait('restart_acknowledged',lambda:(s if (s:=state('restart'))['applied']==0 and not s['routing_error'] else None),12)
        wait('race_warmed',lambda:eligible('race'),8)
        r=instances['race'];ssh('touch '+r+'/pause;echo 0 >'+r+'/enable0')
        wait('old_lease_worker_paused',lambda:ssh('[ ! -f '+r+'/entered ] || echo yes').strip(),8)
        progress=state('race')['progress']
        wait('old_lease_decision_queued',lambda:(s if (s:=state('race'))['progress']>progress else None),3.5)
        ssh('echo 1 >'+r+'/enable0;echo 127.0.0.3 >'+r+'/address0;touch '+r+'/release')
        time.sleep(8)
        s=state('race');acted=ssh('cat '+r+'/acted 2>/dev/null || true')
        assert '1:127.0.0.3:' not in acted,acted
        assert s['applied']==-1,s
        result['pending_decision_discarded']={'state':s,'acted':acted}
        r=instances['slow'];ssh('rm '+r+'/slow;touch '+r+'/hang')
        wait('shadow_failure_reported',lambda:(s if (s:=state('slow'))['routing_error'] else None),12)
        ssh('rm '+r+'/hang')
        wait('shadow_error_cleared',lambda:(s if not (s:=state('slow'))['routing_error'] else None),12)
        assert before==ssh('cat /tmp/wan-watchdog/wan-watchdog.pid;cat /tmp/wan-affinity/wan-affinity.pid;md5sum /jffs/scripts/wan-watchdog /jffs/scripts/services-start /jffs/scripts/firewall-start')
        assert normalized(firewall)==normalized(ssh('iptables-save'))
        assert rules==ssh('ip rule show')
        result['production_preserved']=True
    finally:
        for name,r in instances.items():
            for file in ['calls','acted','run/status','monitor.log']:
                try:(a.evidence/(name+'-'+file.replace('/','-'))).write_text(ssh('cat '+r+'/'+file+' 2>/dev/null || true'))
                except Exception:pass
            ssh(f'''if [ -r {r}/run/wan-watchdog.pid ];then
 read p <{r}/run/wan-watchdog.pid
 first=$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)
 [ "$first" != {root}/wan-watchdog ] || kill -TERM "$p"
fi
''')
        (a.evidence/'results.json').write_text(json.dumps(result,indent=2)+'\n')
        ssh('rm -rf '+root)
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':main()
