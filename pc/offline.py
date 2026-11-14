"""Offline measured-depth fusion, conservative ICP and full-resolution OpenMVS texturing.

Every run writes to a separate directory. The input capture and live output stay untouched.
World frame here is right-handed: Unity x,y,-z, relative to the scan origin.
"""
from __future__ import annotations
import argparse,json,os,shutil,subprocess,time
from pathlib import Path
import cv2,numpy as np
from scipy.spatial.transform import Rotation

def depth_camera(meta,shape,origin):
    h,w=shape
    U=np.diag([1.,1.,-1.,1.]);U[:3,3]=origin
    M=np.asarray(meta['depth_world_to_clip']).reshape(4,4)@U
    pixels=np.array([[w/2,0,0,w/2-.5],[0,-h/2,0,h/2-.5],[0,0,0,1.]])
    K,R,C,*_=cv2.decomposeProjectionMatrix(pixels@M)
    K/=K[2,2];C=(C[:3]/C[3]).ravel()
    E=np.eye(4);E[:3,:3]=R;E[:3,3]=-R@C
    if K[0,0]<=0 or K[1,1]<=0:raise ValueError('Depth camera decomposition has invalid focal length')
    return K,E,U

def decode_depth(raw,meta):
    a,b=meta['depth_z_params']
    with np.errstate(divide='ignore',invalid='ignore'):z=a/(2*raw-1+b)
    valid=np.isfinite(z)&(raw>0)&(raw<.999999)&(z>=.2)&(z<=5)
    clean=np.where(valid,z,0).astype(np.float32)
    filtered=cv2.bilateralFilter(clean,5,.008,2)
    # Do not blend a background sample across a depth discontinuity.
    local_min=cv2.erode(np.where(valid,clean,100).astype(np.float32),np.ones((3,3),np.uint8))
    local_max=cv2.dilate(clean,np.ones((3,3),np.uint8))
    valid&=(local_max-local_min)<.04
    return np.where(valid,filtered,0).astype(np.float32)

