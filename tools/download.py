"""Resumable ranged downloader for official large SDK artifacts."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import urllib.request


def download(url,path,expected_sha256=None,threads=8):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    response=urllib.request.urlopen(urllib.request.Request(url,headers={'Range':'bytes=0-0'}),timeout=30)
    content_range=response.headers.get('Content-Range','')
    response.close()
    match=re.search(r'/(\d+)$',content_range)
    if not match:
        raise RuntimeError('Server did not support byte-range requests')
    size=int(match.group(1));chunk=4*1024*1024;count=(size+chunk-1)//chunk
    state_path=path.with_suffix(path.suffix+'.chunks.json')
    done=set()
    if state_path.exists() and path.exists() and path.stat().st_size==size:
        state=json.loads(state_path.read_text())
        if state.get('url')==url and state.get('size')==size:
            done=set(state['done'])
    else:
        with open(path,'wb') as f:f.truncate(size)
    lock=threading.Lock();started=time.time();last=[0]
    def part(i):
        if i in done:return
        start=i*chunk;end=min(size,start+chunk)-1
        for attempt in range(5):
            try:
                req=urllib.request.Request(url,headers={'Range':f'bytes={start}-{end}'})
                with urllib.request.urlopen(req,timeout=45) as r:
                    if r.status!=206 or r.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}':
                        raise RuntimeError('Incorrect range response')
                    data=r.read()
                if len(data)!=end-start+1:raise RuntimeError('Incomplete chunk')
                with open(path,'r+b') as f:f.seek(start);f.write(data)
                with lock:
                    done.add(i)
                    temp=state_path.with_suffix('.tmp');temp.write_text(json.dumps({'url':url,'size':size,'done':sorted(done)}));temp.replace(state_path)
                    if time.time()-last[0]>5 or len(done)==count:
                        print(f'{path.name}: {len(done)}/{count} chunks ({100*len(done)/count:.1f}%), {time.time()-started:.0f}s',flush=True);last[0]=time.time()
                return
            except Exception as e:
                if attempt==4:raise
                time.sleep(min(2**attempt,8))
    with concurrent.futures.ThreadPoolExecutor(max_workers=threads) as executor:
        list(executor.map(part,range(count)))
    sha=hashlib.sha256()
    with open(path,'rb') as f:
        while data:=f.read(8*1024*1024):sha.update(data)
    actual=sha.hexdigest()
    if expected_sha256 and actual.lower()!=expected_sha256.lower():raise RuntimeError('SHA256 mismatch')
    print(f'Complete: {path} ({size} bytes), SHA256 {actual}',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('url');p.add_argument('path');p.add_argument('--sha256');p.add_argument('--threads',type=int,default=8);a=p.parse_args()
    download(a.url,a.path,a.sha256,a.threads)
