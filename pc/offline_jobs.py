"""One bounded offline CPU job at a time; separate outputs and authenticated downloads."""
import json,os,re,subprocess,sys,threading,time
from pathlib import Path
from fastapi import HTTPException

class OfflineJobs:
    def __init__(self,root):
        self.root=Path(root);self.lock=threading.RLock();self.active=None
    def status(self,sid):
        if not re.fullmatch('[a-f0-9]{16}',sid):raise HTTPException(400,'Invalid session')
        paths=list((self.root/'offline'/sid).glob('*/job.json'))
        if not paths:return {'status':'none','files':[]}
        path=max(paths,key=lambda p:p.stat().st_mtime);state=json.loads(path.read_text());folder=path.parent
        if state['status']=='running' and self.active is None:
            state['status']='interrupted';state['error']='PC service restarted; saved intermediate files remain available'
        if state['status']=='complete':
            state['files']=[f'/api/offline/{sid}/{folder.name}/{f.name}' for f in sorted(folder.iterdir()) if f.is_file() and (f.name.startswith('quality') and f.suffix in ['.glb','.obj','.mtl','.jpg','.png'] or f.name=='report.json')]
        return state
    def start(self,sid):
        if not re.fullmatch('[a-f0-9]{16}',sid):raise HTTPException(400,'Invalid session')
        path=self.root/'captures'/sid/'session.json'
        if not path.is_file():raise HTTPException(404,'Scan missing')
        state=json.loads(path.read_text())
        if state.get('status')!='complete' or state.get('method')!='depth-tsdf':raise HTTPException(409,'Finish a depth scan with an exported mesh first')
        with self.lock:
            if self.active is not None:raise HTTPException(409,'Another offline reconstruction is running')
            run=time.strftime('%Y%m%d-%H%M%S')+'-'+os.urandom(2).hex();folder=self.root/'offline'/sid/run;folder.mkdir(parents=True)
            job={'status':'running','run':run,'created_at':time.time(),'files':[]};(folder/'job.json').write_text(json.dumps(job))
            log=open(folder/'pipeline.log','wb');env=os.environ.copy();env['OMP_NUM_THREADS']='16';env['TEMP']=str(self.root/'temp');env['TMP']=env['TEMP']
            try:
                proc=subprocess.Popen([sys.executable,'-m','pc.offline',sid,'--root',str(self.root),'--geometry','live','--refine','--output',str(folder)],cwd=Path(__file__).resolve().parent.parent,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except Exception:
                log.close();job['status']='failed';(folder/'job.json').write_text(json.dumps(job));raise
            log.close();self.active=(sid,proc)
            def watch():
                code=proc.wait()
                with self.lock:
                    job['status']='complete' if code==0 and (folder/'quality.glb').is_file() else 'failed';job['finished_at']=time.time()
                    if code:job['error']='Offline pipeline failed; inspect '+str(folder/'pipeline.log')
                    (folder/'job.json').write_text(json.dumps(job));self.active=None
            threading.Thread(target=watch,daemon=True).start()
            return job
