#!/usr/bin/env python3
"""Explicitly opted-in live-kernel tests, limited to dedicated loopback UDP.

Requires a prebuilt static nfq-probe already copied into a unique router /tmp
directory. Never invoked by the unit suite. The engine runs on the Mac; the
router binary is only a netlink bridge and synthetic traffic generator. This
is capability/integration evidence, not a production router daemon.
"""
import argparse
import concurrent.futures
import hashlib
import json
import pathlib
import queue
import random
import re
import shlex
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from wan_affinity.engine import Config, Engine
from wan_affinity.health import HealthView, WanFacts
from wan_affinity.model import Domain, Packet
from wan_affinity.reconcile import read_dump
from wan_affinity.selection import StickySelector

SRC, DST, PORT, QNUM = "127.88.0.2", "127.88.0.1", 58888, 58888


def ssh(command, **kwargs):
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "router", command],
        text=True, capture_output=True, timeout=kwargs.pop("timeout", 12), **kwargs
    )


def checked(command):
    p = ssh(command)
    if p.returncode:
        raise RuntimeError((command, p.returncode, p.stdout, p.stderr))
    return p.stdout


class Child:
    def __init__(self, command):
        self.proc = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "router", command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
        self.q = queue.Queue()
        self.lines = []

        def read():
            for line in self.proc.stdout:
                self.lines.append(line.rstrip())
                self.q.put(line.rstrip())
            self.q.put(None)

        self.thread = threading.Thread(target=read, daemon=True)
        self.thread.start()

    def line(self, timeout=5):
        line = self.q.get(timeout=timeout)
        if line is None:
            raise RuntimeError(("child ended", self.proc.poll(), self.proc.stderr.read()))
        return line

    def verdict(self, packet_id, mark):
        self.proc.stdin.write("R %d %08x\n" % (packet_id, mark))
        self.proc.stdin.flush()


