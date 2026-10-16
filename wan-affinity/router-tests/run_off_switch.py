#!/usr/bin/env python3
"""Controller off-switch regressions with a loopback-only source on the LAN jump.

The source cannot match household LAN traffic. Only our private chain, queue,
runtime and helper are leased. An independent guard restores those objects.
"""
import argparse, datetime, json, re, subprocess, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'wan-watchdog/router-tests'))
from router_ssh import MUX

def ssh(cmd,body=None):
    p=subprocess.run(['ssh',*MUX,'-o','BatchMode=yes','router',cmd],input=body,text=True,capture_output=True,timeout=20)
    if p.returncode:raise RuntimeError((cmd,p.stdout,p.stderr))
    return p.stdout
def normalized(text):return [re.sub(r'\[\d+:\d+\]','[0:0]',s) for s in text.splitlines() if s and not s.startswith('#')]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True);a=ap.parse_args();a.evidence.mkdir(parents=True,exist_ok=True)
    source=Path(__file__).resolve().parents[1];root='/tmp/wa-off-'+datetime.datetime.now().strftime('%H%M%S');chain='WA_OFF_'+root[-6:]
    env=f'PATH={root}/bin:$PATH WA_BASE={root} WA_RUN={root}/run WA_CHAIN={chain}'
    before=ssh('iptables-save');rules=ssh('ip rule show');prod=ssh('cat /tmp/wan-affinity/wan-affinity.pid;cat /tmp/wan-watchdog/wan-watchdog.pid')
    assert not ssh("awk '$1==20893 {print}' /proc/net/netfilter/nfnetlink_queue").strip()
    ssh(f'if iptables -t mangle -n -L {chain} >/dev/null 2>&1;then exit 1;fi;umask 077;mkdir {root} {root}/run {root}/bin')
    nvram=ssh('which nvram').strip();assert re.fullmatch(r'/[A-Za-z0-9_./-]+',nvram),nvram
    # Only the fixture sees this synthetic LAN. It forces the real controller's
    # admission jump onto lo while keeping the production helper unchanged.
    shim=f'''#!/bin/sh
case "$1:$2" in get:lan_ifname) echo lo;;get:lan_ipaddr) echo 127.88.0.1;;get:lan_netmask) echo 255.255.255.0;;*) exec {nvram} "$@";;esac
'''
    ssh('cat >'+root+'/bin/nvram',shim);ssh('chmod 700 '+root+'/bin/nvram')
    for file,path in [('wan-affinity',source/'build/wan-affinity'),('wan-affinity.sh',source/'wan-affinity.sh')]:
        with path.open('rb') as data:subprocess.run(['ssh',*MUX,'router','cat >'+root+'/'+file],stdin=data,check=True)
    ssh('chmod 700 '+root+'/wan-affinity '+root+'/wan-affinity.sh')
    config='ENABLED=1\nMODE=sticky\nCLIENT=127.88.0.2\nLAN_SCOPE=0\nSUPERVISE=0\nQUEUE_NUM=20893\nHEALTH_SOURCE=watchdog\nIDLE_SECONDS=0\n'
    ssh('cat >'+root+'/guard',f'#!/bin/sh\nsleep 120\n[ ! -f {root}/armed ] || {env} sh {root}/wan-affinity.sh stop\n')
    ssh(f'touch {root}/armed;nohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\necho $! >{root}/guard.pid')
    result={}
    try:
        for name,contents,command in [('invalid-value','ENABLED=1\nMODE=Sticky\n','disable'),('invalid-syntax',"ENABLED=1\nMODE='broken\n",'disable'),('missing-config',None,'disable'),('disabled-broken',"ENABLED=0\nMODE='broken\n",'reconcile')]:
            ssh('cat >'+root+'/wan-affinity.conf',config)
            ssh(f'{env} sh {root}/wan-affinity.sh start')
            if contents is None:ssh('rm '+root+'/wan-affinity.conf')
            else:ssh('cat >'+root+'/wan-affinity.conf',contents)
            result[name]=ssh(f"{env} SSH_CONNECTION='127.88.0.2 1234 192.168.1.1 22' sh {root}/wan-affinity.sh {command}")
            assert not ssh(f'iptables -t mangle -S PREROUTING | awk \'$NF=="{chain}" {{print}}\'').strip()
            assert not ssh("awk '$1==20893 {print}' /proc/net/netfilter/nfnetlink_queue").strip()
            print('Passed actual router off switch: '+name,flush=True)
        assert normalized(before)==normalized(ssh('iptables-save'));assert rules==ssh('ip rule show')
        assert prod==ssh('cat /tmp/wan-affinity/wan-affinity.pid;cat /tmp/wan-watchdog/wan-watchdog.pid')
        result['production_preserved']=True
    finally:
        for name in ['wan-affinity.log','status']:
            (a.evidence/name).write_text(ssh('cat '+root+'/run/'+name+' 2>/dev/null || true'))
        ssh(f'{env} sh {root}/wan-affinity.sh stop;rm -f {root}/armed')
        ssh(f'''read p <{root}/guard.pid
case "$(tr '\\000' ' ' </proc/$p/cmdline 2>/dev/null)" in *'{root}/guard'*) kill "$p" 2>/dev/null || true;;esac''')
        (a.evidence/'results.json').write_text(json.dumps(result,indent=2)+'\n');ssh('rm -rf '+root)

if __name__=='__main__':main()
