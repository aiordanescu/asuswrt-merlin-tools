#!/usr/bin/env python3
"""Guarded one-client forwarding matrix. Never changes real WAN health."""
import argparse
import datetime
import json
from pathlib import Path
import re
import shlex
import subprocess
import time
import sys

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'wan-watchdog/router-tests'))
from router_ssh import MUX

def ssh(code,host='router',timeout=20):
    r=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=5',*(MUX if host=='router' else []),host,'sh -s'],
                     input=code,text=True,capture_output=True,timeout=timeout)
    if r.returncode:raise RuntimeError((host,r.returncode,r.stdout,r.stderr))
    return r.stdout

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True)
    ap.add_argument('--allow-client-test',action='store_true',required=True)
    ap.add_argument('--modes',choices=['sticky','affinity'],nargs='+',default=['sticky','affinity'])
    ap.add_argument('--controlled-ports',action='store_true',help='test binary and closed flow domain; no existing-flow adoption')
    ap.add_argument('--production-restart',action='store_true',help='guarded real publisher restart instead of synthetic health')
    a=ap.parse_args();a.evidence.mkdir(parents=True,exist_ok=True)
    assert not a.production_restart or a.controlled_ports
    stamp=datetime.datetime.now().strftime('%H%M%S');root='/tmp/ww-mini-'+stamp;chain='WA_SOCKET_'+stamp
    (a.evidence/'meta.json').write_text(json.dumps(dict(root=root,chain=chain))+'\n')
    client=ssh("ifconfig en0 | awk '$1==\"inet\" {print $2;exit}'\n",'alexs-mac-mini').strip()
    assert re.fullmatch(r'192\.168\.1\.\d+',client),client
    before={name:ssh(command+'\n') for name,command in {
        'pins':'nvram get wans_routing_rulelist','rules':'ip -4 rule show','firewall':'iptables-save',
        'production_pid':'cat /tmp/wan-watchdog/wan-watchdog.pid',
        'scripts':'md5sum /jffs/scripts/wan-watchdog /jffs/scripts/wan-watchdog-ddns /jffs/scripts/firewall-start /jffs/scripts/services-start',
        'queues':'cat /proc/net/netfilter/nfnetlink_queue'}.items()}
    for name,text in before.items():(a.evidence/(name+'.before')).write_text(text)
    assert not before['queues'].strip(),'foreign queue already bound'
    pins=before['pins'].rstrip('\n');entry='<'+client+'>all>0'
    assert pins.count(entry)==1,'test requires the existing exact Primary pin'
    modified=pins.replace(entry,'')
    ssh('umask 077\nmkdir '+root+'\nmkdir '+root+'/base '+root+'/run\n')
    files=[(REPO/('wan-affinity/build/wan-affinity-test' if a.controlled_ports else 'wan-affinity/build/wan-affinity'),'base/wan-affinity'),
           (REPO/'wan-affinity/wan-affinity.sh','base/wan-affinity.sh'),
           (REPO/'wan-affinity/build/health-fixture','fixture')]
    for source,target in files:
        with source.open('rb') as data:subprocess.run(['ssh',*MUX,'router','cat >'+root+'/'+target],stdin=data,check=True)
    ssh('chmod 700 '+root+'/base/wan-affinity '+root+'/fixture\ncat >'+root+'/pins.original <<\'PINS\'\n'+pins+'\nPINS\ncat >'+root+'/pins.modified <<\'PINS\'\n'+modified+'\nPINS\n')
    ctl=f'WA_BASE={root}/base WA_RUN={root}/run WA_CHAIN={chain} sh {root}/base/wan-affinity.sh '
    cleanup=f'''#!/bin/sh
mkdir {root}/cleaning 2>/dev/null || exit 0
rm -f {root}/armed
if [ -r {root}/port-range ];then
 read ports <{root}/port-range
 case "$ports" in 41000:41999|42000:42999|43000:43999|44000:44999) ;; *) exit 1;;esac
 for proto in tcp udp;do
  while iptables -t mangle -C PREROUTING -i br0 -s {client} -p "$proto" --sport "$ports" -m conntrack --ctstate NEW --ctdir ORIGINAL -j {chain} 2>/dev/null;do
   iptables -t mangle -D PREROUTING -i br0 -s {client} -p "$proto" --sport "$ports" -m conntrack --ctstate NEW --ctdir ORIGINAL -j {chain}
  done
 done
 if [ -f {root}/chain-owned ];then iptables -t mangle -F {chain};iptables -t mangle -X {chain};fi
 rm -f {root}/port-range {root}/chain-owned
fi
if [ -f {root}/pin-removed ]; then
 original=$(cat {root}/pins.original);modified=$(cat {root}/pins.modified)
 current=$(nvram get wans_routing_rulelist)
 if [ "$current" = "$modified" ]; then nvram set "wans_routing_rulelist=$original"
 else case "$current" in *'<{client}>all>'*) ;; *) nvram set "wans_routing_rulelist=$current{entry}";; esac;fi
 if ! ip -4 rule show | awk '$1=="100:" && $2=="from" && $3=="{client}" && $5=="wan0" {{f=1}}END{{exit !f}}';then
  ip -4 rule add priority 100 from {client}/32 lookup wan0
 fi
fi
{ctl}stop >/dev/null 2>&1
if [ -r {root}/fixture.pid ];then
 read p <{root}/fixture.pid
 case "$p" in ''|*[!0-9]*) ;; *) first=$(tr '\\000' '\\n' </proc/$p/cmdline 2>/dev/null | head -n1)
 if [ "$first" = {root}/fixture ];then kill -CONT "$p";kill -TERM "$p";fi;;esac
fi
date >{root}/cleaned
rmdir {root}/cleaning
'''
    ssh('cat >'+root+'/cleanup <<\'WW_UPLOAD\'\n'+cleanup+'WW_UPLOAD\n')
    guard=f'''#!/bin/sh
trap 'exit 0' TERM INT
i=0
while [ "$i" -lt 900 ];do
 sleep 1
 [ -f {root}/armed ] || exit 0
 i=$((i+1))
done
sh {root}/cleanup
'''
    ssh('cat >'+root+'/guard <<\'WW_UPLOAD\'\n'+guard+'WW_UPLOAD\nsh -n '+root+'/cleanup\nsh -n '+root+'/guard\n')
    ssh(f'touch {root}/armed\nnohup sh {root}/guard </dev/null >{root}/guard.log 2>&1 &\necho $! >{root}/guard.pid\n')
    results=[];port=46000;expected_pid=before['production_pid'];restarting=False
    def status():return ssh('cat '+root+'/run/status\n')
    def wait_health(token):
        end=time.monotonic()+16
        while time.monotonic()<end:
            text=status()
            if 'enhanced=yes' in text and token in text:return
            time.sleep(.3)
        raise AssertionError(('health convergence',token,text))
    def safe():
        if restarting:return
        assert expected_pid==ssh('cat /tmp/wan-watchdog/wan-watchdog.pid\n')
        s=ssh('cat /tmp/wan-watchdog/status\n')
        if 'monitor=1' in s:
            h=json.loads(ssh('/jffs/wan-watchdog/service/wan-healthctl\n'))
            assert all(w['eligible'] for w in h['wan']) and h['applied']==-1 and not h['routing_error'],h
        else:assert 'WAN0 usable' in s and 'WAN1 usable' in s and 'whole_failed=none' in s,s
    def fixture(primary,backup=1):
        facts=ssh('nvram get wan0_ipaddr\nnvram get wan0_ifname\nnvram get wan1_ipaddr\nnvram get wan1_ifname\n').splitlines()
        bad,eligible,recovered={'good':(0,1,1),'bad':(1,0,0),'held':(0,0,0)}[primary]
        text=f'0 {bad} {eligible} {recovered} {facts[0]} {facts[1]}\n1 0 {backup} {backup} {facts[2]} {facts[3]}\n'
        ssh('cat >'+root+'/fixture.conf.new <<\'HEALTH\'\n'+text+'HEALTH\nmv '+root+'/fixture.conf.new '+root+'/fixture.conf\n')
        return facts
    def start_fixture():
        ssh(f'nohup {root}/fixture --permit-test-fixture {root}/health.sock {root}/fixture.conf </dev/null >{root}/fixture.log 2>&1 &\necho $! >{root}/fixture.pid\n')
    def config(mode):
        return f'''ENABLED=1
CLIENT={client}
LAN_SCOPE=0
MODE={mode}
QUEUE_NUM=20880
RECONCILE_INTERVAL=2
IDLE_SECONDS=0
MAX_RECONCILE_SECONDS=15
AFFINITY_SEED=mini-test-0
HEALTH_SOURCE=watchdog
HEALTH_SOCKET={'/tmp/wan-watchdog/health.sock' if a.production_restart else root+'/health.sock'}
'''
    def protect():
        ssh(f'ip -4 rule show | awk \'$1=="100:" && $3=="{client}" && $5=="wan0" {{f=1}}END{{exit !f}}\' || ip -4 rule add priority 100 from {client}/32 lookup wan0\n')
    def start(mode):
        if a.controlled_ports:
            base=(41000 if mode=='sticky' else 42000) if a.production_restart else (43000 if mode=='sticky' else 44000)
            ct=ssh('conntrack -L -s '+client+' -o extended,id 2>/dev/null\n')
            assert not any(base<=int(n)<base+1000 for n in re.findall(r'\bsport=(\d+)',ct)),'test ports already active'
            ssh('cat >'+root+'/base/wan-affinity.conf <<\'CFG\'\n'+config(mode)+'CFG\n'+f'rm -f {root}/run/status\nnohup {root}/base/wan-affinity --config {root}/base/wan-affinity.conf --status-dir {root}/run --test-port-base {base} </dev/null >{root}/run/wan-affinity.log 2>&1 &\necho $! >{root}/run/wan-affinity.pid\n')
            end=time.monotonic()+12
            while time.monotonic()<end:
                text=ssh('cat '+root+'/run/status 2>/dev/null || true\n')
                if 'enhanced=yes' in text and 'refresh_ok=1' in text:break
                time.sleep(.2)
            else:raise AssertionError(text)
            wait_health('wan0=eligible')
            ssh(f'''set -e
echo '{base}:{base+999}' >{root}/port-range
iptables -t mangle -N {chain}
touch {root}/chain-owned
iptables -t mangle -A {chain} -m conntrack --ctstatus CONFIRMED -j RETURN
iptables -t mangle -A {chain} -m connmark ! --mark 0/0xf0000000 -j RETURN
iptables -t mangle -A {chain} -m mark --mark 0/0xf0000000 -j NFQUEUE --queue-num 20880 --queue-bypass
iptables -t mangle -A {chain} -m mark --mark 0xa0000000/0xf0000000 -j MARK --set-xmark 0/0xf0000000
iptables -t mangle -A {chain} -m mark --mark 0x80000000/0xf0000000 -j CONNMARK --save-mark --nfmask 0xf0000000 --ctmask 0xf0000000
iptables -t mangle -A {chain} -m mark --mark 0x90000000/0xf0000000 -j CONNMARK --save-mark --nfmask 0xf0000000 --ctmask 0xf0000000
pos=$(iptables -t mangle -S PREROUTING | awk '$1=="-A" {{n++;for(i=1;i<NF;i++)if($i=="-j" && $(i+1)=="balance"){{print n;exit}}}}')
[ -n "$pos" ]
for proto in tcp udp;do
 iptables -t mangle -I PREROUTING "$pos" -i br0 -s {client} -p "$proto" --sport {base}:{base+999} -m conntrack --ctstate NEW --ctdir ORIGINAL -j {chain}
done
''');return
        ssh('cat >'+root+'/base/wan-affinity.conf <<\'CFG\'\n'+config(mode)+'CFG\n'+ctl+'start\n',timeout=30)
        end=time.monotonic()+240
        while time.monotonic()<end:
            text=status()
            if f'src={client} wan=0 ' in text and 'transitional=' not in text and 'enhanced=yes' in text:return
            safe();print('Waiting for safe adoption; pin retained:',text.splitlines()[:2],flush=True);time.sleep(5)
        raise AssertionError(('source adoption remained uncertain',text))
    def probe(label,expected):
        nonlocal port
        safe();base=port;port+=100
        code=r'''
import concurrent.futures,json,os,socket,struct,subprocess
ip=CLIENT;base=PORT
def get(i):
 p=subprocess.run(['curl','-4','--interface',ip,'--local-port',str(base+i),'--http1.1','-H','Connection: close','--max-time','12','-sS','https://api4.ipify.org'],capture_output=True,text=True,timeout=14)
 return dict(kind='https',port=base+i,code=p.returncode,out=p.stdout,err=p.stderr)
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(get,range(4)))
for i,server in enumerate(['9.9.9.10','8.8.8.8']):
 p=subprocess.run(['dig','-b',ip+'#'+str(base+10+i),'@'+server,'example.com','+tries=1','+time=3','+short'],capture_output=True,text=True,timeout=5)
 rows.append(dict(kind='dns',port=base+10+i,code=p.returncode,out=p.stdout,err=p.stderr))
for i,host in enumerate(['www.google.com','v4.ident.me']):
 s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.bind((ip,base+20+i));s.settimeout(4)
 dst=socket.getaddrinfo(host,443,socket.AF_INET,socket.SOCK_DGRAM)[0][4]
 pkt=(b'\xc0'+struct.pack('!I',0x0a0a0a0a)+b'\x08'+os.urandom(8)+b'\x08'+os.urandom(8)).ljust(1200,b'\0')
 row=dict(kind='quic',port=base+20+i,host=host)
 try:
  s.sendto(pkt,dst);reply,peer=s.recvfrom(4096);row.update(bytes=len(reply),version=int.from_bytes(reply[1:5],'big'))
 except Exception as e:row['error']=str(e)
 finally:s.close()
 rows.append(row)
print(json.dumps(rows))
'''.replace('CLIENT',repr(client)).replace('PORT',str(base))
        r=subprocess.run(['ssh','-o','BatchMode=yes','alexs-mac-mini','python3 -'],input=code,text=True,capture_output=True,timeout=40)
        (a.evidence/(label+'.client.json')).write_text(r.stdout+'\n'+r.stderr)
        assert not r.returncode,(r.stdout,r.stderr)
        rows=json.loads(r.stdout)
        facts=ssh('nvram get wan0_ipaddr\nnvram get wan1_ipaddr\n').splitlines()
        ct=ssh('conntrack -L -s '+client+' -o extended,id 2>/dev/null\n')
        time.sleep(1.1)
        text=status();(a.evidence/(label+'.status')).write_text(text+'\n'+ct)
        if expected is None:
            expected=int(re.search(r'src='+re.escape(client)+r' wan=([01]) ',text).group(1))
        (a.evidence/(label+'.routing')).write_text(ssh('ip -4 rule show\niptables -t mangle -nvL '+chain+'\n'))
        for row in rows:
            if row['kind']=='quic':assert row.get('bytes',0)>0 and row.get('version')==0,row
            else:assert row['code']==0 and row['out'].strip(),row
            matches=[l for l in ct.splitlines() if 'src='+client+' ' in l and 'sport='+str(row['port'])+' ' in l]
            assert any(re.findall(r'\bdst=([^ ]+)',l)[-1]==facts[expected] for l in matches),(label,row,matches,facts)
        text=status()
        assert 'refresh_failed=0' in text,text
        results.append(dict(label=label,expected_wan=expected,passed=len(rows),status=text))
        (a.evidence/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(json.dumps({'label':label,'passed':len(rows),'wan':expected}),flush=True)
        return expected
    def existing_udp(flow_port):
        code=f'''python3 - <<'PY'
import socket
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
s.bind(({client!r},{flow_port}))
s.sendto(b'',('8.8.8.8',443))
s.close()
PY
'''
        ssh(code,'alexs-mac-mini');time.sleep(.2)
        rows=ssh('conntrack -L -s '+client+' -o extended,id 2>/dev/null\n').splitlines()
        rows=[row for row in rows if 'sport='+str(flow_port)+' ' in row and 'dst=8.8.8.8 ' in row]
        assert len(rows)==1 and '[UNREPLIED]' in rows[0] and 'mark=2147483648' in rows[0],rows
        return rows[0]
    try:
        safe()
        if not a.production_restart:fixture('good',0 if a.controlled_ports else 1);start_fixture()
        ssh(f'[ "$(nvram get wans_routing_rulelist)" = "$(cat {root}/pins.original)" ] || exit 1\ntouch {root}/pin-removed\nnvram set "wans_routing_rulelist=$(cat {root}/pins.modified)"\n')
        for mode in a.modes:
            if a.controlled_ports:
                port=(41000 if mode=='sticky' else 42000) if a.production_restart else (43000 if mode=='sticky' else 44000)
                if not a.production_restart:fixture('good',0)
            start(mode)
            ssh(f'ip -4 rule del priority 100 from {client}/32 lookup wan0\n')
            baseline=probe(mode+'-healthy',None if a.production_restart else 0)
            if a.production_restart:
                # This independent restart survives the client and this SSH
                # command. Existing routes/marks remain in place while stopped.
                guard=f'#!/bin/sh\nsleep 20\n[ ! -f {root}/restart-armed ] || /jffs/scripts/wan-watchdog start\n'
                ssh('cat >'+root+'/restart-guard <<\'EOF\'\n'+guard+'EOF\ntouch '+root+'/restart-armed\nnohup sh '+root+'/restart-guard </dev/null >'+root+'/restart.log 2>&1 &\n')
                restarting=True;ssh('/jffs/scripts/wan-watchdog stop\n');time.sleep(17)
                text=status();assert 'enhanced=no' in text,text
                probe(mode+'-real-publisher-absent',baseline)
                ssh('/jffs/scripts/wan-watchdog start\n',timeout=20)
                ssh('rm -f '+root+'/restart-armed\n')
                end=time.monotonic()+45
                while time.monotonic()<end:
                    text=status()
                    if 'wan0=eligible wan1=eligible' in text and 'enhanced=yes' in text:break
                    time.sleep(1)
                else:raise AssertionError(text)
                expected_pid=ssh('cat /tmp/wan-watchdog/wan-watchdog.pid\n');restarting=False
                probe(mode+'-real-publisher-returned',baseline)
            else:
                fixture('good');time.sleep(1)
                old_port=port-100+40
                old_before=existing_udp(old_port)
                fixture('bad');wait_health('wan0=unusable')
                old_after=existing_udp(old_port)
                (a.evidence/(mode+'-confirmed-unreplied.json')).write_text(json.dumps({'before':old_before,'after':old_after})+'\n')
                print(mode+' confirmed unreplied UDP retained original WAN',flush=True)
                probe(mode+'-failover',1)
                fixture('held');wait_health('wan0=unknown');probe(mode+'-hold',1)
                # Publisher alive but not progressing: no implicit recovery.
                ssh(f'kill -STOP $(cat {root}/fixture.pid)\n');time.sleep(17)
                text=status();assert 'enhanced=no' in text,text
                probe(mode+'-stalled-publisher',1)
                ssh(f'kill -CONT $(cat {root}/fixture.pid)\n');time.sleep(1)
                fixture('good');wait_health('wan0=eligible');probe(mode+'-recovered',1 if mode=='sticky' else 0)
            protect()
            if a.controlled_ports:
                ssh(f'''read ports <{root}/port-range
for proto in tcp udp;do iptables -t mangle -D PREROUTING -i br0 -s {client} -p "$proto" --sport "$ports" -m conntrack --ctstate NEW --ctdir ORIGINAL -j {chain};done
iptables -t mangle -F {chain}
iptables -t mangle -X {chain}
rm -f {root}/port-range {root}/chain-owned
''')
            ssh(ctl+'stop\n',timeout=20)
        print('MINI SOCKET MATRIX PASSED',flush=True)
    finally:
        if a.production_restart:
            ssh('/jffs/scripts/wan-watchdog start\n',timeout=20)
            ssh('rm -f '+root+'/restart-armed\n')
            expected_pid=ssh('cat /tmp/wan-watchdog/wan-watchdog.pid\n')
        ssh('sh '+root+'/cleanup\n',timeout=30)
        for name,cmd in [('status','cat '+root+'/run/status'),('helper.log','cat '+root+'/run/wan-affinity.log'),('fixture.log','cat '+root+'/fixture.log')]:
            try:(a.evidence/name).write_text(ssh(cmd+'\n'))
            except Exception:pass
        after={name:ssh(command+'\n') for name,command in {
            'pins':'nvram get wans_routing_rulelist','rules':'ip -4 rule show','firewall':'iptables-save',
            'production_pid':'cat /tmp/wan-watchdog/wan-watchdog.pid',
            'scripts':'md5sum /jffs/scripts/wan-watchdog /jffs/scripts/wan-watchdog-ddns /jffs/scripts/firewall-start /jffs/scripts/services-start',
            'queues':'cat /proc/net/netfilter/nfnetlink_queue'}.items()}
        for name,text in after.items():(a.evidence/(name+'.after')).write_text(text)
        norm=lambda text:[re.sub(r'\[\d+:\d+\]','[0:0]',l) for l in text.splitlines() if l and not l.startswith('#')]
        checks={'pins':before['pins']==after['pins'],'scripts':before['scripts']==after['scripts'],
                'production_pid':expected_pid==after['production_pid'],
                'rpdb':sorted(before['rules'].splitlines())==sorted(after['rules'].splitlines()),
                'firewall':norm(before['firewall'])==norm(after['firewall']),'queues':not after['queues'].strip()}
        (a.evidence/'restoration.json').write_text(json.dumps(checks,indent=2)+'\n')
        print('RESTORATION',json.dumps(checks),flush=True)
        ssh(f'read p <{root}/guard.pid\ncase "$(tr \'\\000\' \' \' </proc/$p/cmdline 2>/dev/null)" in *\'{root}/guard\'*) kill "$p";;esac\n')
        time.sleep(.5);ssh('rm -rf '+shlex.quote(root)+'\n')
        assert all(checks.values()),checks

if __name__=='__main__':main()
