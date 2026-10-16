#!/usr/bin/env python3
"""Explicit, guarded persistent install/upgrade/rollback/removal qualification.

Requires no existing affinity installation and a GUI-Primary-pinned Mac mini.
All forwarding scope stays on that pinned client. Restores the original hooks;
never commits NVRAM, changes WAN state, rebuilds shared rules or flushes states.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'wan-watchdog/router-tests'))
from router_ssh import MUX


def ssh(code, host='router', timeout=45):
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', *(MUX if host == 'router' else []),
                        host, 'sh -s'], input=code, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((p.returncode, p.stdout, p.stderr))
    return p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allow-install-tests', action='store_true', required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    a = ap.parse_args()
    a.evidence.mkdir(parents=True, exist_ok=True)
    root = '/tmp/wa-install-' + uuid.uuid4().hex[:8]
    base = '/jffs/wan-affinity'
    ctl = base + '/wan-affinity.sh '
    ssh(f'set -e\ntest ! -e {base}\ntest ! -e /tmp/wan-affinity/owned-chain\n')
    assert not ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip()
    client = ssh("ifconfig en0 | awk '$1==\"inet\" {print $2;exit}'\n", 'alexs-mac-mini').strip()
    assert re.fullmatch(r'192\.168\.1\.\d+', client), client
    pins = ssh('nvram get wans_routing_rulelist\n')
    assert '<' + client + '>all>0' in pins
    commands = {'hooks': 'md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start',
                'rules': 'ip -4 rule show', 'firewall': 'iptables-save',
                'watchdog': 'cat /tmp/wan-watchdog/wan-watchdog.pid'}
    before = {k: ssh(v + '\n') for k, v in commands.items()}
    ssh(f'umask 077\nmkdir {root}\nmkdir {root}/bundle\n')
    with (REPO / 'wan-affinity/build/wan-affinity-aarch64.tar.gz').open('rb') as f:
        subprocess.run(['ssh', *MUX, 'router', f'cat >{root}/bundle.tar.gz'], stdin=f, check=True)
    ssh(f'tar -xzf {root}/bundle.tar.gz -C {root}/bundle\n')
    guard = f'''#!/bin/sh
i=0
while [ "$i" -lt 600 ];do
 sleep 1;[ -f {root}/armed ] || exit 0;i=$((i+1))
done
if [ -f {base}/wan-affinity.sh ];then sh {base}/wan-affinity.sh uninstall;fi
'''
    ssh(f'cat >{root}/guard <<\'EOF\'\n{guard}EOF\ntouch {root}/armed\nnohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\n')
    results = {}

    def record(key, value):
        results[key] = value
        (a.evidence / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
        print(key, json.dumps(value), flush=True)

    def pid():
        return ssh('cat /tmp/wan-affinity/wan-affinity.pid\n').strip()

    def ready():
        text = ssh(ctl + 'status\n')
        assert 'jumps=1' in text and 'enhanced=yes' in text and 'uncertain=no' in text, text
        assert 'refresh_failed=0' in text, text
        return text

    try:
        record('fresh_disabled_install', ssh(f'sh {root}/bundle/install.sh --install\n'))
        assert not ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip()
        cfg = f'''ENABLED=1
MODE=sticky
CLIENT={client}
LAN_SCOPE=0
SUPERVISE=0
QUEUE_NUM=20880
IDLE_SECONDS=0
HEALTH_SOURCE=socket
HEALTH_SOCKET=/tmp/wan-watchdog/health.sock
'''
        ssh(f'cat >{base}/wan-affinity.conf <<\'CFG\'\n{cfg}CFG\n' + ctl + 'start\n')
        record('scoped_persistent_start', ready())
        old = pid()
        record('upgrade', ssh(f'sh {root}/bundle/install.sh --upgrade\n'))
        assert old != pid() and cfg == ssh(f'cat {base}/wan-affinity.conf\n')
        ready()
        record('upgrade_preserves_config_and_restarts_owner', True)
        old = pid()
        record('previous_version_rollback', ssh(f'sh {base}/rollback.sh --rollback\n'))
        assert old != pid() and cfg == ssh(f'cat {base}/wan-affinity.conf\n')
        ready()
        # Installer fault injection: config validation succeeds, daemon launch
        # fails. Only our scoped service is stopped; native routing stays live.
        ssh(f'cp -R {root}/bundle {root}/bad-bundle\ncat >{root}/bad-bundle/wan-affinity <<\'BAD\'\n#!/bin/sh\nfor arg in "$@";do [ "$arg" != --check-config ] || exit 0;done\nexit 1\nBAD\nchmod 700 {root}/bad-bundle/wan-affinity\n')
        output = ssh(f'if sh {root}/bad-bundle/install.sh --upgrade 2>&1;then exit 1;fi\n', timeout=60)
        assert 'restoring the previous version' in output, output
        ready()
        assert cfg == ssh(f'cat {base}/wan-affinity.conf\n')
        ssh(f'cmp -s {root}/bundle/wan-affinity {base}/wan-affinity\n')
        record('failed_upgrade_restores_healthy_previous_binary', True)
        ssh(ctl + 'disable\n')
        assert 'ENABLED=0' in ssh(f'cat {base}/wan-affinity.conf\n')
        assert not ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip()
        ssh(ctl + 'reconcile\n')
        assert not ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip()
        record('persistent_disable_is_respected', True)
    finally:
        ssh(f'if [ ! -f {base}/wan-affinity.sh ];then exit 0;fi\nsh {base}/wan-affinity.sh uninstall\n', timeout=60)
        after = {k: ssh(v + '\n') for k, v in commands.items()}
        normalize = lambda text: [re.sub(r'\[\d+:\d+\]', '[0:0]', line)
                                  for line in text.splitlines() if line and not line.startswith('#')]
        restored = {k: before[k] == after[k] for k in before}
        restored['firewall'] = normalize(before['firewall']) == normalize(after['firewall'])
        restored['rules'] = sorted(before['rules'].splitlines()) == sorted(after['rules'].splitlines())
        restored['pins'] = pins == ssh('nvram get wans_routing_rulelist\n')
        restored['queue'] = not ssh('cat /proc/net/netfilter/nfnetlink_queue\n').strip()
        for key in before:
            (a.evidence / (key + '.before')).write_text(before[key])
            (a.evidence / (key + '.after')).write_text(after[key])
        record('uninstall_restoration', restored)
        assert all(restored.values()), restored
        ssh(f'rm -f {root}/armed\n')
    print('INSTALL QUALIFICATION PASSED', flush=True)


if __name__ == '__main__':
    main()
