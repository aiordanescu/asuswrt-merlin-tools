#!/usr/bin/env python3
"""Opt-in real-binary lifecycle and bounded forwarding test on one pinned Mini.

No WAN changes, shared firewall rebuild, acceleration changes or state flushes.
An independent router guard restores the exact test pin and removes our objects.
"""
import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'wan-watchdog/router-tests'))
from router_ssh import MUX


def ssh(code, host='router', timeout=30):
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', *(MUX if host == 'router' else []),
                        host, 'sh -s'], input=code, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.returncode, p.stdout, p.stderr))
    return p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allow-client-test', action='store_true', required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    a = ap.parse_args()
    a.evidence.mkdir(parents=True, exist_ok=True)
    root = '/tmp/wa-service-' + uuid.uuid4().hex[:8]
    chain = 'WA_SERVICE_' + root[-8:]
    base, run = root + '/base', root + '/run'
    env = f'WA_BASE={base} WA_RUN={run} WA_CHAIN={chain} WA_SCRIPTS={root}/hooks '
    ctl = env + f'sh {base}/wan-affinity.sh '
    client = ssh("ifconfig en0 | awk '$1==\"inet\" {print $2;exit}'\n", 'alexs-mac-mini').strip()
    assert re.fullmatch(r'192\.168\.1\.\d+', client), client
    pins = ssh('nvram get wans_routing_rulelist\n').strip()
    entry = '<' + client + '>all>0'
    assert pins.count(entry) == 1
    modified = pins.replace(entry, '')
    before = {k: ssh(v + '\n') for k, v in {
        'firewall': 'iptables-save', 'rules': 'ip -4 rule show',
        'watchdog': 'cat /tmp/wan-watchdog/wan-watchdog.pid',
        'hooks': 'md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start'}.items()}
    assert not ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip()
    ssh(f'umask 077\nmkdir {root}\nmkdir {base} {root}/hooks\n')
    for local, remote in [('build/wan-affinity', 'wan-affinity'), ('wan-affinity.sh', 'wan-affinity.sh'), ('hooks.sh', 'hooks.sh')]:
        with (REPO / 'wan-affinity' / local).open('rb') as f:
            subprocess.run(['ssh', *MUX, 'router', f'cat >{base}/{remote}'], stdin=f, check=True)
    ssh(f'chmod 700 {base}/*\nprintf \'#!/bin/sh\\n# sentinel\\n\' >{root}/hooks/services-start\n')
    ssh(f'cat >{root}/pins.original <<\'PINS\'\n{pins}\nPINS\ncat >{root}/pins.modified <<\'PINS\'\n{modified}\nPINS\n')
    config = f'''ENABLED=1
MODE=sticky
CLIENT={client}
LAN_SCOPE=0
SUPERVISE=1
QUEUE_NUM=20880
IDLE_SECONDS=0
HEALTH_SOURCE=watchdog
HEALTH_SOCKET=/tmp/wan-watchdog/health.sock
'''
    ssh(f'cat >{base}/wan-affinity.conf <<\'CFG\'\n{config}CFG\n')
    cleanup = f'''#!/bin/sh
mkdir {root}/cleaning 2>/dev/null || exit 0
rm -f {root}/armed
if [ -f {root}/pin-removed ]; then
 original=$(cat {root}/pins.original);modified=$(cat {root}/pins.modified)
 current=$(nvram get wans_routing_rulelist)
 if [ "$current" = "$modified" ]; then nvram set "wans_routing_rulelist=$original"
 else case "$current" in *'{entry}'*) ;; *) nvram set "wans_routing_rulelist=$current{entry}";;esac;fi
 ip -4 rule show | awk '$1=="100:" && $3=="{client}" && $5=="wan0" {{f=1}}END{{exit !f}}' || ip -4 rule add priority 100 from {client}/32 lookup wan0
fi
{ctl}stop
date >{root}/cleaned
rmdir {root}/cleaning
'''
    ssh(f'cat >{root}/cleanup <<\'EOF\'\n{cleanup}EOF\nsh -n {root}/cleanup\n')
    ssh(f'cat >{root}/guard <<\'EOF\'\n#!/bin/sh\ni=0\nwhile [ "$i" -lt 900 ];do sleep 1;[ -f {root}/armed ] || exit 0;i=$((i+1));done\nsh {root}/cleanup\nEOF\ntouch {root}/armed\nnohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\n')
    result = {'root': root, 'checks': {}, 'downloads': {}}

    def record(name, value):
        result['checks'][name] = value
        (a.evidence / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
        print(name, json.dumps(value), flush=True)

    def status():
        return ssh(f'cat {run}/status\n')

    def download(label):
        rows = []
        for i in range(3):
            port = 48000 + (0 if label == 'firmware' else 10) + i
            cmd = ['curl', '-4', '--interface', client, '--local-port', str(port),
                   '--max-time', '30', '-sS', '-o', '/dev/null', '-w', '%{json}',
                   'https://speed.cloudflare.com/__down?bytes=10000000']
            output = ssh(shlex.join(cmd) + '\n', 'alexs-mac-mini', 40)
            row = json.loads(output)
            assert row['http_code'] == 200 and row['size_download'] == 10000000, row
            rows.append({k: row[k] for k in ['time_total', 'time_starttransfer', 'speed_download', 'size_download', 'remote_ip']})
        result['downloads'][label] = rows
        record(label + '_downloads', {'bytes': sum(r['size_download'] for r in rows), 'seconds': sum(r['time_total'] for r in rows)})

    try:
        download('firmware')
        for bad in ['QUEUE_NUM=garbage', 'IDLE_SECONDS=nan', 'MODE=wrong', 'QUEUE_MAXLEN=99999']:
            ssh(f'cp {base}/wan-affinity.conf {root}/bad.conf\nprintf \'%s\\n\' {shlex.quote(bad)} >>{root}/bad.conf\n' +
                f'if {base}/wan-affinity --config {root}/bad.conf --check-config >/dev/null 2>&1;then exit 1;fi\n')
        record('invalid_configuration_rejected_without_activation', True)
        real_iptables = ssh("type iptables | awk '{print $NF}'\n").strip()
        shim = f'''#!/bin/sh
if [ -f {root}/withhold-balance ] && [ "$*" = '-t mangle -S PREROUTING' ];then
 {real_iptables} "$@" | sed '/-j balance$/d'
else exec {real_iptables} "$@";fi
'''
        ssh(f'mkdir {root}/bin\ncat >{root}/bin/iptables <<\'SHIM\'\n{shim}SHIM\nchmod 700 {root}/bin/iptables\ntouch {root}/withhold-balance\n')
        ssh(f'if PATH={root}/bin:$PATH {ctl}start;then exit 1;fi\nrm {root}/withhold-balance\n')
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            if ssh(f'test ! -f {run}/status || cat {run}/status\n').startswith('mode=sticky'):
                break
            time.sleep(.5)
        else:
            raise AssertionError('boot retry did not start after firmware readiness returned')
        record('delayed_firewall_readiness_retried_automatically', True)
        pid = ssh(f'cat {run}/wan-affinity.pid\n').strip()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: ssh(ctl + 'reconcile\n'), range(4)))
        assert pid == ssh(f'cat {run}/wan-affinity.pid\n').strip()
        record('concurrent_reconcile_preserves_pid', True)
        # Remove only our recorded jump and chain, emulating their loss.
        ssh(f'set -e\nread record <{run}/owned-jump\niptables -t mangle -D PREROUTING $record\niptables -t mangle -F {chain}\niptables -t mangle -X {chain}\n' + ctl + 'reconcile\n')
        assert pid == ssh(f'cat {run}/wan-affinity.pid\n').strip()
        record('lost_owned_rules_repaired_without_restart', True)
        time.sleep(24)
        assert pid == ssh(f'cat {run}/wan-affinity.pid\n').strip()
        record('healthy_supervision_does_not_restart_helper', True)
        ssh(f'kill -KILL {pid}\n')
        time.sleep(.3)
        assert not ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip()
        deadline = time.monotonic() + 18
        while time.monotonic() < deadline:
            fresh = ssh(f'cat {run}/wan-affinity.pid 2>/dev/null || true\n').strip()
            if fresh != pid and ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip():
                break
            time.sleep(.5)
        else:
            raise AssertionError('supervisor did not restore the helper')
        record('crash_queue_closes_and_supervisor_recovers', True)
        pid = ssh(f'cat {run}/wan-affinity.pid\n').strip()
        ssh(f'kill -STOP {pid}\n')
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            fresh = ssh(f'cat {run}/wan-affinity.pid 2>/dev/null || true\n').strip()
            if fresh != pid and ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip():
                break
            time.sleep(1)
        else:
            raise AssertionError('supervisor did not recover a stopped event loop')
        record('stalled_heartbeat_is_restarted', True)
        # Use the installed hook code, but only in a private hooks directory.
        ssh(env + f'sh {base}/hooks.sh install\n' + env + f'sh {base}/hooks.sh install\n')
        hooks = ssh(f'cat {root}/hooks/services-start\n')
        assert hooks.count('# BEGIN wan-affinity') == 1 and '# sentinel' in hooks
        ssh(env + f'sh {base}/hooks.sh remove\n')
        assert '# BEGIN wan-affinity' not in ssh(f'cat {root}/hooks/services-start\n')
        record('router_hook_install_remove_preserves_content', True)
        # Remove the GUI selector only in RAM; keep the kernel Primary pin
        # until the collector has safely adopted the surviving primary flows.
        ssh(f'[ "$(nvram get wans_routing_rulelist)" = "$(cat {root}/pins.original)" ] || exit 1\ntouch {root}/pin-removed\nnvram set "wans_routing_rulelist=$(cat {root}/pins.modified)"\n')
        # A cold view taken while our original kernel Primary pin is retained
        # safely adopts the live source without deleting any state. Earlier
        # synthetic tests can leave mixed-WAN tuples until normal expiry.
        time.sleep(3)
        ssh(ctl + 'restart\n')
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            text = status()
            if f'src={client} wan=0 ' in text and 'transitional=' not in text:
                break
            time.sleep(3)
        else:
            raise AssertionError(('safe adoption timed out', text))
        record('live_unpin_adopts_existing_primary', text)
        ssh(f'ip -4 rule del priority 100 from {client}/32 lookup wan0\n')
        download('affinity')
        text = status()
        assert 'uncertain=no' in text and 'refresh_failed=0' in text, text
        assert int(re.search(r'admissions=(\d+)', text).group(1)) >= 3, text
        ct = ssh(f'conntrack -L -s {client} -o extended,id 2>/dev/null\n')
        for port in range(48010, 48013):
            assert any(f'sport={port} ' in row and 'mark=2147483648' in row for row in ct.splitlines()), ct
        record('https_downloads_admitted_and_marked_primary', text)
        acceleration = ssh('fc status\n')
        assert 'HW Acceleration <Enabled>' in acceleration
        record('hardware_acceleration_remains_enabled', acceleration)
        ssh(ctl + 'stop\n')
        ssh(ctl + 'reconcile\n')
        assert not ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip()
        record('manual_stop_is_respected', True)
    finally:
        ssh(f'sh {root}/cleanup\n')
        for name in ['status', 'wan-affinity.log', 'supervisor.log']:
            (a.evidence / name).write_text(ssh(f'cat {run}/{name} 2>/dev/null || true\n'))
        after = {k: ssh(v + '\n') for k, v in {
            'firewall': 'iptables-save', 'rules': 'ip -4 rule show',
            'watchdog': 'cat /tmp/wan-watchdog/wan-watchdog.pid',
            'hooks': 'md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start'}.items()}
        restored = {k: before[k] == after[k] for k in before}
        normalize = lambda text: [re.sub(r'\[\d+:\d+\]', '[0:0]', line)
                                  for line in text.splitlines() if line and not line.startswith('#')]
        restored['firewall'] = normalize(before['firewall']) == normalize(after['firewall'])
        restored['rules'] = sorted(before['rules'].splitlines()) == sorted(after['rules'].splitlines())
        for key in before:
            (a.evidence / (key + '.before')).write_text(before[key])
            (a.evidence / (key + '.after')).write_text(after[key])
        restored['pins'] = ssh('nvram get wans_routing_rulelist\n').strip() == pins
        restored['queue'] = not ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip()
        record('restoration', restored)
        assert all(restored.values()), restored
    print('SERVICE QUALIFICATION PASSED', flush=True)


if __name__ == '__main__':
    main()
