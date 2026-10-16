#!/usr/bin/env python3
"""Exercise real install/rollback on a private C service with a file-only adapter."""
import argparse, datetime, hashlib, json, re, subprocess, time
from pathlib import Path
from router_ssh import MUX

def ssh(command,body=None,timeout=15):
    p=subprocess.run(['ssh',*MUX,'-o','BatchMode=yes','router',command],input=body,capture_output=True,text=isinstance(body,str) or body is None,timeout=timeout)
    if p.returncode:raise RuntimeError((command,p.stdout,p.stderr))
    return p.stdout
def normalized(text):return [re.sub(r'\[\d+:\d+\]','[0:0]',s) for s in text.splitlines() if s and not s.startswith('#')]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--previous',type=Path,required=True);ap.add_argument('--candidate',type=Path,required=True);ap.add_argument('--evidence',type=Path,required=True);a=ap.parse_args();a.evidence.mkdir(parents=True,exist_ok=True)
    root='/tmp/ww-handoff-'+datetime.datetime.now().strftime('%H%M%S');base=root+'/service';run=root+'/run';owner=root+'/owner';entry=root+'/entry'
    env=f'WW_BASE={base} STATE_DIR={run} OWNERSHIP_DIR={owner} WW_ENTRY={entry}'
    production=ssh('cat /tmp/wan-watchdog/wan-watchdog.pid;cat /tmp/wan-affinity/wan-affinity.pid;md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start /jffs/scripts/wan-watchdog')
    firewall=ssh('iptables-save');rules=ssh('ip rule show');result={}
    ssh(f'umask 077;mkdir {root} {base} {run} {owner} {root}/candidate')
    for file,name in [(a.previous,'previous'),(a.candidate,'candidate')]:
        with file.open('rb') as data:subprocess.run(['ssh',*MUX,'router',f'cat >{root}/{name}.tar.gz'],stdin=data,check=True)
    ssh(f'tar -xzf {root}/previous.tar.gz -C {base};tar -xzf {root}/candidate.tar.gz -C {root}/candidate;cp {base}/wan-watchdog.sh {entry};echo pinned >{owner}/mode')
    adapter='''#!/bin/sh
printf '%s %s\\n' "$FAILOVER_MODE" "$FAILOVER_PRIORITY" >"$STATE_DIR/policy"
printf 'R -1\\nF 0 1 lo 127.0.0.1 127.0.0.1 0 1 1 127.0.0.1 127.0.0.2\\nF 1 1 lo 127.0.0.1 127.0.0.1 0 1 1 127.0.0.1 127.0.0.2\\nEND\\n'
'''
    for path in [base+'/lib/ww-adapter.sh',root+'/candidate/lib/ww-adapter.sh']:ssh('cat >'+path,adapter)
    stop=f'''#!/bin/sh
if [ -r {run}/wan-watchdog.pid ];then
 read p <{run}/wan-watchdog.pid
 first=$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)
 [ "$first" != {base}/wan-watchdog ] || kill -TERM "$p"
fi
'''
    ssh('cat >'+root+'/stop',stop)
    ssh('cat >'+root+'/guard',f'#!/bin/sh\nsleep 240\n[ ! -f {root}/armed ] || sh {root}/stop\n')
    ssh(f'touch {root}/armed;nohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\necho $! >{root}/guard.pid')
    def health():return json.loads(ssh(f'{base}/wan-healthctl --socket {run}/health.sock --timeout 2'))
    def ready(timeout):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            s=health()
            if all(w['eligible'] for w in s['wan']) and not s['routing_error']:return s
            time.sleep(1)
        raise AssertionError(s)
    try:
        ssh(f'{env} FAILOVER_MODE=pinned FAILOVER_PRIORITY=78 sh {entry} start',timeout=15)
        result['baseline']=ready(40);print('Private previous service qualified.',flush=True)
        result['install_output']=ssh(f'{env} sh {root}/candidate/install.sh --upgrade',timeout=100)
        assert ssh('cat '+run+'/policy').strip()=='pinned 78'
        assert ssh('md5sum '+base+'/wan-watchdog').split()[0]==hashlib.md5((Path(__file__).resolve().parents[1]/'build/wan-watchdog').read_bytes()).hexdigest()
        result['upgraded']=health();result['captured_policy']=ssh('cat '+owner+'/service.conf');print('Actual router upgrade preserved pinned mode and priority 78.',flush=True)
        result['rollback_output']=ssh(f'{env} sh {base}/rollback.sh --rollback',timeout=90)
        assert ssh('cat '+run+'/policy').strip()=='pinned 78'
        result['rolled_back']=health();print('Actual router rollback qualified the previous monitor and preserved policy.',flush=True)
        assert production==ssh('cat /tmp/wan-watchdog/wan-watchdog.pid;cat /tmp/wan-affinity/wan-affinity.pid;md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start /jffs/scripts/wan-watchdog')
        assert normalized(firewall)==normalized(ssh('iptables-save'));assert rules==ssh('ip rule show')
        result['production_preserved']=True
    finally:
        for name,command in [('status','cat '+run+'/status'),('monitor.log','cat '+run+'/monitor.log'),('rollback.log','cat '+run+'/rollback.log')]:
            try:(a.evidence/name).write_text(ssh(command+' 2>/dev/null || true'))
            except Exception:pass
        ssh('sh '+root+'/stop;rm -f '+root+'/armed')
        ssh(f'''for f in {root}/guard.pid {base}/rollback/*/guard.pid;do
 [ -r "$f" ] || continue
 read p <"$f"
 case "$(tr '\\000' ' ' </proc/$p/cmdline 2>/dev/null)" in *'{root}/'*) kill "$p" 2>/dev/null || true;;esac
done''')
        (a.evidence/'results.json').write_text(json.dumps(result,indent=2)+'\n');ssh('rm -rf '+root)
    print(json.dumps({'passed':list(result)},indent=2))

if __name__=='__main__':main()
