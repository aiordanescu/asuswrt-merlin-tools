#!/usr/bin/env python3
"""Bounded, source-bound download and new-connection probes; no network edits."""
import argparse
import json
import os
from pathlib import Path
import resource
import select
import signal
import subprocess
import threading
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('config', type=Path)
    a = ap.parse_args()
    c = json.loads(a.config.read_text())
    duration = float(c['duration'])
    if not 1 <= duration <= 45:
        raise SystemExit('duration must be 1..45 seconds')
    root = a.config.parent
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    common = ['curl', '-q', '-4', '--noproxy', '*', '--http1.1',
              '--interface', c['client'], '--resolve', c['host'] + ':443:' + c['remote'],
              '--connect-timeout', '3', '-sS', '--fail', '--fail-early']
    probe_rows = []

    def probes():
        for i in range(90):
            if stopping.is_set():
                break
            began = time.monotonic()
            try:
                p = subprocess.run(common + ['--local-port', str(c['port'] + 100 + i),
                                   '--max-time', '4', '-o', '/dev/null',
                                   '-w', '%{json}', c['probe_url']],
                                   text=True, capture_output=True, timeout=6)
            except subprocess.TimeoutExpired:
                probe_rows.append(dict(port=c['port'] + 100 + i, code=-1,
                                       status=None, error='probe process timeout'))
                continue
            try:
                row = json.loads(p.stdout)
            except ValueError:
                row = {}
            probe_rows.append(dict(port=c['port'] + 100 + i, code=p.returncode,
                                   status=row.get('http_code'), connect=row.get('time_connect'),
                                   tls=row.get('time_appconnect'), ttfb=row.get('time_starttransfer'),
                                   total=row.get('time_total'), error=p.stderr))
            stopping.wait(max(0, 1 - (time.monotonic() - began)))

    headers = root / (c['label'] + '.headers')
    command = common + ['--local-port', str(c['port']) + '-' + str(c['port'] + 15),
                        '--max-time', str(duration + 2), '--dump-header', str(headers)]
    if c.get('limit_bytes'):
        command += ['--limit-rate', str(c['limit_bytes'])]
    # A large test file keeps one connection busy until the duration cutoff.
    command += [c['url']]
    cpu_start = time.process_time()
    start = time.monotonic()
    last = start
    total = last_total = 0
    samples = []
    bulk = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True)
    thread = threading.Thread(target=probes, daemon=True)
    thread.start()
    try:
        while not stopping.is_set() and time.monotonic() - start < duration:
            if (root / 'stop').exists():
                stopping.set()
                break
            ready, _, _ = select.select([bulk.stdout], [], [], .2)
            if ready:
                block = os.read(bulk.stdout.fileno(), 256 * 1024)
                if not block:
                    break
                total += len(block)
            now = time.monotonic()
            if now - last >= 1:
                samples.append(dict(seconds=now - last, bytes=total - last_total,
                                    mbps=(total - last_total) * 8 / (now - last) / 1e6))
                print('PROGRESS ' + json.dumps(dict(label=c['label'], elapsed=now - start,
                      bytes=total, mbps=samples[-1]['mbps'])), flush=True)
                last, last_total = now, total
    finally:
        elapsed = time.monotonic() - start
        stopping.set()
        if bulk.poll() is None:
            os.killpg(bulk.pid, signal.SIGTERM)
        try:
            _, error = bulk.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(bulk.pid, signal.SIGKILL)
            _, error = bulk.communicate(timeout=2)
        thread.join(timeout=7)
    result = dict(label=c['label'], seconds=elapsed, bytes=total, mbps=total * 8 / elapsed / 1e6,
                  samples=samples, probes=probe_rows, curl_exit=bulk.returncode,
                  curl_error=error.decode(errors='replace'),
                  client_python_cpu_seconds=time.process_time() - cpu_start,
                  client_children_cpu_seconds=sum(resource.getrusage(resource.RUSAGE_CHILDREN)[i]
                                                  for i in [0, 1]),
                  headers=headers.read_text() if headers.exists() else '')
    print('RESULT ' + json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
