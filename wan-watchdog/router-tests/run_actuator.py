#!/usr/bin/env python3
"""Exercise the real actuator in private tables with a loopback-only source.

Does not disable either WAN, edit NVRAM or send forwarding probes. All real
LAN selectors and VPN guards are excluded from this isolated test namespace.
"""
import argparse
import datetime
import json
from pathlib import Path
import re
import subprocess
import time

from router_ssh import MUX
def ssh(code):
    r=subprocess.run(['ssh','-o','BatchMode=yes',*MUX,'router','sh -s'],input=code,
                     text=True,capture_output=True,timeout=25)
    if r.returncode:raise RuntimeError((r.returncode,r.stdout,r.stderr))
    return r.stdout

def main():
    p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True)
    a=p.parse_args();a.evidence.mkdir(parents=True,exist_ok=True)
    root='/tmp/ww-act-'+datetime.datetime.now().strftime('%H%M%S')
    src=Path(__file__).resolve().parents[1]
    before=ssh('ip -4 rule show\n');pid=ssh('cat /tmp/wan-watchdog/wan-watchdog.pid\n')
    assert not re.search(r'^(58|59|60):|3015[89]|30160|127\.88\.0\.2',before,re.M),before
    assert not ssh('for t in 30158 30159 30160;do ip -4 route show table "$t";done\n').strip()
    ssh('umask 077\nmkdir '+root+'\nmkdir '+root+'/run '+root+'/owner\n')
    for local,name in [(src/'wan-watchdog-legacy.sh','legacy'),(src/'lib/ww-adapter.sh','adapter')]:
        with local.open('rb') as data:subprocess.run(['ssh',*MUX,'router','cat >'+root+'/'+name],stdin=data,check=True)
    wrapper=f'''. {root}/legacy
PREFERRED_TARGET_A=198.18.0.100
PREFERRED_TARGET_B=198.18.0.101
vpn_guards() {{ :; }}
configured_ips_for_unit() {{ [ "$1" != 0 ] || echo 127.88.0.2; }}
'''
    ssh('cat >'+root+'/reference <<\'EOF\'\n'+wrapper+'EOF\n')
    env=f'STATE_DIR={root}/run OWNERSHIP_DIR={root}/owner WW_LEGACY={root}/reference WW_SHADOW=0 FAILOVER_PRIORITY=60 FAILOVER_TABLE=30160 PROBE_TABLE0=30158 PROBE_TABLE1=30159 LAN_NETWORKS=127.88.0.2/32 DNS_SERVERS=198.18.0.53 DDNS_HELPER=/nonexistent WW_SAMPLE_SECONDS=100 '
    def call(mode,phase,args=''):return ssh(env+'FAILOVER_MODE='+mode+' sh '+root+'/adapter '+phase+' '+args+'\n')
    cleanup=f'''#!/bin/sh
{env}FAILOVER_MODE=whole_lan sh {root}/adapter cleanup
while ip -4 rule del priority 100 from 127.88.0.2/32 lookup wan0 2>/dev/null;do :;done
while ip -4 rule del priority 100 from 127.88.0.2/32 lookup wan1 2>/dev/null;do :;done
'''
    ssh('cat >'+root+'/cleanup <<\'EOF\'\n'+cleanup+'EOF\ncat >'+root+'/guard <<\'EOF\'\n#!/bin/sh\nsleep 150\nsh '+root+'/cleanup\nEOF\nnohup sh '+root+'/guard </dev/null >'+root+'/guard.log 2>&1 &\necho $! >'+root+'/guard.pid\n')
    results={}
    try:
        call('whole_lan','init')
        ssh('echo "1 0 0 unreachable 100 100 15 15" >'+root+'/run/health_wan0\necho "0 1 0 usable 0 0 15 15" >'+root+'/run/health_wan1\n')
        call('whole_lan','apply','0 0 1 1 1 0 1 0')
        rules=ssh('ip -4 rule show\n');routes=ssh('ip -4 route show table 30160\n')
        assert '60:\tfrom 127.88.0.2 lookup 30160' in rules,rules
        iface=ssh('nvram get wan1_ifname\n').strip();assert 'dev '+iface in routes,routes
        results['whole_failover']={'rules':rules,'routes':routes}
        call('whole_lan','apply','-1 1 0 1 0 0 1 0')
        assert not re.search(r'^60:',ssh('ip -4 rule show\n'),re.M)
        results['whole_restore']=True
        call('pinned','init')
        call('pinned','apply','0 0 1 1 1 0 1 0')
        assert '100:\tfrom 127.88.0.2 lookup wan1' in ssh('ip -4 rule show\n')
        results['pinned_failover']=True
        call('pinned','apply','-1 0 0 1 0 0 1 0')
        assert '100:\tfrom 127.88.0.2 lookup wan0' in ssh('ip -4 rule show\n')
        results['pinned_restore']=True
    finally:
        ssh('sh '+root+'/cleanup\n')
        after=ssh('ip -4 rule show\n')
        results['production_rules_preserved']=sorted(before.splitlines())==sorted(after.splitlines())
        results['production_pid_preserved']=pid==ssh('cat /tmp/wan-watchdog/wan-watchdog.pid\n')
        results['private_tables_empty']=not ssh('for t in 30158 30159 30160;do ip -4 route show table "$t";done\n').strip()
        (a.evidence/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        ssh(f'read p <{root}/guard.pid\ncase "$(tr \'\\000\' \' \' </proc/$p/cmdline 2>/dev/null)" in *\'{root}/guard\'*) kill "$p";;esac\n')
        time.sleep(.3);ssh('rm -rf '+root+'\n')
        assert all(results[k] for k in ['production_rules_preserved','production_pid_preserved','private_tables_empty']),results
    print(json.dumps(results),flush=True)

if __name__=='__main__':main()