def parse_packet(line):
    tag, ident, mark, src, dst, proto, sport, dport = line.split()
    assert tag == "P" and src == SRC and dst == DST and int(dport) == PORT, line
    assert proto == "17", line
    ctid = "udp|%s|%s|%s|%s" % (src, dst, sport, dport)
    return int(ident), Packet(ctid, src, dst, "udp", mark=int(mark, 16))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-router-loopback-tests", action="store_true", required=True)
    ap.add_argument("--backup", type=pathlib.Path, required=True)
    ap.add_argument("--remote", required=True)
    args = ap.parse_args()
    root = args.remote
    assert re.fullmatch(r"/tmp/wan-affinity-test-[0-9T]+", root), root
    chain = "WA_T_" + root[-6:]
    binary = root + "/nfq-probe"
    jump = "-s %s/32 -d %s/32 -o lo -p udp --dport %d -j %s" % (SRC,DST,PORT,chain)
    results = {}
    bridge = sink = None

    def record(key, value):
        results[key] = value
        (args.backup / "kernel-results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(key + ": " + json.dumps(value), flush=True)

    def dump(engine):
        taken = time.monotonic()
        p = ssh("conntrack -L -p udp --orig-src %s --orig-dst %s --dport %d -o extended" % (SRC,DST,PORT))
        if p.returncode:
            raise RuntimeError(("conntrack dump",p.returncode,p.stderr))
        count = re.search(r"(\d+) flow entries have been shown", p.stderr)
        if not count:
            raise RuntimeError(("missing native dump count",p.stderr))
        rows = []
        marks = []
        for line in p.stdout.splitlines():
            tokens = line.split()
            kv = {}
            for token in tokens:
                if "=" in token:
                    k,v = token.split("=",1)
                    if k not in kv: kv[k]=v
            assert kv["src"] == SRC and kv["dst"] == DST, line
            assert int(kv["dport"]) == PORT, line
            mark = int(kv.get("mark","0"),0)
            ctid = "udp|%s|%s|%s|%s" % (SRC,DST,kv["sport"],kv["dport"])
            rows.append("%s udp %s %s orig mark=%08x assured=%d" % (ctid,SRC,DST,mark,"[ASSURED]" in line))
            marks.append(mark)
        assert len(rows) == int(count[1]), (len(rows),count[1])
        text = "#wan-affinity-conntrack v1 count=%d\n%s\n--end--\n" % (len(rows),"\n".join(rows))
        result = read_dump(True,text,taken)
        assert result.valid
        engine.apply_reconcile(result)
        return marks

    def delete_test_flows():
        p=ssh("conntrack -D -p udp --orig-src %s --orig-dst %s --dport %d" % (SRC,DST,PORT))
        if p.returncode not in (0,1): raise RuntimeError(("delete test flows",p.stderr))

    def send(count, base):
        return checked("%s send %d %d %d" % (binary,count,base,PORT))

    # Refuse collisions before preparing cleanup or touching any firewall rule.
    p=ssh("iptables -t mangle -S %s" % chain)
    assert p.returncode != 0, "test chain already exists"
    queues=checked("cat /proc/net/netfilter/nfnetlink_queue")
    assert not any(line.split()[0]==str(QNUM) for line in queues.splitlines()), "queue collision"
    existing=ssh("conntrack -L -p udp --orig-src %s --orig-dst %s --dport %d" % (SRC,DST,PORT))
    assert existing.returncode == 0 and not existing.stdout.strip(), "test-flow collision"

    cleanup = """#!/bin/sh
ROOT=%s
rm -f "$ROOT/armed"
while iptables -t mangle -C OUTPUT %s 2>/dev/null; do
  iptables -t mangle -D OUTPUT %s || break
done
iptables -t mangle -F %s 2>/dev/null
iptables -t mangle -X %s 2>/dev/null
for file in bridge.pid sink.pid; do
  if [ -r "$ROOT/$file" ]; then
    read test_pid < "$ROOT/$file"
    case "$test_pid" in ''|*[!0-9]*) continue ;; esac
    if [ -r "/proc/$test_pid/cmdline" ]; then
      test_cmd=$(tr '\\000' ' ' < "/proc/$test_pid/cmdline")
      case "$test_cmd" in "$ROOT/nfq-probe "*) kill "$test_pid" 2>/dev/null ;; esac
    fi
  fi
done
conntrack -D -p udp --orig-src %s --orig-dst %s --dport %d >/dev/null 2>&1
date > "$ROOT/cleaned"
""" % (shlex.quote(root),jump,jump,chain,chain,SRC,DST,PORT)
    # Upload as stdin: no shell interpolation of the script body.
    p=ssh("cat > %s/cleanup.sh; chmod 700 %s/cleanup.sh" % (root,root),input=cleanup)
    assert p.returncode == 0, p.stderr
    guard="#!/bin/sh\nsleep 180\n[ ! -f %s/armed ] || /bin/sh %s/cleanup.sh\n" % (root,root)
    p=ssh("cat > %s/guard.sh; chmod 700 %s/guard.sh" % (root,root),input=guard)
    assert p.returncode == 0,p.stderr
    checked("touch %s/armed; (trap '' HUP; exec /bin/sh %s/guard.sh) </dev/null >%s/guard.log 2>&1 & echo $! >%s/guard.pid" % (root,root,root,root))
    guard_pid=int(checked("cat %s/guard.pid" % root).strip())
    checked("kill -0 %d; test -f %s/armed" % (guard_pid,root))
    record("independent_cleanup_armed", {"seconds":180,"pid":guard_pid,"chain":chain})

    try:
        sink=Child("echo $$ > %s/sink.pid; exec %s sink 120 %d" % (root,binary,PORT))
        assert sink.line() == "SINK_READY"
        bridge=Child("echo $$ > %s/bridge.pid; exec %s bridge %d 120 1024" % (root,binary,QNUM))
        assert bridge.line().startswith("READY queue=58888 fail_open=1")
        config="""set -e
iptables -t mangle -N %s
iptables -t mangle -A %s -j CONNMARK --set-xmark 0x5a/0xff
iptables -t mangle -A %s -j MARK --set-xmark 0x42/0xff
iptables -t mangle -A %s -m mark --mark 0x0/0xf0000000 -m conntrack --ctstate NEW --ctdir ORIGINAL -j NFQUEUE --queue-num %d --queue-bypass
iptables -t mangle -A %s -j CONNMARK --save-mark --nfmask 0xf0000000 --ctmask 0xf0000000
iptables -t mangle -A %s -m mark --mark 0x80000042/0xf00000ff -m connmark --mark 0x8000005a/0xf00000ff -j RETURN
iptables -t mangle -A %s -m mark --mark 0x90000042/0xf00000ff -m connmark --mark 0x9000005a/0xf00000ff -j RETURN
iptables -t mangle -I OUTPUT 1 %s
""" % (chain,chain,chain,chain,QNUM,chain,chain,chain,jump)
        checked(config)
        # Deliberately synthetic loopback policy domain. No production exclusions
        # are removed on the router. Health is a fixed eligible test fixture.
        facts={u:WanFacts(enable=1,state_t=2,ipaddr="192.0.2.%d"%(u+2),gateway="192.0.2.1",ifname="test",route_ready=True) for u in (0,1)}
        engine=Engine(Domain(lan_cidrs=(SRC+"/32",),local_cidrs=()),HealthView(facts),StickySelector(random.Random(9)),time.monotonic,Config())
        seen=[]
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job=pool.submit(checked,"%s parallel 4 24 50000 %d" % (binary,PORT))
            for i in range(96):
                ident,pkt=parse_packet(bridge.line(timeout=8))
                result=engine.admission_decision(pkt)
                assert result.action == "mark", result
                seen.append(result.mark)
                bridge.verdict(ident,result.mark)
            job.result(timeout=10)
        time.sleep(0.3)
        marks=dump(engine)
        assert len(marks)==96, len(marks)
        assert set(seen)=={0x80000042}, set(seen)
        assert set(marks)=={0x8000005a},set(marks)
        record("concurrent_udp_first_connections",{"count":96,"packet_marks":[hex(x) for x in set(seen)],"conntrack_marks":[hex(x) for x in set(marks)]})
        time.sleep(3)
        assert len(dump(engine))==96
        assert engine.tick()==[]
        record("quiet_confirmed_states_retained",True)
        delete_test_flows()
        assert dump(engine)==[]
        assert len(engine.tick())==1
        record("confirmed_empty_expiry",True)

        # Create an admission while the actual kernel packet remains queued and
        # unconfirmed. This can expose assumptions hidden by model-only tests.
        send(1,51200)
        ident,pkt=parse_packet(bridge.line())
        result=engine.admission_decision(pkt)
        assert result.action == "mark"
        pending_mark=result.mark
        time.sleep(3)
        pending_dump=dump(engine)
        expired=engine.tick()
        record("pending_unconfirmed_after_3_seconds",{"visible_kernel_states":len(pending_dump),"association_retained":SRC in engine.assoc,"expired":expired,"uncertain":engine.uncertain,"warning_seconds":engine.config.provisional_warning_seconds})
        bridge.verdict(ident,pending_mark)
        time.sleep(0.2)
        assert dump(engine)==[0x8000005a],"released packet mark must persist"
        # Explicitly validate WAN1 mark transport, independent of model selection.
        send(1,51201)
        ident,pkt=parse_packet(bridge.line())
        bridge.verdict(ident,0x90000042)
        time.sleep(0.2)
        assert 0x9000005a in dump(engine)
        record("both_wan_verdict_marks_and_mask_preservation",True)
        counters=checked("iptables -t mangle -L %s -n -v -x" % chain)
        (args.backup/"mangle-test-counters.txt").write_text(counters)
        record("immediate_save_mark_following_rule",counters)
        bridge.proc.stdin.write("STOP\n");bridge.proc.stdin.flush()
        bridge.proc.wait(timeout=5)
        assert not checked("cat /proc/net/netfilter/nfnetlink_queue").strip()
        before=len(dump(engine))
        send(3,51300)
        time.sleep(0.2)
        assert len(dump(engine))==before+3
        record("absent_listener_queue_bypass",True)

        (args.backup/"bridge-admission.stdout.txt").write_text("\n".join(bridge.lines)+"\n")
        (args.backup/"bridge-admission.stderr.txt").write_text(bridge.proc.stderr.read())
        bridge=Child("echo $$ > %s/bridge.pid; exec %s bridge %d 30 8" % (root,binary,QNUM))
        assert bridge.line().startswith("READY queue=58888 fail_open=1 maxlen=8")
        before=len(dump(engine))
        send(64,51400)
        time.sleep(0.3)
        queue_state=checked("cat /proc/net/netfilter/nfnetlink_queue").strip()
        assert int(queue_state.split()[2])==8,queue_state
        after_full=len(dump(engine))
        assert after_full==before+56,(before,after_full)
        record("queue_full_fail_open",{"sent":64,"held":8,"confirmed_bypass":after_full-before,"queue":queue_state})
        for _ in range(8):parse_packet(bridge.line())
        pid=int(checked("cat %s/bridge.pid" % root).strip())
        cmd=checked("tr '\\000' ' ' < /proc/%d/cmdline" % pid)
        assert cmd.startswith(binary+" bridge 58888 "),cmd
        checked("kill -KILL %d" % pid)
        bridge.proc.wait(timeout=5)
        assert not checked("cat /proc/net/netfilter/nfnetlink_queue").strip()
        send(3,51500)
        time.sleep(0.2)
        assert len(dump(engine))==after_full+3
        record("helper_crash",{"pending_packets_lost":8,"new_connections_bypass":3,"queue_unbound":True})
    finally:
        (args.backup/"mangle-before-cleanup.txt").write_text(ssh("iptables -t mangle -L %s -n -v -x" % chain).stdout)
        p=ssh("/bin/sh %s/cleanup.sh" % root)
        record("cleanup_exit",p.returncode)
        for child,name in ((bridge,"bridge"),(sink,"sink")):
            if child is not None:
                try: child.proc.wait(timeout=5)
                except subprocess.TimeoutExpired: child.proc.terminate();child.proc.wait(timeout=5)
                (args.backup/(name+".stdout.txt")).write_text("\n".join(child.lines)+"\n")
                (args.backup/(name+".stderr.txt")).write_text(child.proc.stderr.read())
        after=checked("iptables-save")
        (args.backup/"iptables-save.after.txt").write_text(after)
        def normalized(text):
            return [re.sub(r"\[\d+:\d+\]", "[COUNTERS]", line)
                    for line in text.splitlines() if not line.startswith("#")]
        record("firewall_rules_restored",normalized(after)==normalized((args.backup/"iptables-save.before.txt").read_text()))
        record("test_chain_absent",ssh("iptables -t mangle -S %s" % chain).returncode != 0)
        record("queue_bindings_after",checked("cat /proc/net/netfilter/nfnetlink_queue").strip())
        record("routing_rules_unchanged",checked("ip -4 rule show")==(args.backup/"ip-rule.before.txt").read_text())
        hashes=json.loads((args.backup/"scripts.before.json").read_text())
        unchanged=True
        for path,data in hashes.items():
            p=subprocess.run(["ssh","-o","BatchMode=yes","router","cat "+path],capture_output=True,check=True,timeout=10)
            unchanged &= hashlib.sha256(p.stdout).hexdigest()==data["sha256"]
        record("production_scripts_unchanged",unchanged)
        record("watchdog_status_after",checked("cat /tmp/wan-watchdog/status"))
        assert results["firewall_rules_restored"] and results["routing_rules_unchanged"] and unchanged

    pending_ok=results["pending_unconfirmed_after_3_seconds"]["association_retained"]
    record("loopback_kernel_gate", "PASS" if pending_ok else "PARTIAL: provisional admission expired while real packet remained queued")
    return 0 if pending_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
