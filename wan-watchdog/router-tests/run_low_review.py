#!/usr/bin/env python3
"""Low-finding regressions using private files, loopback ICMP and mock routes."""
import argparse,datetime,json,os,shlex,subprocess,time
from pathlib import Path
from router_ssh import MUX

def ssh(cmd,data=None,timeout=15,check=True):
    p=subprocess.run(['ssh',*MUX,'-o','BatchMode=yes','-o','ConnectTimeout=8','router','set -e\n'+cmd],input=data,capture_output=True,timeout=timeout)
    if check and p.returncode:raise RuntimeError((cmd,p.returncode,p.stdout.decode(),p.stderr.decode()))
    return p

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--evidence',type=Path,required=True);ap.add_argument('--cc',default=os.environ.get('ROUTER_CC','zig cc -target aarch64-linux-musl'));a=ap.parse_args()
    repo=Path(__file__).resolve().parents[2];a.evidence.mkdir(parents=True,exist_ok=True);a.evidence.chmod(0o700)
    root='/tmp/ww-low-'+datetime.datetime.now().strftime('%H%M%S');result={}
    def baseline():
        return {key:ssh(cmd).stdout.decode() for key,cmd in {
            'owners':'cat /tmp/wan-watchdog/wan-watchdog.pid /tmp/wan-affinity/wan-affinity.pid',
            'hooks':'md5sum /jffs/scripts/wan-watchdog /jffs/scripts/services-start /jffs/scripts/firewall-start /jffs/scripts/inadyn.postconf',
            'pins':'nvram get wans_routing_rulelist','rules':'ip rule show',
            'firewall':"iptables-save | sed '/^#/d;s/\[[0-9][0-9]*:[0-9][0-9]*\]/[0:0]/g'"}.items()}
    before=baseline();ssh('umask 077;mkdir '+root)
    monitor=None
    try:
        files={}
        for name,sources,inc in [
            ('ww-worker',['wan-watchdog/tests/monitor/test_worker.c','wan-watchdog/src/ww_worker.c'],['wan-watchdog/src']),
            ('ww-runtime',['wan-watchdog/tests/monitor/test_runtime.c'],['wan-watchdog/src','common/wan-health']),
            ('wa-engine',['wan-affinity/tests/c/test_engine.c','wan-affinity/src/wa_engine.c','wan-affinity/src/wa_conntrack.c','wan-affinity/src/wa_checkpoint.c'],['wan-affinity/src'])]:
            out=repo/'wan-watchdog/build'/name
            subprocess.run([*shlex.split(a.cc),'-std=c11','-O2','-Wall','-Wextra','-Werror','-static',*[f'-I{repo/x}' for x in inc],*[str(repo/x) for x in sources],'-o',str(out)],check=True)
            files[name]=out
        for name in ['wan-watchdog','wan-healthctl','test_socket','test_probe']:files[name]=repo/'wan-watchdog/build'/name
        files['wan-affinity']=repo/'wan-affinity/build/wan-affinity'
        files['legacy']=repo/'wan-watchdog/wan-watchdog-legacy.sh';files['ddns']=repo/'wan-watchdog/wan-watchdog-ddns.sh'
        for name,path in files.items():ssh('cat >'+root+'/'+name,path.read_bytes())
        ssh('chmod 700 '+root+'/*')
        for name in ['ww-worker','ww-runtime','wa-engine','test_socket','test_probe']:
            # The UID test must reach SO_PEERCRED rather than fail at this
            # harness's parent directory. Test files themselves remain 0700.
            if name=='test_socket':ssh('chmod 711 '+root)
            result[name]=ssh('WH_TEST_ROOT='+root+' '+root+'/'+name,timeout=20).stdout.decode();print(result[name],flush=True)
            ssh('chmod 700 '+root)
        config='export ENABLED="1" # on\n MODE=sticky # default\nCLIENT="192.168.1.100"\nLAN_SCOPE=0\nQUEUE_NUM=20880 # queue\nHEALTH_SOURCE=watchdog\n'
        ssh('cat >'+root+'/config',config.encode());result['literal_config']=ssh(root+'/wan-affinity --config '+root+'/config --check-config').stdout.decode()
        ssh('cat >'+root+'/bad',b'MODE=$(echo sticky)\n');assert ssh(root+'/wan-affinity --config '+root+'/bad --check-config',check=False).returncode==2
        too_long='/tmp/'+'w'*92
        assert ssh(root+'/wan-watchdog --shadow --adapter '+root+'/adapter --legacy '+root+'/legacy --run-dir '+too_long,check=False).returncode==2
        result['overlong_runtime_rejected']=True
        adapter='''#!/bin/sh
[ ! -f "$STATE_DIR/hang" ] || sleep 7
printf 'R -1\nF 0 1 lo 127.0.0.1 127.0.0.1 0 1 1 127.0.0.1 127.0.0.2\nF 1 1 lo 127.0.0.1 127.0.0.1 0 1 1 127.0.0.1 127.0.0.2\nEND\n'
'''
        ssh('cat >'+root+'/adapter',adapter.encode())
        ssh('mkdir '+root+'/run;chmod 777 '+root+'/run;touch '+root+'/run/hang '+root+'/run/health.sock')
        monitor=int(ssh('nohup '+root+'/wan-watchdog --shadow --adapter '+root+'/adapter --legacy '+root+'/legacy --run-dir '+root+'/run --duration 25 </dev/null >'+root+'/monitor.log 2>&1 &\necho $!').stdout)
        time.sleep(2)
        status=ssh('cat '+root+'/run/status').stdout.decode();assert 'fact_age_ms=-1' in status,status
        assert ssh('ls -ld '+root+'/run').stdout.startswith(b'drwx------')
        time.sleep(5)
        log=ssh('cat '+root+'/monitor.log').stdout.decode();assert log.count('health socket unavailable')==1 and 'health socket retry' not in log,log
        ssh('rm '+root+'/run/hang '+root+'/run/health.sock')
        end=time.monotonic()+15
        while time.monotonic()<end:
            status=ssh('cat '+root+'/run/status').stdout.decode()
            if 'publisher_ready=1' in status:break
            time.sleep(1)
        assert 'publisher_ready=1' in status,status
        result['runtime_permissions_missing_facts_and_listener_recovery']=True
        # Exercise the actual policy serializer with synthetic allowlisted data.
        ssh('cat >'+root+'/service.sh',(repo/'wan-watchdog/lib/ww-service.sh').read_bytes());ssh('mkdir '+root+'/proc '+root+'/proc/123 '+root+'/owner')
        ssh('cat >'+root+'/proc/123/environ',b'LAN_NETWORKS=192.168.1.0/24\n10.0.0.0/8\0SECRET_SENTINEL=secret\nFAILOVER_PRIORITY=99\0FAILOVER_PRIORITY=78\0')
        script=f'''WW_BASE={root}/service STATE_DIR={root}/capture OWNERSHIP_DIR={root}/owner WW_ENTRY={root}/entry WW_PROC={root}/proc WW_CONFIG={root}/service.conf sh -c '. "$1";ww_capture_policy 123 "$2" shell' sh {root}/service.sh {root}/captured
sh -c '. "$1";[ "$FAILOVER_PRIORITY" = 78 ] && [ "$LAN_NETWORKS" = "$(printf "192.168.1.0/24\\n10.0.0.0/8")" ]' sh {root}/captured
! grep -q SECRET_SENTINEL {root}/captured
'''
        ssh(script);result['busybox_multiline_policy']=True
        env=dict(os.environ,WAN_WATCHDOG_TEST_ROUTER='router',WAN_WATCHDOG_TEST_SCRIPT=root+'/legacy',WAN_WATCHDOG_TEST_DDNS_HELPER=root+'/ddns',WAN_WATCHDOG_TEST_SSH_OPTIONS=shlex.join(MUX))
        for pattern in ['test_wan_watchdog.py','test_ddns.py']:
            p=subprocess.run(['python3','-m','unittest','discover','-s','wan-watchdog/tests','-p',pattern,'-q'],cwd=repo,env=env,capture_output=True,text=True,timeout=150)
            result[pattern]=p.stdout+p.stderr;print(result[pattern],flush=True);assert not p.returncode,result[pattern]
        assert before==baseline(),'production configuration or owners changed'
        result['production_preserved']=True
    finally:
        if monitor:
            ssh(fr'''if [ -r /proc/{monitor}/cmdline ];then
case "$(tr '\000' ' ' </proc/{monitor}/cmdline)" in '{root}/wan-watchdog '*) kill -TERM {monitor};;esac
fi''',check=False)
        (a.evidence/'results.json').write_text(json.dumps(result,indent=2)+'\n')
        ssh('rm -rf '+root)
    print('All private router review regressions passed; production preserved.',flush=True)
if __name__=='__main__':main()
