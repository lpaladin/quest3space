import json,tempfile,unittest
from pathlib import Path
import numpy as np
from fastapi.testclient import TestClient
from pc.offline import depth_camera
from pc.server import create_app
from test_pipeline import sphere_frame,TEST_TEMP

class OfflineTests(unittest.TestCase):
    def test_rotated_volume_world_projection_and_preview_coordinates(self):
        from pc.reconstruction import DepthFusion
        from scipy.spatial.transform import Rotation
        rotation=Rotation.from_euler('xyz',[25,40,15],degrees=True)
        origin=np.array([.2,1.1,-.4]);fusion=DepthFusion([.2,.3,.4],32,origin,rotation.as_quat())
        basis=np.array([[1,0,0],[0,0,1],[0,-1,0]],float)
        np.testing.assert_allclose(fusion.world,fusion.points@(rotation.as_matrix()@basis).T+origin,atol=1e-7)
        # A measured local x=0 plane must be exported as a rotated plane in world-offset coordinates.
        fusion.tsdf=fusion.points[:,0].copy();fusion.weights[:]=1
        mesh=fusion.mesh();normal=basis.T@rotation.as_matrix()@basis[:,0]
        np.testing.assert_allclose(np.asarray(mesh.vertices)@normal,0,atol=1e-6)
        self.assertGreater(len(mesh.faces),100)
        identity=DepthFusion([.2,.3,.4],32,origin)
        np.testing.assert_allclose(identity.local_to_world[:3,:3],basis)

    def test_texture_selection_preserves_rare_view_and_prefers_sharp_frame(self):
        from pc.keyframes import select_texture_frames,view_bin
        def frame(name,center,sharp):
            E=np.eye(4);E[:3,3]=-np.array(center)
            return {'name':name,'E':E.tolist(),'sharpness':sharp}
        frames=[frame(str(i),[1,0,0],100+i) for i in range(10)]
        frames.extend([frame('rare-blurry',[0,1,1],1),frame('rare-best',[0,1,1],2)])
        selected,report=select_texture_frames(frames);names={f['name'] for f in selected}
        self.assertIn('rare-best',names);self.assertNotIn('rare-blurry',names)
        self.assertEqual({view_bin(f) for f in frames},{view_bin(f) for f in selected})
        self.assertEqual(report['directional_fallbacks'],['rare-best'])

    def test_iteration_coalesces_snapshots_and_keeps_last_success(self):
        from pc.iterative_jobs import OfflineJobs
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as root:
            jobs=OfflineJobs(root);jobs.available=True;jobs.active=('busy',None)
            sid='b'*16;state={'coverage_bins':3,'status':'scanning'};preview={'faces':[[0,1,2]]}
            frames=[str(i) for i in range(24)];jobs.offer(sid,state,preview,frames)
            frames.append('new');state['coverage_bins']=9
            self.assertEqual(len(jobs.pending[sid][2]),24);self.assertEqual(jobs.pending[sid][0]['coverage_bins'],3)
            jobs.offer(sid,state,preview,[str(i) for i in range(40)])
            self.assertEqual(len(jobs.pending),1);self.assertEqual(len(jobs.pending[sid][2]),40)
            base=Path(root)/'offline'/sid
            for run,status,created in [('v1','complete',1),('v2','running',2)]:
                folder=base/run;folder.mkdir(parents=True);(folder/'job.json').write_text(json.dumps({'run':run,'status':status,'created_at':created,'source_frames':24 if run=='v1' else 40}))
            (base/'v1'/'quality.glb').write_bytes(b'glTF')
            result=jobs.status(sid);self.assertEqual(result['available_run'],'v1');self.assertTrue(result['files'])
            jobs.configure(False);jobs.active=None;self.assertFalse(jobs.pending)

    def test_meta_depth_camera_preserves_metric_depth_and_pixel_origin(self):
        _,_,meta,raw=sphere_frame(.7,pixels=64);origin=np.array([0.,1.,1.]);K,E,U=depth_camera(meta,raw.shape,origin)
        y,x=np.mgrid[:64,:64];clip=np.stack([2*(x+.5)/64-1,2*(y+.5)/64-1,2*raw-1,np.ones_like(raw)],-1)
        world=clip.reshape(-1,4)@np.linalg.inv(np.array(meta['depth_world_to_clip']).reshape(4,4)@U).T;world=world[:,:3]/world[:,3:]
        cam=world@E[:3,:3].T+E[:3,3];pixel=cam@K.T;pixel=pixel[:,:2]/pixel[:,2:]
        np.testing.assert_allclose(pixel[:,0],x.ravel(),atol=1e-4)
        np.testing.assert_allclose(pixel[:,1],63-y.ravel(),atol=1e-4)
        z=meta['depth_z_params'][0]/(2*raw.ravel()-1+meta['depth_z_params'][1])
        np.testing.assert_allclose(cam[:,2],z,atol=1e-4)

    def test_installed_tsdf_fuses_known_plane(self):
        import open3d as o
        color=o.geometry.Image(np.full((64,64,3),128,np.uint8));depth=o.geometry.Image(np.full((64,64),.5,np.float32))
        rgbd=o.geometry.RGBDImage.create_from_color_and_depth(color,depth,depth_scale=1,depth_trunc=2,convert_rgb_to_intensity=False)
        volume=o.pipelines.integration.ScalableTSDFVolume(.01,.04,o.pipelines.integration.TSDFVolumeColorType.RGB8)
        volume.integrate(rgbd,o.camera.PinholeCameraIntrinsic(64,64,56,56,32,32),np.eye(4))
        m=volume.extract_triangle_mesh();self.assertGreater(len(m.vertices),100)
        np.testing.assert_allclose(np.asarray(m.vertices)[:,2],.5,atol=.011)

    def test_offline_auth_state_and_output_path(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as root:
            client=TestClient(create_app(root,'TEST'));sid='a'*16
            self.assertEqual(client.get(f'/api/sessions/{sid}/offline').status_code,401)
            headers={'x-scan-token':'TEST'}
            self.assertEqual(client.get(f'/api/sessions/{sid}/offline',headers=headers).json()['status'],'none')
            folder=Path(root)/'captures'/sid;folder.mkdir();(folder/'session.json').write_text(json.dumps({'status':'scanning','method':'depth-tsdf'}))
            self.assertEqual(client.post(f'/api/sessions/{sid}/offline',headers=headers).status_code,409)
            self.assertEqual(client.get(f'/api/offline/{sid}/run/frames.json',headers=headers).status_code,404)
            import trimesh
            from PIL import Image
            mesh=trimesh.Trimesh(vertices=[[0,0,0],[.1,0,0],[0,.1,0]],faces=[[0,1,2]],process=False)
            mesh.visual=trimesh.visual.texture.TextureVisuals(uv=[[0,0],[1,0],[0,1]],image=Image.new('RGB',(4,4),(200,80,40)))
            export=Path(root)/'exports'/sid;export.mkdir();mesh.export(export/'model.glb')
            (folder/'session.json').write_text(json.dumps({'status':'complete','method':'depth-tsdf'}))
            response=client.get(f'/api/sessions/{sid}/preview',headers=headers)
            self.assertEqual(response.status_code,200);self.assertEqual(len(response.json()['faces']),1)
            np.testing.assert_allclose(response.json()['colors'][0],[200,80,40],atol=1)
            for run,created in [('version1',1),('version2',2)]:
                ready=Path(root)/'offline'/sid/run;ready.mkdir(parents=True)
                (ready/'job.json').write_text(json.dumps({'status':'complete','created_at':created}))
                (ready/'preview.json').write_text(json.dumps({'version':run}))
            pinned=f'/api/sessions/{sid}/preview?run=version1'
            self.assertEqual(client.get(pinned).status_code,401)
            response=client.get(pinned,headers=headers)
            self.assertEqual(response.json()['version'],'version1')
            self.assertEqual(response.headers['x-refinement-run'],'version1')
            self.assertEqual(client.get(f'/api/sessions/{sid}/preview?refined=true',headers=headers).json()['version'],'version2')
            self.assertEqual(client.get(f'/api/sessions/{sid}/preview?run=missing',headers=headers).status_code,404)
            mesh.export(Path(root)/'offline'/sid/'version2'/'quality.glb')
            textured=client.get(f'/api/sessions/{sid}/preview?run=version2&textured=true',headers=headers)
            self.assertEqual(textured.status_code,200)
            payload=textured.json();self.assertEqual(payload['representation'],'photo-textured')
            self.assertEqual(len(payload['uv']),len(payload['vertices']))
            import base64,io
            decoded=Image.open(io.BytesIO(base64.b64decode(payload['texture_jpeg'])))
            self.assertEqual(decoded.size,(4,4))
            np.testing.assert_allclose(np.asarray(decoded)[0,0],[200,80,40],atol=3)

if __name__=='__main__':unittest.main()
