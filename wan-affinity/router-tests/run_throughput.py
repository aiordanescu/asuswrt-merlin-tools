#!/usr/bin/env python3
"""Guarded same-WAN bulk comparison while production affinity stays running.

Baseline only pre-marks the Mini's benchmark HTTPS connections; normal mode
must receive the same mark from affinity. No global disable/offload/WAN change.
"""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

REPO = Path(__file__).resolve().parents[2]
DOWNLOAD_HOST = 'pdx1-hil.speedtest.network.ovh.net'
DOWNLOAD_URL = 'https://' + DOWNLOAD_HOST + '/files/10Gb.dat'
PROBE_URL = 'https://' + DOWNLOAD_HOST + '/'
sys.path.insert(0, str(REPO / 'wan-watchdog/router-tests'))
from router_ssh import MUX


def ssh(code, host='router', timeout=20):
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', *(MUX if host == 'router' else []),
                        host, 'sh -s'], input=code, text=True, capture_output=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError((host, p.returncode, p.stdout, p.stderr))
    return p.stdout


def bulk_flows(text, client, remote, port):
    return [row for row in text.splitlines() if f'src={client} dst={remote} ' in row and
            'dport=443 ' in row and port <= int(re.search(r'\bsport=(\d+)', row)[1]) <= port + 15]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--allow-client-test', action='store_true', required=True)
    ap.add_argument('--full-speed', action='store_true', required=True)
    ap.add_argument('--evidence', type=Path, required=True)
    ap.add_argument('--duration', type=int, default=30, choices=range(10, 31))
    a = ap.parse_args()
    a.evidence.mkdir(parents=True, exist_ok=True)
    root = '/tmp/wa-throughput-' + uuid.uuid4().hex[:8]
    mini_root = root + '-client'
    before = {k: ssh(v + '\n') for k, v in {
        'pins': 'nvram get wans_routing_rulelist', 'rules': 'ip -4 rule show',
        'firewall': 'iptables-save', 'affinity': 'cat /tmp/wan-affinity/wan-affinity.pid',
        'watchdog': 'cat /tmp/wan-watchdog/wan-watchdog.pid',
        'hooks': 'md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start'}.items()}
    pid = before['affinity'].strip()
    client = ssh("ifconfig en0 | awk '$1==\"inet\" {print $2;exit}'\n", 'alexs-mac-mini').strip()
    ipaddress.IPv4Address(client)
    assert client.startswith('192.168.1.')
    remote = ssh(f"python3 - <<'PY'\nimport socket\nprint(socket.getaddrinfo('{DOWNLOAD_HOST}',443,socket.AF_INET,socket.SOCK_STREAM)[0][4][0])\nPY\n", 'alexs-mac-mini').strip()
    ipaddress.IPv4Address(remote)
    # Validate the endpoint before changing even the client's pin. No body.
    code = ssh(f'curl -q -4 --noproxy \'*\' --http1.1 --interface {client} '
               f'--resolve {DOWNLOAD_HOST}:443:{remote} --connect-timeout 3 '
               f'--max-time 5 -fsSI -o /dev/null -w \'%{{http_code}}\' {DOWNLOAD_URL}\n',
               'alexs-mac-mini')
    assert code == '200', code
    primary = ssh('nvram get wan0_ipaddr\n').strip()
    ipaddress.IPv4Address(primary)
    # Never confuse an earlier connection with evidence for a new admission.
    ct = ssh(f'conntrack -L -s {client} -o extended,id 2>/dev/null\n')
    for port in [51000, 52000, 55000]:
        assert not any(f'dst={remote} ' in row and
                       port <= int(re.search(r'\bsport=(\d+)', row)[1]) <= port + 199
                       for row in ct.splitlines() if 'sport=' in row), 'test ports still occupied'
    pins = before['pins'].strip()
    entry = '<' + client + '>all>0'
    assert pins.count(entry) == 1
    assert sum(bool(re.fullmatch(r'100:\s+from ' + re.escape(client) + r'(?:/32)?\s+lookup wan0\s*',
                                line)) for line in before['rules'].splitlines()) == 1
    modified = pins.replace(entry, '')
    iface = ssh('nvram get lan_ifname\n').strip()
    assert re.fullmatch(r'[A-Za-z0-9_.:-]+', iface)
    ssh(f'umask 077\nmkdir {root}\n')
    ssh(f'umask 077\nmkdir {mini_root}\n', 'alexs-mac-mini')
    source = (REPO / 'wan-affinity/router-tests/throughput_client.py').read_text()
    ssh(f'cat >{mini_root}/client.py <<\'SOURCE\'\n{source}SOURCE\n', 'alexs-mac-mini')
    ssh(f'cat >{root}/pins.original <<\'PINS\'\n{pins}\nPINS\ncat >{root}/pins.modified <<\'PINS\'\n{modified}\nPINS\n')
    cleanup = f'''#!/bin/sh
exec 9>{root}/cleanup.lock
flock -x 9
if [ -r {root}/baseline-rule ];then
 read record <{root}/baseline-rule
 set -- $record
 while iptables -t mangle -C PREROUTING "$@" 2>/dev/null;do iptables -t mangle -D PREROUTING "$@" || exit 1;done
 rm -f {root}/baseline-rule
fi
if [ -f {root}/pin-removed ];then
 original=$(cat {root}/pins.original);modified=$(cat {root}/pins.modified)
 current=$(nvram get wans_routing_rulelist)
 if [ "$current" = "$modified" ];then nvram set "wans_routing_rulelist=$original"
 else case "$current" in *'<{client}>all>'*) ;; *) nvram set "wans_routing_rulelist=$current{entry}";;esac;fi
 case "$(nvram get wans_routing_rulelist)" in *'{entry}'*)
  ip -4 rule show | awk '$1=="100:" && $3=="{client}" && $5=="wan0" {{f=1}}END{{exit !f}}' || ip -4 rule add priority 100 from {client}/32 lookup wan0
 ;;esac
 rm -f {root}/pin-removed
fi
rm -f {root}/armed
'''
    ssh(f'cat >{root}/cleanup <<\'CLEAN\'\n{cleanup}CLEAN\nsh -n {root}/cleanup\ncat >{root}/guard <<\'GUARD\'\n#!/bin/sh\ni=0\nwhile [ "$i" -lt 600 ];do sleep 1;[ -f {root}/armed ] || exit 0;i=$((i+1));done\nsh {root}/cleanup\nGUARD\ntouch {root}/armed\nnohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\n')
    result = dict(root=root, mini_root=mini_root, client=client, remote=remote,
                  host=DOWNLOAD_HOST, url=DOWNLOAD_URL,
                  started_utc=datetime.now(timezone.utc).isoformat(), phases=[])
    for k, value in before.items():
        (a.evidence / (k + '.before')).write_text(value)

    def save():
        (a.evidence / 'results.json').write_text(json.dumps(result, indent=2) + '\n')

    def healthy():
        assert pid == ssh('cat /tmp/wan-affinity/wan-affinity.pid\n').strip()
        s = ssh('cat /tmp/wan-affinity/status\n')
        assert 'mode=sticky enhanced=yes uncertain=no wan0=eligible wan1=eligible' in s, s
        assert 'refresh_failed=0' in s, s
        h = json.loads(ssh('/jffs/wan-watchdog/service/wan-healthctl\n'))
        assert h['fresh'] and h['desired'] == h['applied'] == -1 and not h['routing_error'], h
        assert all(w['eligible'] and not w['bad'] for w in h['wan']), h
        q = ssh('cat /proc/net/netfilter/nfnetlink_queue\n').split()
        assert q[0] == '20880' and q[5:7] == ['0', '0'], q
        return s

    def flow_samples(text, port):
        rows = []
        for line in text.splitlines():
            addresses = re.findall(r'<([0-9.]+):(\d+)>', line)
            if len(addresses) != 2:
                continue
            pairs = [('.'.join(str(int(x)) for x in address.split('.')), int(p)) for address, p in addresses]
            if not any(address == remote and p == 443 for address, p in pairs):
                continue
            if not any(port <= p <= port + 15 for _, p in pairs):
                continue
            fields = line.split()
            rows.append(dict(id=fields[0], hardware_template=fields[5], hardware_hits=int(fields[6]),
                             endpoints=pairs))
        return rows

    def phase(label, port, baseline=False, duration=None, limit=0):
        healthy()
        if baseline:
            rule = f'-i {iface} -s {client}/32 -d {remote}/32 -p tcp --sport {port}:{port+199} --dport 443 -m conntrack --ctstate NEW --ctdir ORIGINAL -j MARK --set-xmark 0x80000000/0xf0000000'
            ssh(f'set -e\nprintf \'%s\\n\' {shlex.quote(rule)} >{root}/baseline-rule\npos=$(iptables -t mangle -S PREROUTING | awk \'$1=="-A" {{n++;for(i=1;i<NF;i++)if($i=="-j" && $(i+1)=="WA_ADMIT"){{print n;exit}}}}\')\n[ -n "$pos" ]\niptables -t mangle -I PREROUTING "$pos" {rule}\n')
        c = dict(label=label, client=client, remote=remote, host=DOWNLOAD_HOST,
                 url=DOWNLOAD_URL, probe_url=PROBE_URL, port=port,
                 duration=duration or a.duration, limit_bytes=limit)
        ssh(f'rm -f {mini_root}/stop\ncat >{mini_root}/phase.json <<\'JSON\'\n{json.dumps(c)}\nJSON\n', 'alexs-mac-mini')
        status_before = healthy()
        samples = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(ssh, f'python3 {mini_root}/client.py {mini_root}/phase.json\n', 'alexs-mac-mini', 55)
            try:
                while not job.done():
                    time.sleep(2)
                    status = healthy()
                    flow = flow_samples(ssh("awk '/:5[1-5][0-9][0-9][0-9]>/ {print}' /proc/fcache/nflist\n"), port)
                    samples.append(dict(status=status, flows=flow, uptime=ssh('cat /proc/uptime\n').strip()))
                output = job.result()
            except BaseException:
                ssh(f'touch {mini_root}/stop\n', 'alexs-mac-mini')
                raise
        (a.evidence / (label + '.client.log')).write_text(output)
        final = [line[7:] for line in output.splitlines() if line.startswith('RESULT ')]
        assert len(final) == 1, output
        metrics = json.loads(final[0])
        phase_result = dict(label=label, baseline=baseline, metrics=metrics,
                            router_samples=samples, status_before=status_before, status_after=healthy())
        result['phases'].append(phase_result)
        save()
        assert metrics['seconds'] >= c['duration'] - .5 and metrics['bytes'] > 1000000, \
            f'{label}: incomplete transfer; see {a.evidence / (label + ".client.log")}'
        assert metrics['curl_exit'] == -15 and not metrics['curl_error'], \
            f'{label}: unexpected curl exit {metrics["curl_exit"]}'
        assert metrics['probes'] and all(p['code'] == 0 and p['status'] == 200 for p in metrics['probes']), metrics['probes']
        codes = re.findall(r'^HTTP/\S+ (\d+)', metrics['headers'], flags=re.M)
        assert codes and all(code == '200' for code in codes), codes
        ct = ssh(f'conntrack -L -s {client} -o extended,id 2>/dev/null\n')
        (a.evidence / (label + '.conntrack')).write_text(ct)
        matching = bulk_flows(ct, client, remote, port)
        assert matching and all('mark=2147483648' in row and f'dst={primary} ' in row
                                for row in matching), matching
        hits = {}
        for sample in samples:
            for flow in sample['flows']:
                if int(flow['hardware_template'], 16):
                    hits.setdefault(flow['id'], []).append(flow['hardware_hits'])
        assert any(len(values) > 1 and values[-1] > values[0]
                   for values in hits.values()), 'no increasing bulk hardware hit counter'
        if baseline:
            ssh(f'set -e\nread record <{root}/baseline-rule\niptables -t mangle -D PREROUTING $record\nrm {root}/baseline-rule\n')
        print(json.dumps(dict(label=label, seconds=metrics['seconds'], mbps=metrics['mbps'],
                             probes=len(metrics['probes']), hardware_hits=max((f['hardware_hits'] for s in samples for f in s['flows']),default=0))), flush=True)

    try:
        healthy()
        ssh(f'set -e\n[ "$(nvram get wans_routing_rulelist)" = "$(cat {root}/pins.original)" ]\ntouch {root}/pin-removed\nnvram set "wans_routing_rulelist=$(cat {root}/pins.modified)"\n')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if f'src={client} wan=0 ' in healthy():
                break
            time.sleep(.5)
        else:
            raise AssertionError('could not safely adopt existing Primary connections')
        ssh(f'ip -4 rule del priority 100 from {client}/32 lookup wan0\n')
        phase('capped-smoke', 55000, duration=5, limit=12500000)
        phase('bypass', 51000, baseline=True)
        time.sleep(3)
        phase('affinity', 52000)
        print('THROUGHPUT COMPARISON COMPLETE', flush=True)
    finally:
        try:
            ssh(f'touch {mini_root}/stop\n', 'alexs-mac-mini')
        finally:
            ssh(f'sh {root}/cleanup\n')
        after = {k: ssh(v + '\n') for k, v in {
            'pins': 'nvram get wans_routing_rulelist', 'rules': 'ip -4 rule show',
            'firewall': 'iptables-save', 'affinity': 'cat /tmp/wan-affinity/wan-affinity.pid',
            'watchdog': 'cat /tmp/wan-watchdog/wan-watchdog.pid',
            'hooks': 'md5sum /jffs/scripts/services-start /jffs/scripts/firewall-start'}.items()}
        normalize = lambda s: [re.sub(r'\[\d+:\d+\]', '[0:0]', line) for line in s.splitlines() if line and not line.startswith('#')]
        checks = {k: before[k] == after[k] for k in before}
        checks['rules'] = sorted(before['rules'].splitlines()) == sorted(after['rules'].splitlines())
        checks['firewall'] = normalize(before['firewall']) == normalize(after['firewall'])
        result['restoration'] = checks
        save()
        for k, value in after.items():
            (a.evidence / (k + '.after')).write_text(value)
        print('RESTORATION ' + json.dumps(checks), flush=True)
        assert all(checks.values()), checks
        ssh(f'rm -rf {mini_root}\n', 'alexs-mac-mini')
        ssh(f'rm -rf {root}\n')


if __name__ == '__main__':
    main()
