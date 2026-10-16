#!/usr/bin/env python3
"""Observe queue bypass on dedicated loopback UDP; restore exact owned rules."""
import argparse, datetime, json, re, subprocess, sys, time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'wan-watchdog/router-tests'))
from router_ssh import MUX

def ssh(cmd):
    p=subprocess.run(['ssh',*MUX,'-o','BatchMode=yes','router',cmd],capture_output=True,text=True,timeout=15)
    if p.returncode:raise RuntimeError((cmd,p.stdout,p.stderr))
    return p.stdout
def normalized(text):return [re.sub(r'\[\d+:\d+\]','[0:0]',s) for s in text.splitlines() if s and not s.startswith('#')]
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--allow-router-loopback-tests',action='store_true',required=True);ap.add_argument('--probe',type=Path,required=True);ap.add_argument('--evidence',type=Path,required=True);a=ap.parse_args()
    a.evidence.mkdir(parents=True,exist_ok=True)
    root='/tmp/wa-fallback-'+datetime.datetime.now().strftime('%H%M%S');chain='WA_QF_'+root[-6:];queue=20892;port=58992
    jump=f'-o lo -s 127.88.0.2/32 -d 127.88.0.1/32 -p udp --dport {port} -j {chain}'
    before=ssh('iptables-save');rules=ssh('ip rule show');prod=ssh('cat /tmp/wan-affinity/wan-affinity.pid;cat /tmp/wan-watchdog/wan-watchdog.pid')
    assert not ssh(f"cat /proc/net/netfilter/nfnetlink_queue | awk '$1=={queue} {{print}}'").strip()
    ssh(f'if iptables -t mangle -n -L {chain} >/dev/null 2>&1;then exit 1;fi;umask 077;mkdir {root}')
    with a.probe.open('rb') as f:subprocess.run(['ssh',*MUX,'router','cat >'+root+'/probe'],stdin=f,check=True)
    ssh('chmod 700 '+root+'/probe')
    cleanup=f'''#!/bin/sh
while iptables -t mangle -C OUTPUT {jump} 2>/dev/null;do iptables -t mangle -D OUTPUT {jump};done
iptables -t mangle -F {chain} 2>/dev/null
iptables -t mangle -X {chain} 2>/dev/null
'''
    for name,text in [('cleanup',cleanup),('guard',f'#!/bin/sh\nsleep 90\n[ ! -f {root}/armed ] || sh {root}/cleanup\n')]:
        p=subprocess.run(['ssh',*MUX,'router','cat >'+root+'/'+name],input=text,text=True,capture_output=True);assert not p.returncode,p.stderr
    ssh(f'touch {root}/armed;nohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\necho $! >{root}/guard.pid')
    bridge=None;results={}
    def dump(label):
        text=ssh(f'iptables -t mangle -v -x -n -L {chain};conntrack -L -p udp -s 127.88.0.2 -d 127.88.0.1 --dport {port} 2>/dev/null')
        (a.evidence/(label+'.txt')).write_text(text);return text
    try:
        ssh(f'iptables -t mangle -N {chain}\niptables -t mangle -A {chain} -j NFQUEUE --queue-num {queue} --queue-bypass\niptables -t mangle -A {chain} -j CONNMARK --set-xmark 0x200/0x200\niptables -t mangle -A {chain} -j RETURN\niptables -t mangle -I OUTPUT 1 {jump}\nnohup {root}/probe sink 70 {port} </dev/null >{root}/sink.log 2>&1 &\necho $! >{root}/sink.pid')
        time.sleep(1)
        ssh(f'{root}/probe send 3 54100 {port}');text=dump('absent')
        assert re.search(r'^\s*0\s+0\s+CONNMARK',text,re.M),text
        assert 'mark=0 ' in text and 'mark=512 ' not in text,text
        results['absent_listener']={'later_rule_packets':0,'new_connections':3,'connmark':0}
        bridge=subprocess.Popen(['ssh',*MUX,'router',f'{root}/probe bridge {queue} 45 8'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        ready=bridge.stdout.readline();assert ready.startswith('READY'),ready
        ssh(f'{root}/probe send 64 54200 {port}');time.sleep(1);text=dump('overflow')
        assert re.search(r'^\s*0\s+0\s+CONNMARK',text,re.M),text
        assert 'mark=512 ' not in text,text
        queues=ssh('cat /proc/net/netfilter/nfnetlink_queue');row=next(s.split() for s in queues.splitlines() if s.split()[0]==str(queue));assert row[2]=='8',queues
        results['queue_full']={'held':8,'bypassed':56,'later_rule_packets':0,'connmark':0}
        bridge.communicate('STOP\n',timeout=8);assert bridge.returncode==0
        bridge=None
        ssh(f'iptables -t mangle -D {chain} -j NFQUEUE --queue-num {queue} --queue-bypass\n{root}/probe send 3 54300 {port}')
        text=dump('normal');assert re.search(r'^\s*3\s+\d+\s+CONNMARK',text,re.M),text;assert 'mark=512 ' in text,text
        results['normal_traversal']={'later_rule_packets':3,'new_connections':3,'connmark':512}
    finally:
        if bridge:
            try:bridge.communicate('STOP\n',timeout=8)
            except subprocess.TimeoutExpired:bridge.terminate()
        ssh('sh '+root+'/cleanup;rm -f '+root+'/armed')
        for name in ['guard','sink']:
            ssh(f'''if [ -r {root}/{name}.pid ];then
 read p <{root}/{name}.pid
 case "$(tr '\\000' ' ' </proc/$p/cmdline 2>/dev/null)" in *'{root}/'*) kill "$p" 2>/dev/null || true;;esac
fi''')
        after=ssh('iptables-save');assert normalized(before)==normalized(after),'firewall changed'
        assert rules==ssh('ip rule show');assert prod==ssh('cat /tmp/wan-affinity/wan-affinity.pid;cat /tmp/wan-watchdog/wan-watchdog.pid')
        results['production_preserved']=True
        (a.evidence/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        ssh('rm -rf '+root)
    print(json.dumps(results,indent=2))

if __name__=='__main__':main()
