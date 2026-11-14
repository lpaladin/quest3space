import unittest,tempfile
from pathlib import Path
import numpy as np
from fastapi.testclient import TestClient
from pc.plane_region import resolve_region,inside_planes
from pc.reconstruction import DepthFusion
from pc.server import create_app
from test_pipeline import TEST_TEMP

class PlaneRegionTests(unittest.TestCase):
    def walls(self):
        return np.array([[1,0,0,1],[-1,0,0,1],[0,1,0,1],[0,-1,0,1],[0,0,1,1],[0,0,-1,1]],float)
    def test_double_sided_planes_close_and_deletion_reopens(self):
        raw=self.walls();raw[[0,2,5]]*=-1
        planes,center,size,vertices=resolve_region(raw,[0,0,0])
        np.testing.assert_allclose(center,0);np.testing.assert_allclose(size,[2,2,2]);self.assertEqual(len(vertices),8)
        self.assertTrue(inside_planes([[0,0,0],[.9,.9,.9]],planes).all())
        self.assertFalse(inside_planes([[1.1,0,0]],planes)[0])
        with self.assertRaises(ValueError):resolve_region(raw[:-1],[0,0,0])
        with self.assertRaises(ValueError):resolve_region(raw,[1,0,0])
    def test_slanted_plane_masks_voxels_and_http_derives_actual_bounds(self):
        planes=np.vstack([self.walls(),[1,1,1,.1]])
        oriented,center,size,_=resolve_region(planes,[0,0,0])
        fusion=DepthFusion(size,32,center,planes=oriented)
        self.assertTrue(fusion.region_mask.any());self.assertTrue((~fusion.region_mask).any())
        self.assertTrue((fusion.world[fusion.region_mask].sum(1)>=-.100001).all())
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as root:
            app=create_app(root,'TEST');client=TestClient(app)
            response=client.post('/api/sessions',headers={'x-scan-token':'TEST'},json={'method':'depth-tsdf','planes':planes.tolist(),'region_seed':[0,0,0],'resolution':32})
            self.assertEqual(response.status_code,200,response.text)
            state=response.json();self.assertEqual(state['size_mm'],[2000]*3)
            self.assertEqual(len(state['planes']),7)
            self.assertTrue((Path(root)/'captures'/state['id']/'session.json').is_file())

if __name__=='__main__':unittest.main()
