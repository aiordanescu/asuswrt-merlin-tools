"""Private SSH-controlled HTTPS connections for the one-client adoption check."""
import concurrent.futures
import http.client
import json
import ssl
import sys
import threading
import time

ip=sys.argv[1]
connections={}
errors=[]
stop=threading.Event()
context=ssl.create_default_context()

def request(entry):
    with entry['lock']:
        connection=entry['connection']
        connection.request('GET','/cdn-cgi/trace',headers={'Connection':'keep-alive'})
        response=connection.getresponse()
        body=response.read().decode()
        assert response.status==200 and connection.sock is not None, (response.status,body)
        assert connection.sock.getsockname()==(ip,entry['port'])
        return {'port':entry['port'],'public_ip':dict(line.split('=',1) for line in body.splitlines() if '=' in line)['ip']}

def open_one(port,old):
    entry={'port':port,'old':old,'lock':threading.Lock(),
           'connection':http.client.HTTPSConnection('speed.cloudflare.com',443,timeout=8,
                                                   source_address=(ip,port),context=context)}
    result=request(entry)
    connections[port]=entry
    return result

def heartbeat():
    while not stop.wait(4):
        for entry in list(connections.values()):
            if entry['old']:
                try:request(entry)
                except Exception as e:errors.append(str(e))
threading.Thread(target=heartbeat,daemon=True).start()
try:
    for line in sys.stdin:
        try:
            command=json.loads(line)
            assert not errors,errors
            if command['op']=='open':
                with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                    rows=list(pool.map(lambda port:open_one(port,command.get('old',False)),command['ports']))
            elif command['op']=='touch':
                rows=[request(connections[p]) for p in command['ports']]
            else:raise ValueError('unsupported client operation')
            print(json.dumps({'ok':True,'rows':rows}),flush=True)
        except Exception as e:print(json.dumps({'ok':False,'error':str(e)}),flush=True)
finally:
    stop.set()
    for entry in connections.values():entry['connection'].close()
