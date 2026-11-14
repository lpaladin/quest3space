import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
import cv2
import numpy as np
import trimesh
from pc.reconstruction import DepthFusion, Hull, board_pose, marker_corners
from pc.server import Service

TEST_TEMP=Path(__file__).resolve().parent.parent/'.build'/'test-temp'
TEST_TEMP.mkdir(parents=True,exist_ok=True)


def sphere_frame(angle, origin=np.array([0.,1.,1.]),pixels=192):
    center=origin+np.array([.5*np.cos(angle),.24,.5*np.sin(angle)])
    forward=origin-center;forward/=np.linalg.norm(forward)
    right=np.cross([0.,1.,0.],forward);right/=np.linalg.norm(right)
    up=np.cross(forward,right)
    w2c=np.eye(4);w2c[:3,:3]=np.stack([right,up,forward]);w2c[:3,3]=-w2c[:3,:3]@center
    near,far=.05,5.
    P=np.zeros((4,4));P[0,0]=P[1,1]=1.4;P[2,2]=-(far+near)/(far-near);P[2,3]=-2*far*near/(far-near);P[3,2]=-1
    flip=np.diag([1,1,-1,1]);M=P@flip@w2c
    u,v=np.meshgrid((np.arange(pixels)+.5)/pixels*2-1,(np.arange(pixels)+.5)/pixels*2-1)
    local=np.stack([u/1.4,v/1.4,np.ones_like(u)],axis=-1)
    rays=local@w2c[:3,:3]
    offset=center-origin
    aa=(rays*rays).sum(axis=-1);bb=2*(rays*offset).sum(axis=-1);cc=offset@offset-.065**2
    disc=bb*bb-4*aa*cc
    distances=np.where(disc>=0,(-bb-np.sqrt(np.maximum(0,disc)))/(2*aa),far)
    zp=np.array([-2*far*near/(far-near),-(far+near)/(far-near)])
    raw=((zp[0]/distances-zp[1]+1)*.5).astype(np.float32);raw[disc<0]=1
    cv_w2c=np.diag([1,-1,1,1])@w2c
    image=np.zeros((pixels,pixels,3),np.uint8);image[:]=[160,110,70]
    image[::8,:,0]=180  # deterministic texture, so blank-image rejection is exercised
    K=np.array([[pixels*.7,0,pixels/2],[0,pixels*.7,pixels/2],[0,0,1.]])
    meta={'intrinsics':[pixels*.7,pixels*.7,pixels/2,pixels/2], 'depth_size':[pixels,pixels],
          'depth_world_to_clip':M.ravel().tolist(),'depth_z_params':zp.tolist(),'rgb_world_to_camera':cv_w2c.ravel().tolist()}
    return image,K,meta,raw


class PipelineTests(unittest.TestCase):
    def test_metric_depth_sphere_and_textured_export(self):
        fusion=DepthFusion((.2,.2,.2),48,[0,1,1])
        for a in np.linspace(0,2*np.pi,12,endpoint=False):
            image,K,meta,depth=sphere_frame(a)
            fusion.integrate_depth(image,K,meta,depth)
        m=fusion.mesh()
        # Metric sphere diameter should be within two voxels per side.
        np.testing.assert_allclose(m.extents,[.13,.13,.13],atol=.015)
        self.assertGreater(len(fusion.bins),8)
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as folder:
            report=fusion.export(folder)
            self.assertIn('TSDF',report['method'])
            loaded=trimesh.load(Path(folder)/'model.glb',force='mesh')
            self.assertGreater(len(loaded.faces),100)
            self.assertEqual(loaded.visual.kind,'texture')
            self.assertTrue((Path(folder)/'model.obj').exists())
            self.assertTrue(list(Path(folder).glob('*.png')))

    def test_real_aruco_detection_and_metric_pose(self):
        image=np.full((720,960,3),255,np.uint8)
        K=np.array([[1000.,0,480],[0,1000,360],[0,0,1.]])
        R=np.diag([1.,-1.,-1.]);r=cv2.Rodrigues(R)[0];t=np.array([0.,0.,.65])
        from pc.reconstruction import DICT
        for i in range(4):
            corners=cv2.projectPoints(marker_corners(i),r,t,K,None)[0].reshape(4,2).astype(np.float32)
            marker=cv2.aruco.generateImageMarker(DICT,i,140)
            H=cv2.getPerspectiveTransform(np.float32([[0,0],[139,0],[139,139],[0,139]]),corners)
            warp=cv2.warpPerspective(marker,H,(960,720),borderValue=255)
            image=np.minimum(image,cv2.cvtColor(warp,cv2.COLOR_GRAY2BGR))
        actual_R,actual_t,error,n=board_pose(image,K)
        self.assertEqual(n,4)
        np.testing.assert_allclose(actual_t,t,atol=.005)
        self.assertLess(error,1.5)

    def test_durable_idempotent_receive_and_worker(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as root:
            service=Service(root,'TEST')
            state=service.create({'method':'depth-tsdf','origin':[0,1,1],'size_mm':[200]*3,'resolution':40})
            sid=state['id']
            for i,a in enumerate(np.linspace(0,2*np.pi,6,endpoint=False)):
                image,K,meta,depth=sphere_frame(a)
                jpeg=cv2.imencode('.jpg',image)[1].tobytes()
                receipt=service.enqueue(sid,str(i),jpeg,meta,depth)
                self.assertTrue(receipt['stored'])
                self.assertTrue(service.enqueue(sid,str(i),jpeg,meta,depth)['duplicate'])
                service.jobs.join()
            self.assertEqual(service.get(sid)['state']['accepted'],6)
            # Simulate restarting PC service with an unfinished saved scan.
            recovered=Service(root,'TEST')
            deadline=time.monotonic()+15
            while recovered.get(sid)['state'].get('restoring') and time.monotonic()<deadline:
                time.sleep(.05)
            self.assertFalse(recovered.get(sid)['state']['restoring'])
            self.assertEqual(recovered.get(sid)['state']['accepted'],6)
            recovered.finish(sid);recovered.jobs.join()
            self.assertEqual(recovered.get(sid)['state']['status'],'complete')
            self.assertTrue((Path(root)/'exports'/sid/'model.glb').is_file())
            saved=json.loads((Path(root)/'captures'/sid/'session.json').read_text())
            self.assertEqual(saved['received'],6)


if __name__=='__main__':unittest.main()
