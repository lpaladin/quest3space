from __future__ import annotations
import argparse
import base64
import concurrent.futures
import json
import logging
import os
from pathlib import Path
import queue
import re
import secrets
import shutil
import socket
import struct
import threading
import time
import uuid

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pc.reconstruction import Hull, DepthFusion, board_pose, board_svg, camera_matrix

PROJECT = Path(__file__).resolve().parent.parent
LOG = logging.getLogger('quest3space')


def atomic_json(path, value):
    path=Path(path); temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temp,path)


class Service:
    def __init__(self, root, token=None):
        self.root=Path(root)
        for name in ['captures','reconstruction','temp','exports']:
            (self.root/name).mkdir(parents=True,exist_ok=True)
        token_path=self.root/'pairing-token.txt'
        if token is not None:
            self.token=token
        elif token_path.exists():
            self.token=token_path.read_text().strip()
        else:
            self.token=secrets.token_hex(6).upper()
            token_path.write_text(self.token,encoding='ascii')
        self.sessions={}
        self.lock=threading.RLock()
        self.jobs=queue.Queue(maxsize=12)
        self.on_preview=None
        self.restore_jobs=[]
        self.restore_active_scan()
        self.worker=threading.Thread(target=self.run,name='reconstruction',daemon=True)
        self.worker.start()
        if self.restore_jobs:
            threading.Thread(target=self.feed_restore,name='scan-recovery',daemon=True).start()
        board_svg(self.root/'exports'/'scan-board-A4.svg')

    def restore_active_scan(self):
        candidates=[]
        for path in (self.root/'captures').glob('*/session.json'):
            try:
                state=json.loads(path.read_text(encoding='utf-8'))
                if state.get('status') in ['scanning','finishing']:
                    candidates.append((state.get('created_at',0),path,state))
            except (ValueError,OSError):
                continue
        if not candidates:return
        _,path,state=max(candidates,key=lambda entry:entry[0])
        sid=state['id']
        if not re.fullmatch('[a-f0-9]{16}',sid):return
        size=np.asarray(state['size_mm'],float)/1000
        hull=DepthFusion(size,state['resolution'],state['origin'],state.get('rotation',[0,0,0,1]),state.get('planes')) if state['method']=='depth-tsdf' else Hull(size,state['resolution'])
        frames=sorted(p.stem for p in path.parent.glob('*.jpg') if p.with_suffix('.json').exists())
        resume_finish=state['status']=='finishing'
        state.update(received=len(frames),processed=0,accepted=0,rejected=0,coverage_bins=0,error=None,restoring=True,hint='Recovering saved scan frames; uploads will resume automatically')
        self.sessions[sid]={'state':state,'hull':hull,'seen':set(frames),'preview':None,'folder':path.parent,'busy':False,'resume_finish':resume_finish}
        self.restore_jobs=[(sid,fid) for fid in frames]+[(sid,'__restore_complete__')]

    def feed_restore(self):
        for item in self.restore_jobs:self.jobs.put(item)
        self.restore_jobs=[]

    def create(self, body):
        body=body.copy();planes=None
        if body.get('planes'):
            from pc.plane_region import resolve_region
            planes,center,extent,_=resolve_region(body['planes'],body.get('region_seed'))
            body.update(origin=center.tolist(),size_mm=(extent*1000).tolist(),rotation=[0,0,0,1])
        size=[float(x)/1000 for x in body.get('size_mm',[130,160,200])]
        resolution=int(body.get('resolution',80))
        if len(size)!=3 or not all(.03<=x<=(4.001 if planes is not None else .6) for x in size) or not 32<=resolution<=112:
            raise ValueError('Volume size must be 30–600 mm per axis; resolution 32–112')
        sid=uuid.uuid4().hex[:16]
        folder=self.root/'captures'/sid; folder.mkdir()
        method=body.get('method','visual-hull')
        if method not in ['visual-hull','depth-tsdf']:
            raise ValueError('Unknown reconstruction method')
        origin=np.asarray(body.get('origin',[0,1,1]),float)
        if origin.shape!=(3,) or not np.isfinite(origin).all():
            raise ValueError('Invalid scan origin')
        rotation=np.asarray(body.get('rotation',[0,0,0,1]),float)
        if rotation.shape!=(4,) or not np.isfinite(rotation).all() or np.linalg.norm(rotation)<1e-8:raise ValueError('Invalid scan rotation')
        rotation=rotation/np.linalg.norm(rotation)
        state={'id':sid,'status':'scanning','received':0,'processed':0,'accepted':0,'rejected':0,
               'coverage_bins':0,'coverage_total':12,'hint':('Place scan box around object; begin RGB-D capture' if method=='depth-tsdf' else 'Show at least two board markers'),
               'size_mm':[round(x*1000) for x in size],'resolution':resolution,'created_at':time.time(),
               'method':method,'origin':origin.tolist(),'rotation':rotation.tolist(),'error':None,'exports':[]}
        if planes is not None:
            if method!='depth-tsdf':raise ValueError('Plane regions require depth scanning')
            state.update(planes=planes.tolist(),region_seed=body['region_seed'])
        hull=DepthFusion(size,resolution,origin,rotation,planes) if method=='depth-tsdf' else Hull(size,resolution)
        session={'state':state,'hull':hull,'seen':set(),'preview':None,'folder':folder,'busy':False}
        with self.lock:
            if any(s['state']['status'] in ['scanning','finishing'] for s in self.sessions.values()):
                raise ValueError('Finish the active scan before creating another')
            self.sessions[sid]=session
            atomic_json(folder/'session.json',state)
        return state.copy()

    def get(self,sid):
        with self.lock:
            s=self.sessions.get(sid)
        if not s:
            if re.fullmatch('[a-f0-9]{16}',sid):
                path=self.root/'captures'/sid/'session.json'
                if path.is_file():
                    state=json.loads(path.read_text(encoding='utf-8'))
                    if state.get('status') not in ['scanning','finishing']:
                        return {'state':state,'preview':None,'archived':True}
            raise HTTPException(404,'Session not active on this server; create a new session. Saved scans remain on disk.')
        return s

    def enqueue(self,sid,fid,jpeg,meta,depth=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',fid):
            raise HTTPException(400,'Invalid frame ID')
        s=self.get(sid)
        with self.lock:
            if s.get('archived'):
                if (self.root/'captures'/sid/f'{fid}.jpg').is_file():
                    return {'stored':True,'duplicate':True}
                raise HTTPException(409,'Saved session is closed')
            if fid in s['seen']:
                return {'stored':True,'duplicate':True}
            if s['state'].get('restoring'):
                raise HTTPException(503,'Recovering saved scan; retry this frame')
            if s['state']['status']!='scanning':
                raise HTTPException(409,'Session is not scanning')
            if (s['state']['method']=='depth-tsdf') != (depth is not None):
                raise HTTPException(400,'Frame type does not match reconstruction method')
            if self.jobs.full():
                raise HTTPException(429,'PC reconstruction queue full; retry this frame')
            if shutil.disk_usage(self.root).free < 512*1024*1024:
                raise HTTPException(507,'Less than 512 MiB free on data drive; scan paused')
            # Decode and validate before acknowledging durable receipt.
            image=cv2.imdecode(np.frombuffer(jpeg,np.uint8),cv2.IMREAD_COLOR)
            if image is None or not 160<=image.shape[0]<=2048 or not 160<=image.shape[1]<=2048:
                raise HTTPException(400,'Invalid JPEG or unsupported image dimensions')
            try:
                camera_matrix(meta,image.shape[1],image.shape[0])
            except ValueError as e:
                raise HTTPException(400,str(e))
            path=s['folder']/f'{fid}.jpg'
            temp=path.with_suffix('.jpg.tmp'); temp.write_bytes(jpeg); os.replace(temp,path)
            atomic_json(path.with_suffix('.json'),meta)
            if depth is not None:
                np.save(path.with_suffix('.depth.npy'),depth)
            s['seen'].add(fid)
            s['state']['received']+=1
            atomic_json(s['folder']/'session.json',s['state'])
            self.jobs.put_nowait((sid,fid))
        return {'stored':True,'duplicate':False,'queued':self.jobs.qsize()}

    def finish(self,sid):
        s=self.get(sid)
        with self.lock:
            if s['state'].get('restoring'):
                raise HTTPException(503,'Recovering saved scan; retry finishing shortly')
            if s['state']['status'] in ['finishing','complete','failed']:
                return s['state'].copy()
            if s.get('archived'):
                raise HTTPException(409,'Saved session is closed')
            if self.jobs.full():
                raise HTTPException(429,'Queue full; retry finishing in a moment')
            s['state']['status']='finishing'
            self.jobs.put_nowait((sid,None))
        return s['state'].copy()

    def run(self):
        while True:
            sid,fid=self.jobs.get()
            s=self.sessions[sid]
            if fid=='__restore_complete__':
                s['state']['restoring']=False
                s['state']['hint']='Saved scan recovered; continue capturing or finish'
                if s.get('resume_finish'):self.jobs.put_nowait((sid,None))
                atomic_json(s['folder']/'session.json',s['state'])
                self.jobs.task_done()
                continue
            try:
                s['busy']=True
                if fid is None:
                    if s['state']['accepted']<4 or len(s['hull'].bins)<3:
                        raise ValueError('Not enough views: need at least four accepted images from three directions')
                    result=s['hull'].export(self.root/'exports'/sid)
                    result['coverage_bins']=len(s['hull'].bins)
                    result['coverage_total']=12
                    if s['state']['method']=='visual-hull':
                        result['warning']='Silhouette-based exterior; hidden concavities and bottom are not measured. Incomplete directions can leave excess geometry.'
                    atomic_json(self.root/'exports'/sid/'report.json',result)
                    s['state']['exports']=[f'/api/exports/{sid}/{p.name}' for p in (self.root/'exports'/sid).iterdir()]
                    s['state']['status']='complete'
                    s['state']['hint']='Model exported. Inspect missing directions and silhouette quality.'
                else:
                    path=s['folder']/f'{fid}.jpg'
                    meta=json.loads(path.with_suffix('.json').read_text())
                    image=cv2.imread(str(path)); h,w=image.shape[:2]
                    if float(np.std(image,axis=(0,1)).max())<.5:
                        raise ValueError('RGB frame is uniform; camera image readback is not ready')
                    K=camera_matrix(meta,w,h)
                    # Work at <=800 pixels wide; transform intrinsics consistently.
                    scale=min(1,800/w)
                    if scale<1:
                        image=cv2.resize(image,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
                        K[:2]*=scale
                    if s['state']['method']=='visual-hull' and cv2.Laplacian(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var()<15:
                        raise ValueError('Image blurred: pause briefly before moving to the next view')
                    if s['state']['method']=='depth-tsdf':
                        depth=np.load(path.with_suffix('.depth.npy'))
                        mask=s['hull'].integrate_depth(image,K,meta,depth)
                    else:
                        R,t,error,markers=board_pose(image,K)
                        mask=s['hull'].integrate(image,K,R,t)
                        atomic_json(path.with_suffix('.pose.json'),{'R':R.tolist(),'t':t.tolist(),'K':K.tolist(),'error_px':error,'markers':markers})
                    cv2.imwrite(str(self.root/'reconstruction'/f'{sid}-mask.png'),mask*255)
                    s['state']['accepted']+=1
                    s.setdefault('accepted_ids',[]).append(fid)
                    s['state']['coverage_bins']=len(s['hull'].bins)
                    s['state']['hint']=f'Accepted — {len(s["hull"].bins)}/12 directions. Scan slowly at different heights.'
                    try:
                        s['preview']=s['hull'].preview()
                    except ValueError:
                        s['state']['hint']='Depth received; collect more viewpoints to extract a surface'
                    overlay=image.copy(); overlay[mask>0]=(overlay[mask>0]*.45+np.array([110,220,70])*.55).astype(np.uint8)
                    ok,encoded=cv2.imencode('.jpg',overlay)
                    if ok:
                        s['overlay']=encoded.tobytes()
                    # Checkpoint occupancy for diagnostics/recovery, original photos always retained.
                    checkpoint={'occupied':s['hull'].occupied,'size':s['hull'].size,'resolution':s['hull'].resolution}
                    if isinstance(s['hull'],DepthFusion):
                        checkpoint.update(tsdf=s['hull'].tsdf,weights=s['hull'].weights,origin=s['hull'].origin)
                    np.savez_compressed(self.root/'reconstruction'/f'{sid}-hull.npz',**checkpoint)
            except Exception as e:
                LOG.exception('Processing %s/%s',sid,fid)
                if fid is None:
                    s['state']['status']='failed'; s['state']['error']=str(e)
                else:
                    s['state']['rejected']+=1
                s['state']['hint']=str(e)
            finally:
                with self.lock:
                    if fid is not None:
                        s['state']['processed']+=1
                    s['busy']=False
                    atomic_json(s['folder']/'session.json',s['state'])
                self.jobs.task_done()
                if self.on_preview is not None and s['state']['method']=='depth-tsdf' and s['state']['status'] in ['scanning','complete']:
                    try:self.on_preview(sid,s['state'],s.get('preview'),s.get('accepted_ids',[]))
                    except Exception:LOG.exception('Background refinement notification failed')


def create_app(root=None,token=None):
    root=root or json.loads((PROJECT/'storage.config.json').read_text())['storageRoot']
    svc=Service(root,token)
    app=FastAPI(title='Quest3Space',docs_url=None,redoc_url=None)
    app.state.service=svc
    from pc.iterative_jobs import OfflineJobs
    offline=OfflineJobs(svc.root)
    svc.on_preview=offline.offer
    app.state.offline=offline
    saved_previews={}

    @app.middleware('http')
    async def auth(request,call_next):
        if request.url.path.startswith('/api/') and request.url.path not in ['/api/health','/api/bootstrap']:
            supplied=request.headers.get('x-scan-token','')
            if not secrets.compare_digest(supplied,svc.token):
                return JSONResponse({'detail':'Pairing token required'},status_code=401)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response

    @app.get('/')
    def index():
        return FileResponse(PROJECT/'pc'/'web'/'index.html')

    @app.get('/api/health')
    def health():
        return {'name':'Quest3Space','version':'0.2.0','ready':True,'protocol':2,'methods':['depth-tsdf','visual-hull']}

    @app.get('/api/bootstrap')
    def bootstrap(request:Request):
        if request.client.host not in ['127.0.0.1','::1','testclient'] or request.url.hostname not in ['127.0.0.1','localhost','::1','testserver']:
            raise HTTPException(403,'Open dashboard on PC localhost to see pairing token')
        addresses=sorted({ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith('127.')})
        return {'token':svc.token,'addresses':addresses,'root':str(svc.root),'free_gib':round(shutil.disk_usage(svc.root).free/2**30,1)}

    @app.get('/api/sessions')
    def sessions():
        with svc.lock:
            current=[s['state'].copy() for s in svc.sessions.values()]
        saved=[]
        for path in sorted((svc.root/'captures').glob('*/session.json'),reverse=True):
            try:
                state=json.loads(path.read_text(encoding='utf-8'))
                if state['id'] not in svc.sessions:
                    state['archived']=True
                    if state['status'] in ['scanning','finishing']:
                        state['status']='interrupted'
                    saved.append(state)
            except (ValueError,OSError):
                continue
        return sorted(current+saved[:100],key=lambda s:s.get('created_at',0),reverse=True)

    @app.post('/api/sessions')
    async def new_session(request:Request):
        try:
            return svc.create(await request.json())
        except (ValueError,TypeError) as e:
            raise HTTPException(400,str(e))

    @app.get('/api/sessions/{sid}')
    def session(sid:str):
        with svc.lock:
            return svc.get(sid)['state'].copy()

    @app.post('/api/sessions/{sid}/frames/{fid}')
    async def frame(sid:str,fid:str,request:Request):
        if int(request.headers.get('content-length','0'))>8*1024*1024:
            raise HTTPException(413,'Frame exceeds 8 MiB')
        data=bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data)>8*1024*1024:
                raise HTTPException(413,'Frame exceeds 8 MiB')
        try:
            raw=request.headers.get('x-frame-meta','')
            if len(raw)>16000:
                raise ValueError('Metadata too long')
            meta=json.loads(base64.b64decode(raw,validate=True))
            if not isinstance(meta,dict):
                raise ValueError('Metadata must be object')
        except Exception:
            raise HTTPException(400,'Invalid X-Frame-Meta base64 JSON')
        return svc.enqueue(sid,fid,bytes(data),meta)

    @app.post('/api/sessions/{sid}/finish')
    def finish(sid:str):
        return svc.finish(sid)

    @app.post('/api/sessions/{sid}/rgbd/{fid}')
    async def rgbd(sid:str,fid:str,request:Request):
        data=bytearray()
        async for part in request.stream():
            data.extend(part)
            if len(data)>10*1024*1024:
                raise HTTPException(413,'RGB-D packet exceeds 10 MiB')
        try:
            if len(data)<12:raise ValueError('Short packet')
            ml,jl,dl=struct.unpack_from('<III',data)
            if ml>32000 or 12+ml+jl+dl!=len(data):raise ValueError('Invalid packet lengths')
            meta=json.loads(data[12:12+ml]);jpeg=bytes(data[12+ml:12+ml+jl])
            dw,dh=map(int,meta['depth_size'])
            if not 16<=dw<=512 or not 16<=dh<=512 or dl!=dw*dh*4:raise ValueError('Invalid depth dimensions')
            depth=np.frombuffer(data[12+ml+jl:],dtype='<f4').copy().reshape(dh,dw)
            for name,count in [('depth_world_to_clip',16),('rgb_world_to_camera',16),('depth_z_params',2)]:
                values=np.asarray(meta[name],float)
                if values.size!=count or not np.isfinite(values).all():raise ValueError('Invalid '+name)
        except Exception as e:
            raise HTTPException(400,str(e))
        return svc.enqueue(sid,fid,jpeg,meta,depth)

    @app.get('/api/sessions/{sid}/preview')
    def preview(sid:str,refined:bool=False,run:str|None=None,textured:bool=False):
        if run is not None:
            ready=next(((p,s) for p,s in offline.records(sid) if p.parent.name==run and s['status']=='complete'),None)
            if ready is None:raise HTTPException(404,'Refinement version unavailable')
            folder=ready[0].parent
            if textured:
                if not (folder/'textured-preview.json').is_file():offline.publish_textured_preview(folder)
                return FileResponse(folder/'textured-preview.json',media_type='application/json',headers={'X-Refinement-Run':run})
            if not (folder/'preview.json').is_file():offline.publish_preview(folder)
            return FileResponse(folder/'preview.json',media_type='application/json',headers={'X-Refinement-Run':run})
        if refined:
            ready=offline.latest_complete(sid)
            if ready:
                folder=ready[0].parent
                if not (folder/'preview.json').is_file():offline.publish_preview(folder)
                return FileResponse(folder/'preview.json',media_type='application/json')
        s=svc.get(sid)
        if s.get('preview') is not None:return s['preview']
        model=svc.root/'exports'/sid/'model.glb'
        if s.get('archived') and model.is_file():
            if sid not in saved_previews:
                import trimesh
                m=trimesh.load(model,force='mesh',process=False)
                colors=np.asarray(m.visual.to_color().vertex_colors)[m.faces][:,:,:3].mean(axis=1).astype(np.uint8) if hasattr(m.visual,'to_color') else m.visual.face_colors[:,:3]
                vertices=np.asarray(m.vertices)[:,[0,2,1]].copy();vertices[:,1]*=-1
                if len(saved_previews)>=4:saved_previews.pop(next(iter(saved_previews)))
                saved_previews[sid]={'vertices':np.round(vertices,5).tolist(),'faces':m.faces.tolist(),'colors':colors.tolist()}
            return saved_previews[sid]
        return {'vertices':[],'faces':[],'colors':[]}

    @app.get('/api/sessions/{sid}/overlay')
    def overlay(sid:str):
        from fastapi.responses import Response
        s=svc.get(sid)
        if 'overlay' not in s:
            raise HTTPException(404,'No accepted frame yet')
        return Response(s['overlay'],media_type='image/jpeg')

    @app.get('/board.svg')
    def board():
        return FileResponse(svc.root/'exports'/'scan-board-A4.svg',media_type='image/svg+xml')

    @app.get('/api/exports/{sid}/{name}')
    def export(sid:str,name:str):
        if not re.fullmatch('[a-f0-9]{16}',sid) or name!=Path(name).name or not re.fullmatch(r'[A-Za-z0-9_.-]+',name):
            raise HTTPException(400,'Invalid filename')
        path=svc.root/'exports'/sid/name
        if not path.is_file():
            raise HTTPException(404,'Export not found')
        return FileResponse(path,filename=name)

    @app.get('/api/sessions/{sid}/offline')
    def offline_status(sid:str):
        return offline.status(sid)

    @app.get('/api/iteration')
    def iteration_configuration():
        return offline.configuration()

    @app.post('/api/iteration')
    async def iteration_configure(request:Request):
        body=await request.json()
        if not isinstance(body.get('enabled'),bool):raise HTTPException(400,'enabled must be a boolean')
        return offline.configure(body['enabled'])

    @app.post('/api/sessions/{sid}/offline')
    def offline_start(sid:str):
        return offline.start(sid)

    @app.get('/api/offline/{sid}/{run}/{name}')
    def offline_file(sid:str,run:str,name:str):
        if not re.fullmatch('[a-f0-9]{16}',sid) or not re.fullmatch('[A-Za-z0-9_-]+',run) or not re.fullmatch('[A-Za-z0-9_.-]+',name):raise HTTPException(400,'Invalid output path')
        if not (name=='report.json' or name.startswith('quality') and Path(name).suffix in ['.glb','.obj','.mtl','.jpg','.png']):raise HTTPException(404,'Output unavailable')
        path=svc.root/'offline'/sid/run/name
        if not path.is_file():raise HTTPException(404,'Output missing')
        return FileResponse(path,filename=name)

    return app


if __name__=='__main__':
    import uvicorn
    p=argparse.ArgumentParser()
    p.add_argument('--host',default='0.0.0.0'); p.add_argument('--port',type=int,default=8765)
    p.add_argument('--data-root'); args=p.parse_args()
    application=create_app(args.data_root)
    logging.basicConfig(level=logging.INFO)
    print(f'Quest3Space dashboard: http://127.0.0.1:{args.port}',flush=True)
    print(f'Data: {application.state.service.root}',flush=True)
    uvicorn.run(application,host=args.host,port=args.port,log_level='info')
