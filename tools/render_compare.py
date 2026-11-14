"""CPU raycast render of actual textured meshes; no generated pixels or geometry."""
import argparse
from pathlib import Path
import cv2,numpy as np,trimesh,open3d as o3d

def renderer(path):
    loaded=trimesh.load(path,force='scene',process=False);scene=o3d.t.geometry.RaycastingScene();materials={}
    for name,node in loaded.graph.nodes_geometry.items() if isinstance(loaded.graph.nodes_geometry,dict) else [(n,n) for n in loaded.graph.nodes_geometry]:
        transform,geom=loaded.graph[name];m=loaded.geometry[geom].copy();m.apply_transform(transform)
        gid=scene.add_triangles(o3d.core.Tensor(m.vertices.astype(np.float32)),o3d.core.Tensor(m.faces.astype(np.uint32)))
        image=getattr(getattr(m.visual,'material',None),'baseColorTexture',None)
        if image is None:image=getattr(getattr(m.visual,'material',None),'image',None)
        materials[gid]=(m,np.asarray(image.convert('RGB')) if image is not None else None)
    return loaded,scene,materials

def render(parts,center,eye,n=640):
    loaded,scene,materials=parts;forward=center-eye;forward/=np.linalg.norm(forward);right=np.cross(forward,[0,1,0]);right/=np.linalg.norm(right);up=np.cross(right,forward)
    y,x=np.mgrid[:n,:n];dirs=forward+(x[...,None]-n/2)/(n*1.1)*right-(y[...,None]-n/2)/(n*1.1)*up
    rays=np.concatenate([np.broadcast_to(eye,dirs.shape),dirs],axis=-1).astype(np.float32);hit=scene.cast_rays(o3d.core.Tensor(rays))
    ids=hit['primitive_ids'].numpy();gids=hit['geometry_ids'].numpy();bary=hit['primitive_uvs'].numpy();result=np.full((n,n,3),[30,37,45],np.uint8)
    for gid,(m,texture) in materials.items():
        mask=gids==gid;f=ids[mask];b=bary[mask];weights=np.column_stack([1-b.sum(1),b]);faces=m.faces[f]
        if texture is not None and hasattr(m.visual,'uv'):
            uv=(m.visual.uv[faces]*weights[:,:,None]).sum(1);height,width=texture.shape[:2];xy=np.column_stack([uv[:,0]*(width-1),(1-uv[:,1])*(height-1)]).astype(np.float32)
            colors=np.concatenate([cv2.remap(texture,part[:,0,None],part[:,1,None],cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)[:,0] for part in np.array_split(xy,max(1,int(np.ceil(len(xy)/30000))))])
        else:colors=np.broadcast_to([180,190,195],(len(f),3))
        result[mask]=colors
    return result[:,:,::-1].copy()

p=argparse.ArgumentParser();p.add_argument('old');p.add_argument('new');p.add_argument('out');a=p.parse_args();old=renderer(a.old);new=renderer(a.new);center=(old[0].bounds[0]+old[0].bounds[1])/2
rows=[]
for direction in [[.6,.6,.7],[-.6,.5,-.7]]:
    images=[]
    for title,model in [('LIVE BASELINE',old),('OFFLINE QUALITY',new)]:
        im=render(model,center,center+np.array(direction));cv2.putText(im,title,(20,30),cv2.FONT_HERSHEY_SIMPLEX,.7,(240,240,240),2);images.append(im)
    rows.append(np.hstack(images))
cv2.imwrite(a.out,np.vstack(rows));print(a.out)