def run_tool(binary,args,work):
    command=[str(binary),*map(str,args)];print('RUN',binary.name,flush=True)
    result=subprocess.run(command,cwd=work,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (work/(binary.stem+'-console.txt')).write_bytes(result.stdout)
    if result.returncode:raise RuntimeError(f'{binary.name} exited {result.returncode}; inspect logs in {work}')

def texture_saved(work,root,refine=False,planes=None,origin=None):
    import open3d as o3d
    binary=Path(os.environ.get('QUEST3SPACE_TOOLS_ROOT',str(root/'tools')))/'OpenMVS-2.4.0'/'vc17'/'x64'/'Release'
    mesh=o3d.io.read_triangle_mesh(str(work/'measured.ply'))
    if len(mesh.triangles)>80000:mesh=mesh.simplify_quadric_decimation(80000,boundary_weight=10.)
    mesh=mesh.filter_smooth_taubin(number_of_iterations=2)
    mesh.remove_degenerate_triangles();mesh.remove_duplicated_triangles();mesh.remove_unreferenced_vertices()
    mesh_path=work/'clean.ply';o3d.io.write_triangle_mesh(str(mesh_path),mesh)
    if refine:
        run_tool(binary/'RefineMesh.exe',[work/'scene.mvs','-m',mesh_path,'-o',work/'refined.mvs','--resolution-level','0','--max-threads',os.environ.get('OMP_NUM_THREADS','16'),'--close-holes','0','--scales','2'],work)
        mesh_path=work/'refined.ply'
    if planes:
        from pc.plane_region import inside_planes
        bounded=o3d.io.read_triangle_mesh(str(mesh_path))
        world=np.asarray(bounded.vertices)@np.diag([1.,1.,-1.])+np.asarray(origin)
        keep=inside_planes(world,planes,margin=1e-5)
        bounded.remove_triangles_by_mask(~keep[np.asarray(bounded.triangles)].all(1));bounded.remove_unreferenced_vertices()
        if not len(bounded.triangles):raise ValueError('No refined surface remains inside plane region')
        mesh_path=work/'region-clipped.ply';o3d.io.write_triangle_mesh(str(mesh_path),bounded)
    run_tool(binary/'TextureMesh.exe',[work/'scene.mvs','-m',mesh_path,'-o',work/'quality.mvs','--export-type','obj','--resolution-level','0','--close-holes','0','--max-texture-size','8192','--max-threads','8','--sharpness-weight','0','--virtual-face-images','0','--global-seam-leveling','0','--local-seam-leveling','0'],work)
    import trimesh
    textured=trimesh.load(work/'quality.obj',force='scene',process=False)
    textured.apply_transform(np.diag([1.,1.,-1.,1.]));textured.export(work/'quality.glb')
    return len(mesh.triangles)

def main():
    p=argparse.ArgumentParser();p.add_argument('session');p.add_argument('--root',default='E:/Quest3Space');p.add_argument('--voxel-mm',type=float,default=4);p.add_argument('--geometry',choices=['live','tsdf'],default='live');p.add_argument('--no-icp',action='store_true');p.add_argument('--texture-only',action='store_true');p.add_argument('--refine',action='store_true');p.add_argument('--output',default='');a=p.parse_args()
    if a.geometry=='live':a.no_icp=True
    import open3d as o3d
    root=Path(a.root);capture=root/'captures'/a.session;state=json.loads((capture/'session.json').read_text())
    if state['status'] not in ['complete','failed']:raise ValueError('Finish capture before offline reconstruction')
    work=Path(a.output) if a.output else root/'offline'/a.session/time.strftime('%Y%m%d-%H%M%S')
    work.mkdir(parents=True,exist_ok=True);(work/'images').mkdir(exist_ok=True);sparse=work/'sparse';sparse.mkdir(exist_ok=True)
    if a.texture_only:
        faces=texture_saved(work,root,a.refine);(work/'texture-report.json').write_text(json.dumps({'faces_before_photometric_refinement':faces,'photometric_mesh_refinement':a.refine,'global_seam_leveling':False,'local_seam_leveling':False,'full_resolution':True},indent=2));print('OFFLINE_TEXTURE_COMPLETE',work,faces,flush=True);return
    origin=np.array(state['origin']);size=np.array(state['size_mm'])/1000;voxel=a.voxel_mm/1000
    flip=np.diag([1.,1.,-1.]);box_rotation=flip@Rotation.from_quat(state.get('rotation',[0,0,0,1])).as_matrix()@flip
    from pc.plane_region import inside_planes
    if not .001<=voxel<=.02:raise ValueError('Voxel must be 1–20 mm')
    volume=o3d.pipelines.integration.ScalableTSDFVolume(voxel_length=voxel,sdf_trunc=voxel*4,color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8)
    target=None;frames=[];started=time.time()
    paths=sorted(capture.glob('*.jpg'));bounds=o3d.geometry.OrientedBoundingBox(np.zeros(3),box_rotation,size+.05)
    for index,path in enumerate(paths):
        meta=json.loads(path.with_suffix('.json').read_text());image=cv2.imread(str(path));h,w=image.shape[:2]
        if image.std()<.5:continue
        # Meta readback rows start at the bottom; Open3D images start at the top.
        depth=decode_depth(np.load(path.with_suffix('.depth.npy')),meta)[::-1].copy();dh,dw=depth.shape
        K,E,U=depth_camera(meta,depth.shape,origin);intrinsic=o3d.camera.PinholeCameraIntrinsic(dw,dh,K[0,0],K[1,1],K[0,2],K[1,2])
        dimg=o3d.geometry.Image(depth)
        cloud=o3d.geometry.PointCloud.create_from_depth_image(dimg,intrinsic,E,depth_scale=1.,depth_trunc=5.,stride=1).crop(bounds).voxel_down_sample(voxel*2)
        if len(cloud.points)<60:continue
        correction=np.eye(4);fitness=None
        if target is not None and not a.no_icp:
            target.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=voxel*8,max_nn=30))
            result=o3d.pipelines.registration.registration_icp(cloud,target,.015,np.eye(4),o3d.pipelines.registration.TransformationEstimationPointToPlane(),o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=20))
            delta=result.transformation;angle=Rotation.from_matrix(delta[:3,:3]).magnitude()
            if result.fitness>.5 and np.linalg.norm(delta[:3,3])<.01 and angle<np.deg2rad(1.5):
                correction=delta;fitness=result.fitness;cloud.transform(delta)
        if target is None:target=cloud
        else:target=(target+cloud).voxel_down_sample(voxel*2)
        E=E@np.linalg.inv(correction)
        # Register the independent RGB camera onto this depth camera's rays.
        yr,xr=np.mgrid[:dh,:dw];camera=np.stack([(xr-K[0,2])/K[0,0]*depth,(yr-K[1,2])/K[1,1]*depth,depth],-1)
        world=camera.reshape(-1,3)@np.linalg.inv(E)[:3,:3].T+np.linalg.inv(E)[:3,3]
        rgbE=np.asarray(meta['rgb_world_to_camera']).reshape(4,4)@U@np.linalg.inv(correction)
        xyz=world@rgbE[:3,:3].T+rgbE[:3,3];fx,fy,cx,cy=meta['intrinsics']
        with np.errstate(divide='ignore',invalid='ignore'):uv=xyz[:,:2]/xyz[:,2:]*[fx,fy]+[cx,cy]
        uv=np.nan_to_num(uv,nan=-1,posinf=-1,neginf=-1).reshape(dh,dw,2).astype(np.float32)
        registered=cv2.remap(image,uv[:,:,0],uv[:,:,1],cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)[:,:,::-1].copy()
        # Activate only blocks near the requested object, not the whole room.
        box_points=(world@box_rotation).reshape(dh,dw,3)
        inside=(np.abs(box_points)<=size/2+.025).all(2)
        inside&=inside_planes(world@flip+origin,state.get('planes')).reshape(dh,dw)
        dimg=o3d.geometry.Image(np.where(inside,depth,0).astype(np.float32))
        rgbd=o3d.geometry.RGBDImage.create_from_color_and_depth(o3d.geometry.Image(registered),dimg,depth_scale=1.,depth_trunc=5.,convert_rgb_to_intensity=False)
        if a.geometry=='tsdf':volume.integrate(rgbd,intrinsic,E)
        # Sharpness is measured over the projected object, rather than the background.
        mask=(depth>0)&(np.abs(box_points)<=size/2).all(2)
        mask&=inside_planes(world@flip+origin,state.get('planes')).reshape(dh,dw)
        pix=uv[mask];roi=np.zeros((h,w),np.uint8)
        good=(pix[:,0]>=0)&(pix[:,0]<w)&(pix[:,1]>=0)&(pix[:,1]<h)
        pix=pix[good].astype(int);roi[pix[:,1],pix[:,0]]=1
        roi=cv2.dilate(roi,np.ones((9,9),np.uint8));gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY);grad=cv2.Laplacian(gray,cv2.CV_32F)**2
        sharp=float(grad[roi>0].mean()) if roi.any() else 0
        frames.append({'name':path.name,'K':[fx,fy,cx,cy],'size':[w,h],'E':rgbE.tolist(),'sharpness':sharp,'icp_fitness':fitness,'correction':correction.tolist()})
        print(f'frame {index+1}/{len(paths)} points={len(cloud.points)} sharpness={sharp:.1f} ICP={fitness}',flush=True)
    if len(frames)<4:raise ValueError('Not enough depth frames intersect the object box')
    if a.geometry=='live':
        import trimesh
        baseline=trimesh.load(root/'exports'/a.session/'model.glb',force='mesh',process=False)
        baseline=trimesh.Trimesh(baseline.vertices,baseline.faces,process=False);baseline.merge_vertices();baseline.apply_transform(np.diag([1.,1.,-1.,1.]))
        mesh=o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(baseline.vertices),o3d.utility.Vector3iVector(baseline.faces))
    else:mesh=volume.extract_triangle_mesh()
    print('TSDF raw mesh',len(mesh.vertices),len(mesh.triangles),'bounds',mesh.get_min_bound(),mesh.get_max_bound(),flush=True)
    mesh=mesh.crop(o3d.geometry.OrientedBoundingBox(np.zeros(3),box_rotation,size))
    if state.get('planes'):
        keep=inside_planes(np.asarray(mesh.vertices)@flip+origin,state['planes'],margin=1e-5)
        mesh.remove_triangles_by_mask(~keep[np.asarray(mesh.triangles)].all(1));mesh.remove_unreferenced_vertices()
    print('Cropped mesh',len(mesh.vertices),len(mesh.triangles),flush=True)
    mesh.remove_degenerate_triangles();mesh.remove_duplicated_triangles();mesh.remove_unreferenced_vertices()
    labels,counts,areas=mesh.cluster_connected_triangles();counts=np.array(counts);areas=np.array(areas)
    # Remove only tiny disconnected islands, never fill an unmeasured hole.
    remove=(counts[np.array(labels)]<24)&(areas[np.array(labels)]<voxel*voxel*100)
    removed=int(remove.sum());mesh.remove_triangles_by_mask(remove);mesh.remove_unreferenced_vertices();mesh.compute_vertex_normals()
    if not len(mesh.vertices):raise ValueError('No reliable surface after cropping/cleanup; no model exported')
    o3d.io.write_triangle_mesh(str(work/'measured.ply'),mesh,write_vertex_colors=True)
    (work/'frames.json').write_text(json.dumps(frames,indent=2))
    # Photographic texturing uses the sharp half plus directional fallbacks.
    from pc.keyframes import select_texture_frames
    chosen,selection=select_texture_frames(frames)
    (work/'keyframe-selection.json').write_text(json.dumps(selection,indent=2))
    camera_lines=[];image_lines=[]
    for i,f in enumerate(chosen,1):
        shutil.copy2(capture/f['name'],work/'images'/f['name']);w,h=f['size'];fx,fy,cx,cy=f['K'];R=np.array(f['E'])[:3,:3];t=np.array(f['E'])[:3,3];q=Rotation.from_matrix(R).as_quat();q=[q[3],*q[:3]]
        camera_lines.append(f'{i} PINHOLE {w} {h} {fx} {fy} {cx} {cy}')
        image_lines.extend([f'{i} '+ ' '.join(map(str,[*q,*t]))+f' {i} {f["name"]}',''])
    (sparse/'cameras.txt').write_text('\n'.join(camera_lines)+'\n');(sparse/'images.txt').write_text('\n'.join(image_lines)+'\n');(sparse/'points3D.txt').write_text('# Depth-fused mesh supplied separately.\n')
    binary=Path(os.environ.get('QUEST3SPACE_TOOLS_ROOT',str(root/'tools')))/'OpenMVS-2.4.0'/'vc17'/'x64'/'Release'
    run_tool(binary/'InterfaceCOLMAP.exe',['-i',work,'-o',work/'scene.mvs','--image-folder',work/'images','--no-points','1'],work)
    texture_saved(work,root,a.refine,state.get('planes'),origin)
    report={'source_session':a.session,'geometry_source':a.geometry,'voxel_mm':a.voxel_mm if a.geometry=='tsdf' else state['size_mm'][0]/state['resolution'],'input_frames':len(paths),'geometry_frames':len(frames),'texture_frames':len(chosen),'raw_faces':int(len(labels)),'tiny_island_faces_removed':removed,'geometry_faces_before_refinement':len(mesh.triangles),'elapsed_seconds':time.time()-started,'icp_enabled':not a.no_icp,'photometric_mesh_refinement':a.refine,'unseen_holes_filled':False,'global_seam_leveling':False,'local_seam_leveling':False,'origin_unity':origin.tolist(),'originals_modified':False}
    import trimesh
    final=trimesh.load(work/'quality.glb',force='mesh',process=False);final=trimesh.Trimesh(final.vertices,final.faces,process=False);final.merge_vertices()
    report['final_faces']=len(final.faces);report['final_components']=len(final.split(only_watertight=False));report['warning']='Unobserved surfaces are not filled. Photo refinement is an estimate; metric accuracy has not been independently measured.'
    report['snapshot']=state.get('snapshot',False);report['snapshot_frame_count']=state.get('snapshot_frame_count')
    report['texture_selection']=selection
    (work/'report.json').write_text(json.dumps(report,indent=2))
    renderer=Path(__file__).resolve().parent.parent/'tools'/'render_compare.py'
    run_tool(Path(os.sys.executable),[renderer,root/'exports'/a.session/'model.glb',work/'quality.glb',work/'quality-comparison.jpg'],work)
    print('OFFLINE_COMPLETE',work,flush=True)

if __name__=='__main__':main()
