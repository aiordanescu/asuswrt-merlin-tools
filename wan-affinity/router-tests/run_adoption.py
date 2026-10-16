#!/usr/bin/env python3
"""Guarded mixed-session adoption test beside the running LAN-wide service.

Only the authorized Mini's selected source-port ranges enter the private queue.
Two real HTTPS connections on different WANs remain alive through admission and
restart. No household queue, service, gateway health or connection is flushed.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import select
import shlex
import subprocess
import sys
import time
import uuid

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'wan-watchdog/router-tests'))
from router_ssh import MUX

def ssh(code,host='router',timeout=25):
    p=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8',*(MUX if host=='router' else []),host,'sh -s'],input=code,text=True,capture_output=True,timeout=timeout)
    if p.returncode:raise RuntimeError((host,p.returncode,p.stdout,p.stderr))
    return p.stdout

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--allow-client-test',action='store_true',required=True)
    ap.add_argument('--evidence',type=Path,required=True)
    a=ap.parse_args();a.evidence.mkdir(parents=True,exist_ok=True)
    root='/tmp/wa-adoption-'+uuid.uuid4().hex[:8];chain='WA_ADOPT_'+root[-8:];queue=20881
    client=ssh("ipconfig getifaddr en0\n",'alexs-mac-mini').strip()
    assert ipaddress.ip_address(client) in ipaddress.ip_network('192.168.1.0/24')
    before={k:ssh(cmd+'\n') for k,cmd in {
        'pins':'nvram get wans_routing_rulelist','rules':'ip -4 rule show',
        'firewall':'iptables-save','watchdog':'cat /tmp/wan-watchdog/wan-watchdog.pid',
        'affinity':'cat /tmp/wan-affinity/wan-affinity.pid',
        'hooks':'md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start /jffs/scripts/wan-event',
        'queues':'cat /proc/net/netfilter/nfnetlink_queue'}.items()}
    for key,value in before.items():(a.evidence/(key+'.before')).write_text(value)
    assert not any(row.split()[0]==str(queue) for row in before['queues'].splitlines())
    health=json.loads(ssh('/jffs/wan-watchdog/service/wan-healthctl --timeout 2\n'))
    assert health['fresh'] and not health['routing_error'] and health['applied']==-1 and all(w['eligible'] for w in health['wan']),health
    assert not ssh(f'iptables -t mangle -S {chain} 2>/dev/null || true\n').strip()
    pins=before['pins'].strip();entry='<'+client+'>all>0';assert pins.count(entry)==1
    modified=pins.replace(entry,'')
    ct=ssh('conntrack -L -f ipv4 -o extended,id 2>/dev/null\n')
    occupied=[int(x) for row in ct.splitlines() if 'src='+client+' ' in row for x in re.findall(r'\bsport=(\d+)',row)[:1]]
    bases=[base for base in [43000,44000,41000,42000] if not any(base<=p<base+1000 for p in occupied)]
    assert len(bases)>=2,'closed test port ranges are occupied'
    bases=bases[:2]
    ssh(f'umask 077\nmkdir {root} {root}/sticky {root}/affinity\n')
    for local,target in [('wan-affinity/build/wan-affinity-test','wan-affinity'),('wan-affinity/build/test_health_socket','test-health')]:
        p=subprocess.run(['ssh',*MUX,'router','cat >'+root+'/'+target],input=(REPO/local).read_bytes(),capture_output=True,timeout=20);assert not p.returncode,p.stderr
    ssh(f'chmod 700 {root}/wan-affinity {root}/test-health\n{root}/test-health\n')
    ssh(f'cat >{root}/pins.original <<\'PINS\'\n{pins}\nPINS\ncat >{root}/pins.modified <<\'PINS\'\n{modified}\nPINS\n')
    jumps=[f'-i br0 -s {client}/32 -p {proto} --sport {base}:{base+999} -m conntrack --ctstate NEW --ctdir ORIGINAL -j {chain}' for base in bases for proto in ['tcp','udp']]
    cleanup=f'''#!/bin/sh
exec 9>{root}/cleanup.lock
flock -x 9
rm -f {root}/armed
if [ -f {root}/pin-removed ];then
 original=$(cat {root}/pins.original);modified=$(cat {root}/pins.modified)
 current=$(nvram get wans_routing_rulelist)
 if [ "$current" = "$modified" ];then nvram set "wans_routing_rulelist=$original"
 else case "$current" in *'{entry}'*) ;; *) nvram set "wans_routing_rulelist=$current{entry}";; esac;fi
 ip -4 rule show | awk '$1=="100:" && $3=="{client}" && $5=="wan0" {{f=1}}END{{exit !f}}' || ip -4 rule add priority 100 from {client}/32 lookup wan0
 rm -f {root}/pin-removed
fi
'''
    for jump in jumps:cleanup+=f'while iptables -t mangle -C PREROUTING {jump} 2>/dev/null;do iptables -t mangle -D PREROUTING {jump};done\n'
    cleanup+=f'''if [ -r {root}/helper.pid ];then
 read p <{root}/helper.pid
 case "$p" in ''|*[!0-9]*) ;; *)
 first=$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)
 if [ "$first" = {root}/wan-affinity ];then kill -TERM "$p";fi;;esac
fi
if [ -f {root}/chain-owned ];then
 iptables -t mangle -F {chain}
 iptables -t mangle -X {chain}
 rm -f {root}/chain-owned
fi
'''
    ssh(f'cat >{root}/cleanup <<\'CLEAN\'\n{cleanup}CLEAN\nsh -n {root}/cleanup\n')
    ssh(f'cat >{root}/guard <<\'GUARD\'\n#!/bin/sh\ni=0\nwhile [ "$i" -lt 600 ];do sleep 1;[ -f {root}/armed ] || exit 0;i=$((i+1));done\nsh {root}/cleanup\nGUARD\ntouch {root}/armed\nnohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\n')
    actor=None;results={'root':root,'client':client,'modes':{}}
    def record():
        (a.evidence/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    def ask(op,ports,old=False):
        actor.stdin.write(json.dumps({'op':op,'ports':ports,'old':old})+'\n');actor.stdin.flush()
        assert select.select([actor.stdout],[],[],40)[0],'client response timed out'
        row=json.loads(actor.stdout.readline());assert row['ok'],row
        return row['rows']
    def flows(base):
        data=ssh('conntrack -L -f ipv4 -o extended,id 2>/dev/null\n');rows={}
        for line in data.splitlines():
            if 'src='+client+' ' not in line:continue
            port=int(re.search(r'\bsport=(\d+)',line)[1])
            if not base<=port<base+1000:continue
            if 'tcp' not in line and 'udp' not in line:continue
            dests=re.findall(r'\bdst=(\S+)',line)
            rows[port]={'id':int(re.search(r'\bid=(\d+)',line)[1]),'mark':int(re.search(r'\bmark=(\d+)',line)[1]),'reply_dst':dests[1]}
        return rows
    try:
        ssh(f'iptables -t mangle -N {chain}\ntouch {root}/chain-owned\n')
        for jump in jumps:ssh('iptables -t mangle -I PREROUTING 1 '+jump+'\n')
        ssh(f'touch {root}/pin-removed\nnvram set '+shlex.quote('wans_routing_rulelist='+modified)+f'\nip -4 rule del priority 100 from {client}/32 lookup wan0\n')
        source=(REPO/'wan-affinity/router-tests/adoption_client.py').read_text()
        # Stream the actor source through a command argument; stdin stays its control channel.
        command=shlex.join(['python3','-u','-c',source,client])
        actor=subprocess.Popen(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','alexs-mac-mini',command],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
        for mode,base in zip(['sticky','affinity'],bases):
            run=root+'/'+mode
            ssh(f'''set -e
iptables -t mangle -F {chain}
iptables -t mangle -A {chain} -m conntrack --ctstatus CONFIRMED -j RETURN
iptables -t mangle -A {chain} -m connmark ! --mark 0/0xf0000000 -j RETURN
iptables -t mangle -A {chain} -p tcp --sport {base} -j MARK --set-xmark 0x80000000/0xf0000000
iptables -t mangle -A {chain} -p tcp --sport {base+1} -j MARK --set-xmark 0x90000000/0xf0000000
iptables -t mangle -A {chain} -m mark --mark 0x80000000/0xf0000000 -j CONNMARK --save-mark --nfmask 0xf0000000 --ctmask 0xf0000000
iptables -t mangle -A {chain} -m mark --mark 0x90000000/0xf0000000 -j CONNMARK --save-mark --nfmask 0xf0000000 --ctmask 0xf0000000
''')
            old=ask('open',[base,base+1],True);time.sleep(1)
            original=flows(base);assert original[base]['mark']&0xf0000000==0x80000000 and original[base+1]['mark']&0xf0000000==0x90000000,original
            config=f'ENABLED=1\nMODE={mode}\nCLIENT={client}\nLAN_SCOPE=0\nQUEUE_NUM={queue}\nIDLE_SECONDS=0\nRECONCILE_INTERVAL=2\nAFFINITY_SEED=adoption-test\nHEALTH_SOURCE=watchdog\nHEALTH_SOCKET=/tmp/wan-watchdog/health.sock\n'
            ssh(f'cat >{run}/config <<\'CFG\'\n{config}CFG\n')
            start=f'nohup {root}/wan-affinity --config {run}/config --status-dir {run} --test-port-base {base} </dev/null >>{run}/log 2>&1 &\necho $! >{root}/helper.pid\n'
            ssh(start)
            def status():return ssh('cat '+run+'/status 2>/dev/null || true\n')
            for i in range(30):
                state=status()
                if 'awaiting_new=1' in state and 'socket_connected=2' in state:break
                time.sleep(.3)
            else:raise AssertionError(state)
            # Insert after bootstrap mark assignments, before their saves.
            ssh(f'iptables -t mangle -I {chain} 5 -m mark --mark 0/0xf0000000 -j NFQUEUE --queue-num {queue} --queue-bypass\n')
            ports=list(range(base+10,base+26));new=ask('open',ports)
            time.sleep(3);state=status();match=re.search(r'src='+re.escape(client)+r' wan=([01]) ',state);assert match,state
            chosen=int(match[1]);expected=old[chosen]['public_ip']
            assert all(row['public_ip']==expected for row in new),new
            now=flows(base)
            assert all(now[p]['mark']&0xf0000000==(0x80000000 if chosen==0 else 0x90000000) for p in ports),now
            assert now[base]==original[base] and now[base+1]==original[base+1],'pre-existing state changed'
            assert 'legacy=2' in state and 'awaiting_new=' not in state,state
            # Restart while old sessions on both WANs and new members stay alive.
            ssh(f'read p <{root}/helper.pid\nkill -TERM "$p"\n')
            for i in range(30):
                q=ssh('cat /proc/net/netfilter/nfnetlink_queue\n')
                if not any(row.split()[0]==str(queue) for row in q.splitlines()):break
                time.sleep(.2)
            else:raise AssertionError('private queue did not stop')
            ssh(start)
            for i in range(30):
                state=status()
                if 'reason=restored' in state and 'socket_connected=2' in state:break
                time.sleep(.3)
            else:raise AssertionError(state)
            assert 'src='+client+' wan='+str(chosen)+' ' in state,state
            after=ask('open',[base+30,base+31]);assert all(row['public_ip']==expected for row in after),after
            ask('touch',[base,base+1]);now=flows(base)
            assert now[base]==original[base] and now[base+1]==original[base+1],'old connections changed across helper restart'
            results['modes'][mode]={'chosen_wan':chosen,'parallel_new_https':len(new),'after_restart_https':len(after),'old_connections_unchanged':True,'checkpoint_restored':True,'status':state}
            record();print(mode+': mixed adoption, 16 parallel HTTPS flows, restart and old-state preservation passed',flush=True)
            ssh(f'read p <{root}/helper.pid\nkill -TERM "$p"\n')
            time.sleep(1)
        assert before['watchdog']==ssh('cat /tmp/wan-watchdog/wan-watchdog.pid\n')
        assert before['affinity']==ssh('cat /tmp/wan-affinity/wan-affinity.pid\n')
    finally:
        if actor:
            actor.stdin.close()
            try:actor.wait(timeout=12)
            except subprocess.TimeoutExpired:actor.terminate()
            errors=actor.stderr.read();(a.evidence/'client-stderr.txt').write_text(errors)
        ssh('sh '+root+'/cleanup\n')
        for key,cmd in [('pins','nvram get wans_routing_rulelist'),('rules','ip -4 rule show'),('firewall','iptables-save'),('hooks','md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start /jffs/scripts/wan-event')]:
            current=ssh(cmd+'\n');(a.evidence/(key+'.after')).write_text(current)
            def stable(text):
                return '\n'.join(re.sub(r'\[\d+:\d+\]', '[counters]', line) if line.startswith(':') else line
                                 for line in text.splitlines() if line and not line.startswith('#'))
            assert stable(before[key])==stable(current),key+' not restored'
        results['cleanup_verified']=True;record()
    print('ADOPTION MATRIX PASSED; production services retained, original pins/rules/hooks restored.',flush=True)

if __name__=='__main__':main()
