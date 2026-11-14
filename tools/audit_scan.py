"""Read-only diagnostics of saved Quest captures; outputs are separate from originals."""
import argparse,json
from pathlib import Path
import cv2,numpy as np,trimesh

p=argparse.ArgumentParser();p.add_argument('session');p.add_argument('--root',default='E:/Quest3Space');a=p.parse_args()
root=Path(a.root);folder=root/'captures'/a.session;state=json.loads((folder/'session.json').read_text());out=root/'analysis'/a.session;out.mkdir(parents=True,exist_ok=True)
records=[];samples=[]
files=sorted(folder.glob('*.jpg'))
for i,path in enumerate(files):
    image=cv2.imread(str(path));h,w=image.shape[:2];meta=json.loads(path.with_suffix('.json').read_text());d=np.load(path.with_suffix('.depth.npy'))
    z=meta['depth_z_params'][0]/(2*d-1+meta['depth_z_params'][1]);valid=np.isfinite(z)&(z>.2)&(z<5)
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    records.append({'frame':path.stem,'rgb_size':[w,h],'laplacian_variance':float(cv2.Laplacian(gray,cv2.CV_32F).var()),'depth_valid_fraction':float(valid.mean()),'depth_median_m':float(np.median(z[valid])) if valid.any() else None})
    if i in set(np.linspace(0,len(files)-1,4,dtype=int)):
        dh,dw=d.shape;y,x=np.mgrid[:dh,:dw];ndc=np.stack([2*(x+.5)/dw-1,2*(y+.5)/dh-1,2*d-1,np.ones_like(d)],-1)
        world=ndc.reshape(-1,4)@np.linalg.inv(np.array(meta['depth_world_to_clip']).reshape(4,4)).T;world=world[:,:3]/world[:,3:]
        origin=np.array(state['origin']);inside=(np.abs(world-origin)<np.array(state['size_mm'])/2000).all(1)&valid.ravel()
        camera=world@np.array(meta['rgb_world_to_camera']).reshape(4,4)[:3,:3].T+np.array(meta['rgb_world_to_camera']).reshape(4,4)[:3,3]
        fx,fy,cx,cy=meta['intrinsics'];uv=np.round(camera[:,:2]/camera[:,2:]*[fx,fy]+[cx,cy]).astype(int)
        inside&=(camera[:,2]>0)&(uv[:,0]>=0)&(uv[:,0]<w)&(uv[:,1]>=0)&(uv[:,1]<h)
        overlay=image.copy();pixels=uv[inside][::3]
        for u,v in pixels:cv2.circle(overlay,(int(u),int(v)),1,(0,230,50),-1)
        original=cv2.resize(image,(512,384));overlay=cv2.resize(overlay,(512,384))
        cv2.putText(original,f'frame {i}: RGB',(12,24),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,255,255),1)
        samples.append(np.hstack([original,overlay]))
report={'session':state,'saved_frames':len(files),'voxel_mm':np.array(state['size_mm']).tolist()[0]/state['resolution'],'truncation_mm':max(state['size_mm'][0]/state['resolution']*4,8),'frames':records}
model=root/'exports'/a.session/'model.glb'
if model.exists():
    m=trimesh.load(model,force='mesh',process=False);plain=trimesh.Trimesh(m.vertices,m.faces,process=False);plain.merge_vertices(merge_tex=True,merge_norm=True);parts=plain.split(only_watertight=False)
    report['mesh']={'faces':len(m.faces),'bounds_mm':(m.extents*1000).tolist(),'components':len(parts),'largest_component_faces':sorted([len(x.faces) for x in parts],reverse=True)[:12],'watertight':bool(plain.is_watertight)}
if samples:cv2.imwrite(str(out/'rgb-depth-projection.jpg'),np.vstack(samples))
(out/'audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k not in ['frames','session']},indent=2));print('sharpness min/median/max',np.percentile([r['laplacian_variance'] for r in records],[0,50,100]).tolist());print(out)
