"""Immutable scan snapshots and a coalescing, single-job refinement scheduler."""
import json,os,re,shutil,subprocess,sys,threading,time
from pathlib import Path
from fastapi import HTTPException
from pc.offline_jobs import OfflineJobs as FinishedJobs

def save_json(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value));os.replace(temp,path)

class OfflineJobs(FinishedJobs):
    def __init__(self,root):
        super().__init__(root)
        self.tools=Path(os.environ.get('QUEST3SPACE_TOOLS_ROOT',str(self.root/'tools')))
        self.pending={};self.last={};self.event=threading.Event();self.setting=self.root/'iteration.json'
        self.enabled=json.loads(self.setting.read_text()).get('enabled',True) if self.setting.exists() else True
        self.available=(self.tools/'OpenMVS-2.4.0/vc17/x64/Release/TextureMesh.exe').is_file()
        threading.Thread(target=self.schedule,daemon=True,name='refinement-scheduler').start()
    def configure(self,enabled):
        with self.lock:
            self.enabled=bool(enabled);save_json(self.setting,{'enabled':self.enabled})
            if not self.enabled:self.pending.clear()
        self.event.set();return self.configuration()
    def configuration(self):
        return {'enabled':self.enabled,'available':self.available,'minimum_frames':24,'frame_step':16,'interval_seconds':30}
    def offer(self,sid,state,preview,frames):
        if not self.enabled or not self.available or state.get('restoring') or not preview or not preview.get('faces') or len(frames)<24 or state.get('coverage_bins',0)<3:return
        with self.lock:self.pending[sid]=(state.copy(),preview,tuple(frames))
        self.event.set()
    def schedule(self):
        while True:
            self.event.wait(2);self.event.clear()
            with self.lock:
                if not self.enabled or self.active is not None:continue
                for sid,snapshot in list(self.pending.items()):
                    previous,when=self.last.get(sid,(0,0));count=len(snapshot[2]);final=snapshot[0].get('status')=='complete'
                    if count<=previous:continue
                    if not final and (count-previous<16 or time.monotonic()-when<30):continue
                    try:self.start(sid,snapshot)
                    except Exception:continue
                    self.last[sid]=(count,time.monotonic());self.pending.pop(sid,None);break
    def records(self,sid):
        if not re.fullmatch('[a-f0-9]{16}',sid):raise HTTPException(400,'Invalid session')
        records=[]
        for path in (self.root/'offline'/sid).glob('*/job.json'):
            try:records.append((path,json.loads(path.read_text())))
            except (OSError,ValueError):continue
        return sorted(records,key=lambda item:item[1].get('created_at',0),reverse=True)
    def latest_complete(self,sid):
        return next(((p,s) for p,s in self.records(sid) if s['status']=='complete'),None)
    def status(self,sid):
        records=self.records(sid);state=records[0][1].copy() if records else {'status':'none','files':[]}
        if state['status']=='running' and (self.active is None or self.active[0]!=sid):state.update(status='interrupted',error='PC service restarted; intermediate files are preserved')
        complete=next(((p,s) for p,s in records if s['status']=='complete'),None)
        if complete:
            path,ready=complete;folder=path.parent;state.update(available_run=folder.name,available_frames=ready.get('source_frames'),available_created_at=ready['created_at'])
            state['files']=[f'/api/offline/{sid}/{folder.name}/{f.name}' for f in sorted(folder.iterdir()) if f.is_file() and (f.name.startswith('quality') and f.suffix in ['.glb','.obj','.mtl','.jpg','.png'] or f.name=='report.json')]
        state.update(iteration_enabled=self.enabled,iteration_available=self.available,pending_frames=len(self.pending[sid][2]) if sid in self.pending else 0)
        return state
    def start(self,sid,snapshot=None):
        if not re.fullmatch('[a-f0-9]{16}',sid):raise HTTPException(400,'Invalid session')
        path=self.root/'captures'/sid/'session.json'
        if not path.is_file():raise HTTPException(404,'Scan missing')
        state=snapshot[0] if snapshot else json.loads(path.read_text())
        if not snapshot and (state.get('status')!='complete' or state.get('method')!='depth-tsdf'):raise HTTPException(409,'Finish a depth scan with an exported mesh first')
        with self.lock:
            if self.active is not None:raise HTTPException(409,'Another offline reconstruction is running')
            if shutil.disk_usage(self.root).free<2*1024**3:raise HTTPException(507,'Need 2 GiB free space for background refinement')
            run=time.strftime('%Y%m%d-%H%M%S')+'-'+os.urandom(2).hex();folder=self.root/'offline'/sid/run;folder.mkdir(parents=True)
            job={'status':'running','run':run,'created_at':time.time(),'files':[],'iterative':snapshot is not None,'source_frames':len(snapshot[2]) if snapshot else state.get('accepted')}
            save_json(folder/'job.json',job);self.active=(sid,None)
            threading.Thread(target=self.execute,args=(sid,state,snapshot,folder,job),daemon=True,name='offline-job').start();return job
    def execute(self,sid,state,snapshot,folder,job):
        try:
            source=self.root
            if snapshot:
                import numpy as np,trimesh
                source=folder/'snapshot';captures=source/'captures'/sid;exports=source/'exports'/sid;captures.mkdir(parents=True);exports.mkdir(parents=True)
                for fid in snapshot[2]:
                    for suffix in ['.jpg','.json','.depth.npy']:
                        src=self.root/'captures'/sid/(fid+suffix);dst=captures/src.name
                        try:os.link(src,dst)
                        except OSError:shutil.copy2(src,dst)
                snap_state=state.copy();snap_state.update(status='complete',snapshot=True,snapshot_frame_count=len(snapshot[2]));save_json(captures/'session.json',snap_state)
                data=snapshot[1];v=np.array(data['vertices'])[:,[0,2,1]].copy();v[:,2]*=-1
                mesh=trimesh.Trimesh(v,np.array(data['faces']),process=False);mesh.visual.face_colors=np.array(data['colors'],np.uint8);mesh.export(exports/'model.glb')
            env=os.environ.copy();threads='8' if snapshot else '16'
            env.update(OMP_NUM_THREADS=threads,OPENBLAS_NUM_THREADS=threads,MKL_NUM_THREADS=threads,QUEST3SPACE_TOOLS_ROOT=str(self.tools),TEMP=str(self.root/'temp'),TMP=str(self.root/'temp'))
            with open(folder/'pipeline.log','wb') as log:
                proc=subprocess.Popen([sys.executable,'-m','pc.offline',sid,'--root',str(source),'--geometry','live','--refine','--output',str(folder)],cwd=Path(__file__).resolve().parent.parent,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                with self.lock:self.active=(sid,proc)
                code=proc.wait()
            if code or not (folder/'quality.glb').is_file():raise RuntimeError('Offline pipeline failed; inspect '+str(folder/'pipeline.log'))
            self.publish_preview(folder);job['status']='complete'
        except Exception as error:job.update(status='failed',error=str(error))
        finally:
            with self.lock:job['finished_at']=time.time();save_json(folder/'job.json',job);self.active=None
            self.event.set()
    def publish_preview(self,folder):
        import numpy as np,trimesh,open3d as o3d
        m=trimesh.load(folder/'quality.glb',force='mesh',process=False)
        colors=np.asarray(m.visual.to_color().vertex_colors)[:,:3]/255 if hasattr(m.visual,'to_color') else np.asarray(m.visual.vertex_colors)[:,:3]/255
        mesh=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(m.vertices),o3d.utility.Vector3iVector(m.faces));mesh.vertex_colors=o3d.utility.Vector3dVector(colors)
        # OBJ/UV seams duplicate vertices; weld them before geometry simplification.
        mesh=mesh.merge_close_vertices(1e-7)
        mesh.remove_degenerate_triangles();mesh.remove_duplicated_triangles();mesh.remove_unreferenced_vertices()
        if len(mesh.triangles)>12000:mesh=mesh.simplify_quadric_decimation(12000)
        mesh.remove_unreferenced_vertices()
        vertices=np.asarray(mesh.vertices)[:,[0,2,1]].copy();vertices[:,1]*=-1;faces=np.asarray(mesh.triangles)
        colors=np.clip(np.asarray(mesh.vertex_colors)[faces].mean(1)*255,0,255).astype(np.uint8)
        save_json(folder/'preview.json',{'vertices':np.round(vertices,5).tolist(),'faces':faces.tolist(),'colors':colors.tolist()})
    def publish_textured_preview(self,folder):
        import base64,io,numpy as np,trimesh
        from PIL import Image
        m=trimesh.load(folder/'quality.glb',force='mesh',process=False)
        if len(m.faces)>100000:raise HTTPException(413,'Textured preview exceeds headset triangle budget')
        visual=m.visual
        material=getattr(visual,'material',None)
        photo=getattr(material,'baseColorTexture',None)
        if photo is None:photo=getattr(material,'image',None)
        if photo is None or getattr(visual,'uv',None) is None:raise HTTPException(409,'No photo texture available')
        photo=photo.convert('RGB');photo.thumbnail((4096,4096),Image.Resampling.LANCZOS)
        encoded=io.BytesIO();photo.save(encoded,format='JPEG',quality=92)
        vertices=np.asarray(m.vertices)[:,[0,2,1]].copy();vertices[:,1]*=-1
        save_json(folder/'textured-preview.json',{'vertices':np.round(vertices,6).tolist(),'faces':m.faces.tolist(),
            'uv':np.round(visual.uv,7).tolist(),'texture_jpeg':base64.b64encode(encoded.getvalue()).decode('ascii'),
            'texture_size':list(photo.size),'representation':'photo-textured'})
