#!/usr/bin/env python3
"""Opt-in router tests: fixed loopback scope, timed cleanup, no WAN/LAN changes."""
import argparse
import datetime
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'wan-watchdog/router-tests'))
from router_ssh import MUX

SRC, DST, PORT, QUEUE = '127.88.0.2', '127.88.0.1', 58888, 58888

def ssh(command, *, input=None, check=True, timeout=15):
    p = subprocess.run(['ssh', *MUX, '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5',
                        'router', command], input=input, text=True, capture_output=True,
                       timeout=timeout)
    if check and p.returncode:
        raise RuntimeError((command, p.returncode, p.stdout, p.stderr))
    return p

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allow-loopback-tests', action='store_true', required=True)
    ap.add_argument('--fault-only', action='store_true')
    ap.add_argument('--backup', type=Path, required=True)
    ap.add_argument('--binary', type=Path, required=True)
    ap.add_argument('--generator', type=Path, required=True)
    a = ap.parse_args()
    root = '/tmp/wa-life-' + datetime.datetime.now().strftime('%H%M%S')
    chain = 'WA_LIFE_' + root[-6:]
    binary, generator = root+'/wan-affinity-test', root+'/nfq-probe'
    jump = f'-s {SRC}/32 -d {DST}/32 -o lo -p udp --dport {PORT} -j {chain}'
    assert ssh(f'iptables -t mangle -S {chain}', check=False).returncode
    assert not any(row.split()[0] == str(QUEUE) for row in
                   ssh('cat /proc/net/netfilter/nfnetlink_queue').stdout.splitlines())
    assert not ssh(f'conntrack -L -s {SRC} -d {DST} -p udp --dport {PORT} 2>/dev/null').stdout.strip()
    ssh(f'umask 077\nmkdir {root}\nmkdir {root}/run')
    for source, target in [(a.binary, binary), (a.generator, generator)]:
        with source.open('rb') as f:
            subprocess.run(['ssh',*MUX,'router','cat > '+shlex.quote(target)], stdin=f, check=True)
    ssh(f'chmod 700 {binary} {generator}')
    cfg = f'''CLIENT={SRC}
LAN_SCOPE=0
MODE=sticky
QUEUE_NUM={QUEUE}
RECONCILE_INTERVAL=1
IDLE_SECONDS=0
SNAPSHOT_PATH={root}/no-health
'''
    ssh(f'cat >{root}/config', input=cfg)
    cleanup = f'''#!/bin/sh
rm -f {root}/armed
while iptables -t mangle -C OUTPUT {jump} 2>/dev/null; do iptables -t mangle -D OUTPUT {jump}; done
iptables -t mangle -F {chain} 2>/dev/null
iptables -t mangle -X {chain} 2>/dev/null
for name in helper sink; do
 if [ -r {root}/$name.pid ]; then
  read p <{root}/$name.pid
  case "$p" in ''|*[!0-9]*) continue;; esac
  first=$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)
  case "$first" in '{binary}'|'{generator}') kill -TERM "$p" 2>/dev/null;; esac
 fi
done
conntrack -D -s {SRC} -d {DST} -p udp --dport {PORT} >/dev/null 2>&1
date >{root}/cleaned
'''
    ssh(f'cat >{root}/cleanup.sh', input=cleanup)
    ssh(f'cat >{root}/guard.sh', input=f'#!/bin/sh\nsleep 180\n[ ! -f {root}/armed ] || sh {root}/cleanup.sh\n')
    ssh(f'touch {root}/armed\nnohup sh {root}/guard.sh </dev/null >{root}/guard.log 2>&1 &\necho $! >{root}/guard.pid')
    result_file=a.backup/'lifecycle-kernel-results.json'
    results=json.loads(result_file.read_text()) if result_file.exists() else {}
    def record(key, value):
        results[key] = value
        (a.backup/'lifecycle-kernel-results.json').write_text(json.dumps(results, indent=2)+'\n')
        print(key, json.dumps(value), flush=True)
    def status():
        p = ssh(f'cat {root}/run/status 2>/dev/null', check=False)
        if p.returncode: return {}, p.stdout
        fields = dict(re.findall(r'([a-zA-Z0-9_]+)=([^\s]+)', p.stdout))
        return fields, p.stdout
    def stop():
        ssh(f'''while iptables -t mangle -C OUTPUT {jump} 2>/dev/null; do iptables -t mangle -D OUTPUT {jump}; done
if [ -r {root}/helper.pid ]; then
 read p <{root}/helper.pid
 case "$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)" in '{binary}') kill -TERM "$p";; esac
fi''')
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if not any(row.split()[0]==str(QUEUE) for row in ssh('cat /proc/net/netfilter/nfnetlink_queue').stdout.splitlines()): break
            time.sleep(.1)
        else: raise AssertionError('queue did not close')
        ssh(f'iptables -t mangle -F {chain} 2>/dev/null\niptables -t mangle -X {chain} 2>/dev/null\nconntrack -D -s {SRC} -d {DST} -p udp --dport {PORT} >/dev/null 2>&1', check=False)
    def start(extra='', drop=False):
        ssh(f'rm -f {root}/run/status\nnohup {binary} --config {root}/config --status-dir {root}/run --queue {QUEUE} --loopback-test {extra} </dev/null >{root}/helper.log 2>&1 &\necho $! >{root}/helper.pid')
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            fields, text=status()
            if fields.get('refresh_ok')=='1': break
            time.sleep(.1)
        else: raise AssertionError(('helper readiness',ssh(f'cat {root}/helper.log').stdout))
        rules=f'''set -e
iptables -t mangle -N {chain}
iptables -t mangle -A {chain} -m conntrack --ctstatus CONFIRMED -j RETURN
iptables -t mangle -A {chain} -m connmark ! --mark 0/0xf0000000 -j RETURN
iptables -t mangle -A {chain} -m mark --mark 0x0/0xf0000000 -m conntrack --ctstate NEW --ctdir ORIGINAL -j NFQUEUE --queue-num {QUEUE} --queue-bypass
iptables -t mangle -A {chain} -m mark --mark 0xa0000000/0xf0000000 -j MARK --set-xmark 0/0xf0000000
iptables -t mangle -A {chain} -m mark --mark 0x80000000/0xf0000000 -j CONNMARK --save-mark --nfmask 0xf0000000 --ctmask 0xf0000000
iptables -t mangle -A {chain} -m mark --mark 0x90000000/0xf0000000 -j CONNMARK --save-mark --nfmask 0xf0000000 --ctmask 0xf0000000
iptables -t mangle -A {chain} -j {'DROP' if drop else 'RETURN'}
iptables -t mangle -I OUTPUT 1 {jump}
'''
        ssh('sh -s', input=rules)
    def wait_fields(predicate, timeout=6):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            fields,text=status()
            if predicate(fields): return fields,text
            time.sleep(.15)
        raise AssertionError(('status condition',text))
    try:
        if not a.fault_only:
            start(drop=True)
            ssh(f'{generator} send 16 54000 {PORT}')
            f,text=wait_fields(lambda f: int(f.get('admissions','0'))>=16 and f.get('assoc')=='0' and f.get('unobserved')=='0')
            record('never_confirmed_drops_cleaned', {'admissions':f['admissions'],'status':text})
            stop()
            start('--test-hold-first-ms 4000', drop=True)
            ssh(f'{generator} send 1 54100 {PORT}')
            time.sleep(2.8)
            f,text=status()
            assert f.get('assoc')=='1' and int(f.get('kernel_unconfirmed','0'))>=1, text
            record('held_packet_retained_beyond_old_ttl',text)
            f,text=wait_fields(lambda f:f.get('assoc')=='0' and f.get('unobserved')=='0')
            record('released_then_dropped_packet_cleaned',text)
            stop()
            ssh(f'nohup {generator} sink 90 {PORT} </dev/null >{root}/sink.log 2>&1 &\necho $! >{root}/sink.pid')
            start('--test-refresh-delay-ms 1500')
            roundtrips=[]
            for base in [54200,54300,54400]:
                time.sleep(1.2)
                ssh(f'{generator} parallel 4 24 {base} {PORT}')
                sample=json.loads(ssh(f'{generator} roundtrip 24 {base+400} {PORT}').stdout)
                assert sample['max_ms']<250,sample
                roundtrips.append(sample)
            f,text=wait_fields(lambda f:int(f.get('admissions','0'))>=360 and float(f.get('max_refresh_ms','0'))>=1500)
            assert float(f['max_timestamp_delay_us'])<250000,text
            record('slow_refresh_does_not_block_admission',{'packets':360,'roundtrips':roundtrips,'status':text})
            stop()
        start('--test-refresh-delay-ms 10000')
        for i in range(17):
            ssh(f'{generator} send 1 {54500+i} {PORT}')
            time.sleep(1)
        f,text=status()
        assert int(f.get('refresh_failed','0'))>=1 and int(f.get('admissions','0'))>=17,text
        assert float(f['max_timestamp_delay_us'])<250000,text
        assert f.get('wan0')=='unknown' and f.get('wan1')=='unknown',text
        record('timed_out_worker_keeps_admission_running',text)
        # Kill the helper while its worker is sleeping. Its queue fd must not
        # survive in that worker; parent-death handling must remove the worker.
        worker=int(f.get('refresh_pid','0'));assert worker>0,text
        ssh(f'kill -KILL $(cat {root}/helper.pid)')
        time.sleep(.4)
        assert not any(row.split()[0]==str(QUEUE) for row in ssh('cat /proc/net/netfilter/nfnetlink_queue').stdout.splitlines())
        # A dead child can briefly be an init-owned zombie; no running collector.
        process=ssh(f'cat /proc/{worker}/stat 2>/dev/null',check=False).stdout
        assert not process or process.split()[2]=='Z',process
        record('crash_does_not_leak_queue_or_running_worker',True)
    finally:
        ssh('sh '+root+'/cleanup.sh',check=False)
        for name in ['helper.log','run/status','sink.log']:
            p=ssh('cat '+root+'/'+name,check=False)
            (a.backup/('loopback-'+name.replace('/','-'))).write_text(p.stdout+'\n'+p.stderr)
        ssh(f'''p=$(cat {root}/guard.pid)
case "$(tr '\\000' ' ' </proc/$p/cmdline 2>/dev/null)" in *'{root}/guard.sh'*) kill "$p";; esac
rm -rf {root}''')
        record('cleanup_queue_listing',ssh('cat /proc/net/netfilter/nfnetlink_queue').stdout)
if __name__=='__main__': main()
