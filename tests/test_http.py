import base64
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import cv2
import numpy as np
import uvicorn
from pc.server import create_app
from test_pipeline import TEST_TEMP,sphere_frame


class HttpTests(unittest.TestCase):
    def test_authenticated_rgbd_protocol_through_export(self):
        with tempfile.TemporaryDirectory(dir=TEST_TEMP) as root:
            app=create_app(root,'TESTTOKEN')
            listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
            thread=threading.Thread(target=lambda:server.run(sockets=[listener]),daemon=True);thread.start()
            for _ in range(100):
                if server.started:break
                time.sleep(.05)
            self.assertTrue(server.started)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def call(path,body=None,auth=True,method=None):
                req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=body,method=method,
                    headers={'X-Scan-Token':'TESTTOKEN' if auth else '','Content-Type':'application/octet-stream'})
                return opener.open(req,timeout=15)
            try:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    call('/api/sessions',auth=False)
                self.assertEqual(error.exception.code,401)
                with call('/api/sessions',json.dumps({'method':'depth-tsdf','origin':[0,1,1],'size_mm':[200]*3,'resolution':40}).encode()) as r:
                    sid=json.load(r)['id']
                for i,a in enumerate(np.linspace(0,2*np.pi,6,endpoint=False)):
                    image,K,meta,depth=sphere_frame(a)
                    meta_bytes=json.dumps(meta).encode();jpeg=cv2.imencode('.jpg',image)[1].tobytes();raw=depth.astype('<f4').tobytes()
                    packet=struct.pack('<III',len(meta_bytes),len(jpeg),len(raw))+meta_bytes+jpeg+raw
                    path=f'/api/sessions/{sid}/rgbd/{i}'
                    with call(path,packet) as r:self.assertTrue(json.load(r)['stored'])
                    with call(path,packet) as r:self.assertTrue(json.load(r)['duplicate'])
                    app.state.service.jobs.join()
                with self.assertRaises(urllib.error.HTTPError) as error:
                    call(f'/api/sessions/{sid}/rgbd/invalid',b'bad')
                self.assertEqual(error.exception.code,400)
                with call(f'/api/sessions/{sid}/finish',b'') as r:self.assertEqual(json.load(r)['status'],'finishing')
                app.state.service.jobs.join()
                with call(f'/api/sessions/{sid}') as r:
                    state=json.load(r);self.assertEqual(state['status'],'complete');self.assertEqual(state['accepted'],6)
                with call(f'/api/exports/{sid}/model.glb') as r:self.assertEqual(r.read(4),b'glTF')
            finally:
                server.should_exit=True;thread.join(timeout=10);listener.close()


if __name__=='__main__':unittest.main()
