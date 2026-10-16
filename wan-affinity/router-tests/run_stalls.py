#!/usr/bin/env python3
"""Opt-in queue recovery qualification, restricted to a loopback fixture.

The controller copy changes PREROUTING/-i to OUTPUT/-o; an iptables read shim
supplies a virtual balance anchor. All mutations still use the real controller,
real NFQUEUE helper, independent supervisor and real kernel. No WAN is failed.
"""
import argparse, datetime, json, math, re, shlex, subprocess, sys, time
from pathlib import Path
REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'wan-watchdog/router-tests'))
from router_ssh import MUX

def ssh(cmd,body=None,check=True,timeout=25):
    p=subprocess.run(['ssh',*MUX,'-o','BatchMode=yes','-o','ConnectTimeout=8','router',cmd],input=body,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:raise RuntimeError((cmd,p.returncode,p.stdout,p.stderr))
    return p.stdout

def normalized(text):
    return [re.sub(r'\[\d+:\d+\]','[0:0]',x) for x in text.splitlines() if x and not x.startswith('#')]

def distribution(samples):
    s=sorted(samples)
    return dict(n=len(s),min=min(s),median=s[len(s)//2],p95=s[math.ceil(.95*len(s))-1],max=max(s))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--allow-loopback-tests',action='store_true',required=True)
    ap.add_argument('--evidence',type=Path,required=True);ap.add_argument('--probe',type=Path,required=True)
    ap.add_argument('--lock-only',action='store_true');ap.add_argument('--checkpoint-only',action='store_true');ap.add_argument('--trials',type=int,default=12);ap.add_argument('--soak-seconds',type=int,default=90)
    a=ap.parse_args();assert 3<=a.trials<=30 and 30<=a.soak_seconds<=300
    a.evidence.mkdir(parents=True,exist_ok=True)
    root='/tmp/wa-stall-'+datetime.datetime.now().strftime('%H%M%S');chain='WA_STALL_'+root[-6:]
    queue,port=20894,58994;ctl=root+'/wan-affinity.sh';probe=root+'/nfq-probe'
    env=f'PATH={root}/bin:$PATH WA_BASE={root} WA_RUN={root}/run WA_CHAIN={chain}'
    before={k:ssh(v) for k,v in {'firewall':'iptables-save','rules':'ip rule show','pins':'nvram get wans_routing_rulelist','pids':'cat /tmp/wan-affinity/wan-affinity.pid;cat /tmp/wan-watchdog/wan-watchdog.pid','hooks':'md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start /jffs/scripts/wan-event'}.items()}
    assert not ssh(f"awk '$1=={queue} {{print}}' /proc/net/netfilter/nfnetlink_queue").strip()
    assert not ssh(f'iptables -t mangle -S {chain} 2>/dev/null || true').strip()
    assert not ssh(f'conntrack -L -s 127.88.0.2 -d 127.88.0.1 -p udp --dport {port} 2>/dev/null').strip()
    ssh(f'umask 077;mkdir {root} {root}/run {root}/bin')
    for source,name in [(REPO/'wan-affinity/build/wan-affinity','wan-affinity'),(a.probe,'nfq-probe')]:
        p=subprocess.run(['ssh',*MUX,'router','cat >'+root+'/'+name],input=source.read_bytes(),capture_output=True,timeout=20);assert not p.returncode,p.stderr
    script=(REPO/'wan-affinity/wan-affinity.sh').read_text().replace('PREROUTING','OUTPUT').replace('_record="-i $_lan','_record="-o $_lan')
    ssh('cat >'+ctl,script)
    real_ipt=ssh('which iptables').strip();real_nvram=ssh('which nvram').strip()
    assert all(re.fullmatch(r'/[A-Za-z0-9_./-]+',p) for p in (real_ipt,real_nvram))
    ssh('cat >'+root+'/bin/iptables',f'''#!/bin/sh
if [ "$*" = '-t mangle -S OUTPUT' ];then
 {real_ipt} "$@" || exit $?
 echo '-A OUTPUT -j balance'
else exec {real_ipt} "$@";fi
''')
    ssh('cat >'+root+'/bin/nvram',f'''#!/bin/sh
case "$1:$2" in get:lan_ifname) echo lo;;get:lan_ipaddr) echo 127.88.0.1;;get:lan_netmask) echo 255.255.255.0;;*) exec {real_nvram} "$@";;esac
''')
    ssh(f'chmod 700 {root}/wan-affinity {ctl} {probe} {root}/bin/*')
    ssh('cat >'+root+'/wan-affinity.conf',f'ENABLED=1\nMODE=sticky\nCLIENT=127.88.0.2\nLAN_SCOPE=0\nSUPERVISE=1\nQUEUE_NUM={queue}\nHEALTH_SOURCE=watchdog\nIDLE_SECONDS=0\n')
    cleanup=f'''#!/bin/sh
if [ -r {root}/lock.pid ];then
 read p <{root}/lock.pid
 case "$(tr '\\000' ' ' </proc/$p/cmdline 2>/dev/null)" in *'{root}/lock'*) kill -TERM "$p" 2>/dev/null;;esac
fi
{env} sh {ctl} stop
for name in sink steady;do
 if [ -r {root}/$name.pid ];then
  read p <{root}/$name.pid
  case "$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)" in '{probe}') kill -TERM "$p" 2>/dev/null;;esac
 fi
done
sleep 1
conntrack -D -s 127.88.0.2 -d 127.88.0.1 -p udp --dport {port} >/dev/null 2>&1
rm -f {root}/armed
'''
    ssh('cat >'+root+'/cleanup',cleanup)
    ssh('cat >'+root+'/guard',f'#!/bin/sh\nsleep 900\n[ ! -f {root}/armed ] || sh {root}/cleanup\n')
    ssh(f'touch {root}/armed;nohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\necho $! >{root}/guard.pid')
    results={'queue':queue,'private_scope':'loopback-only','freeze':[],'exit':[]}
    def record(): (a.evidence/'results.json').write_text(json.dumps(results,indent=2)+'\n')
    def control(cmd):return ssh(f'{env} sh {ctl} {cmd}',timeout=40)
    def row():
        text=ssh(f"awk '$1=={queue} {{print}}' /proc/net/netfilter/nfnetlink_queue").split()
        return text
    def pid():return int(ssh(f'cat {root}/run/wan-affinity.pid 2>/dev/null || true').strip() or '0')
    def queue_changed(old):
        r=row();return not r or r[1]!=str(old)
    def new_ready(old):
        p=pid();r=row()
        if not (p>1 and p!=old and bool(r) and r[1]==str(p)):return False
        # Binding the queue precedes controller readiness and rule insertion.
        # Never fault the next helper until activation actually completed.
        return bool(ssh(f'test -s {root}/run/owned-jump && test ! -f {root}/run/restart-request && iptables -t mangle -C OUTPUT $(cat {root}/run/owned-jump) && echo ready',check=False).strip())
    def status():return ssh(f'cat {root}/run/status')
    def wait_for(fn,seconds=12):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            value=fn()
            if value:return value
            time.sleep(.05)
        raise AssertionError(('condition timed out',status(),ssh('cat '+root+'/run/supervisor.log',check=False)))
    def fresh_monitor():
        # Reset only the private monitor between distribution trials. The
        # separate trip test below leaves its actual restart budget intact.
        ssh(f'''read p <{root}/run/supervisor.pid
case "$(tr '\\000' ' ' </proc/$p/cmdline 2>/dev/null)" in *'--supervise {ctl}'*) kill -TERM "$p";;*) exit 1;;esac
rm {root}/run/supervisor.pid''')
        control('reconcile')
        time.sleep(1.1)
    def probe_round(base,count=1):return json.loads(ssh(f'{probe} roundtrip {count} {base} {port}'))
    def locked_recovery():
        ssh('cat >'+root+'/lock',f'#!/bin/sh\nexec 9>{root}/run.lock\nflock -x 9\ntouch {root}/lock.held\nsleep 20 9>&-\n:\n')
        ssh(f'nohup sh {root}/lock </dev/null >{root}/lock.log 2>&1 &\necho $! >{root}/lock.pid')
        wait_for(lambda:ssh(f'test -f {root}/lock.held && echo held',check=False).strip())
        old=pid();begin=time.monotonic();ssh(f'kill -STOP {old}\n{probe} send 1 26900 {port}')
        wait_for(lambda:queue_changed(old));elapsed=time.monotonic()-begin
        assert elapsed<8,elapsed
        # The holder is still alive: the controller cannot have recovered yet.
        ssh(f'read p <{root}/lock.pid;kill -0 "$p"')
        sample=probe_round(26901)
        ssh(f'read p <{root}/lock.pid;kill -TERM "$p";rm {root}/lock.pid')
        wait_for(lambda:new_ready(old))
        results['controller_lock']={'queue_close_seconds':elapsed,'fresh_request_ms':sample['max_ms'],'closed_before_lock_release':True}
        print('Controller lock cannot delay queue closure: passed',flush=True);record()
    try:
        control('start')
        ssh(f'nohup {probe} sink 880 {port} </dev/null >{root}/sink.log 2>&1 &\necho $! >{root}/sink.pid')
        probe_round(21000)
        ssh(f'nohup {probe} steady 600 21001 {port} </dev/null >{root}/steady.log 2>&1 &\necho $! >{root}/steady.pid')
        wait_for(lambda:'21001' in ssh(f'conntrack -L -s 127.88.0.2 -d 127.88.0.1 -p udp --dport {port} 2>/dev/null'))
        if a.lock_only:
            locked_recovery();return
        # A blocked checkpoint writer may fail, but cannot hold the admission
        # fd or delay verdicts. A FIFO blocks its open until its two-second kill.
        ssh(f'rm -f {root}/run/mappings.cache.new;{probe} fifo {root}/run/mappings.cache.new')
        start=time.monotonic();initial_pid=pid();samples=[];packets=0
        while time.monotonic()-start<a.soak_seconds:
            sample=probe_round(22000+packets,64);samples.append(sample);packets+=64
            assert sample['max_ms']<250,sample
            assert pid()==initial_pid,'healthy helper restarted under checkpoint/reconnect pressure'
            # Delete only this fixture's completed test flows to permit bounded
            # port reuse without inflating the household conntrack table.
            ssh(f'conntrack -D -s 127.88.0.2 -d 127.88.0.1 -p udp --sport {22000+packets-64}:{22000+packets-1} --dport {port} >/dev/null 2>&1 || true')
            if packets>=4096:packets=0
            time.sleep(.25)
        text=status();fields=dict(re.findall(r'(\w+)=([^\s]+)',text))
        assert int(fields.get('failed','0'))>0,text
        assert 'action=' not in ssh('cat '+root+'/run/supervisor.log',check=False)
        results['checkpoint_pressure']={'seconds':time.monotonic()-start,'samples':samples,'status':text,'no_restarts':True}
        # Kill the helper while the blocked writer is definitely alive. Its
        # inherited queue fd must already be closed, not held until timeout.
        def blocked_writer():
            m=re.search(r'checkpoint pid=(\d+)',status())
            if not m or int(m[1])<2:return 0
            stat=ssh(f'cat /proc/{m[1]}/stat 2>/dev/null || true').split()
            return int(m[1]) if stat and stat[2]!='Z' else 0
        writer=wait_for(blocked_writer);old=pid();begin=time.monotonic()
        ssh(f'rm {root}/run/mappings.cache.new;kill -KILL {old}')
        wait_for(lambda:queue_changed(old))
        results['exit_with_blocked_writer']={'queue_close_seconds':time.monotonic()-begin,
                                            'fresh_request_ms':probe_round(26800)['max_ms']}
        wait_for(lambda:new_ready(old))
        worker_stat=ssh(f'cat /proc/{writer}/stat 2>/dev/null || true').split()
        assert not worker_stat or worker_stat[2]=='Z',worker_stat
        results['exit_with_blocked_writer']['no_running_orphan']=True
        print('Checkpoint pressure and writer crash: passed, no false restart or queue leak',flush=True);record()
        if a.checkpoint_only:return
        locked_recovery()
        for i in range(a.trials):
            fresh_monitor();old=pid();base=27000+i*4
            time.sleep((i%4)*.17)
            begin=time.monotonic();ssh(f'kill -STOP {old}\n{probe} send 1 {base} {port}')
            assert row()[2]=='1','low-traffic fixture must hold exactly one packet'
            wait_for(lambda:queue_changed(old))
            detected=time.monotonic()-begin
            sample=probe_round(base+1)
            wait_for(lambda:new_ready(old))
            recovered=time.monotonic()-begin
            results['freeze'].append({'close_seconds':detected,'ready_seconds':recovered,'fresh_request_ms':sample['max_ms']})
            print(f'Low-traffic freeze {i+1}/{a.trials}: queue closed {detected:.3f}s, ready {recovered:.3f}s',flush=True);record()
        # With no arrivals a stopped helper remains alone. One later packet
        # must trigger recovery without waiting for the queue to fill.
        fresh_monitor();old=pid();ssh(f'kill -STOP {old}');time.sleep(7)
        assert pid()==old and row()[2]=='0'
        begin=time.monotonic();ssh(f'{probe} send 1 27600 {port}')
        wait_for(lambda:queue_changed(old));results['idle_then_one']={'close_seconds':time.monotonic()-begin}
        wait_for(lambda:new_ready(old))
        for i in range(a.trials):
            old=pid();begin=time.monotonic();ssh(f'kill -KILL {old}')
            sample=probe_round(27700+i)
            bypass=time.monotonic()-begin
            wait_for(lambda:new_ready(old))
            results['exit'].append({'observed_seconds':bypass,'fresh_request_ms':sample['max_ms']})
            print(f'Exit bypass {i+1}/{a.trials}: fresh echo {sample["max_ms"]:.3f}ms',flush=True);record()
        # Three genuine stalls within a minute trip the recovery breaker.
        fresh_monitor()
        for i in range(3):
            old=pid();ssh(f'kill -STOP {old}\n{probe} send 1 {27800+i*2} {port}')
            wait_for(lambda:queue_changed(old))
            if i<2:wait_for(lambda:new_ready(old))
        wait_for(lambda:not ssh(f'iptables -t mangle -S {chain} 2>/dev/null || true').strip())
        assert ssh(f'test -f {root}/run/supervisor-tripped && echo yes').strip()=='yes'
        control('reconcile');assert not row()
        results['circuit_breaker']={'three_stalls_stop_retries':True,'explicit_start':control('start')}
        probe_round(27810)
        results['freeze_distribution_seconds']=distribution([x['close_seconds'] for x in results['freeze']])
        results['ready_distribution_seconds']=distribution([x['ready_seconds'] for x in results['freeze']])
        results['exit_request_distribution_ms']=distribution([x['fresh_request_ms'] for x in results['exit']])
    finally:
        ssh(f'rm -f {root}/run/mappings.cache.new',check=False)
        ssh('sh '+root+'/cleanup',check=False,timeout=40)
        for name in ['run/status','run/supervisor.log','run/wan-affinity.log','steady.log']:
            (a.evidence/name.replace('/','-')).write_text(ssh('cat '+root+'/'+name+' 2>/dev/null || true'))
        steady_text=ssh(f'cat {root}/steady.log 2>/dev/null || true').strip()
        if steady_text:
            steady=json.loads(steady_text);results['established_flow']=steady
            assert steady['lost']==0,steady
        for k,cmd in {'firewall':'iptables-save','rules':'ip rule show','pins':'nvram get wans_routing_rulelist','pids':'cat /tmp/wan-affinity/wan-affinity.pid;cat /tmp/wan-watchdog/wan-watchdog.pid','hooks':'md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start /jffs/scripts/wan-event'}.items():
            after=ssh(cmd);assert (normalized(before[k])==normalized(after) if k=='firewall' else before[k]==after),k
        results['production_preserved']=True;record()
        ssh(f'''read p <{root}/guard.pid
case "$(tr '\\000' ' ' </proc/$p/cmdline 2>/dev/null)" in *'{root}/guard'*) kill "$p";;esac
rm -rf {root}''')
    print(json.dumps({k:v for k,v in results.items() if k.endswith('seconds') or k.endswith('_ms') or k=='established_flow'},indent=2))
if __name__=='__main__':main()
