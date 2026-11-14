"""Metric, marker-registered visual-hull reconstruction. No CUDA dependency.

This intentionally reconstructs silhouettes: hidden concavities are not measured.
Board coordinates: metres, +Z above paper, +Y toward top of printed sheet.
"""
from pathlib import Path
import io
import cv2
import numpy as np
from PIL import Image
from scipy.ndimage import binary_fill_holes, label
from skimage.measure import marching_cubes
import trimesh

DICT = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
MARKER_SIZE = .035
CENTERS = [(-.075, .115), (.075, .115), (.075, -.115), (-.075, -.115)]


def marker_corners(index):
    x, y = CENTERS[index]
    h = MARKER_SIZE / 2
    return np.float32([[x-h,y+h,0],[x+h,y+h,0],[x+h,y-h,0],[x-h,y-h,0]])


def board_svg(path):
    # A4 physical units. Each ArUco has a surrounding white quiet zone.
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="210mm" height="297mm" viewBox="0 0 210 297">',
             '<rect width="210" height="297" fill="white"/>']
    for index, (x, y) in enumerate(CENTERS):
        marker = cv2.aruco.generateImageMarker(DICT, index, 7)
        left, top = 105 + x*1000-17.5, 148.5-y*1000-17.5
        for row in range(7):
            for col in range(7):
                if marker[row,col] == 0:
                    parts.append(f'<rect x="{left+col*5}" y="{top+row*5}" width="5" height="5" fill="black"/>')
    parts += ['<rect x="40" y="68.5" width="130" height="160" fill="none" stroke="#ddd" stroke-dasharray="2 2"/>',
              '<text x="105" y="146" font-family="sans-serif" font-size="4" text-anchor="middle" fill="#bbb">Place object here</text>',
              '<path d="M55 278h100m-100-2v4m100-4v4" stroke="black" fill="none"/>',
              '<text x="105" y="286" text-anchor="middle" font-family="sans-serif" font-size="3.5">100 mm — print A4 at 100%, no fit-to-page</text>', '</svg>']
    Path(path).write_text('\n'.join(parts), encoding='utf-8')


def camera_matrix(meta, width, height):
    values = np.asarray(meta.get('intrinsics', []), dtype=float)
    if values.size != 4 or not np.isfinite(values).all() or np.any(values[:2] <= 0):
        raise ValueError('Missing camera calibration: expected intrinsics [fx, fy, cx, cy] in image pixels')
    fx,fy,cx,cy = values
    if not (0 <= cx <= width and 0 <= cy <= height):
        raise ValueError('Camera principal point is outside image')
    return np.float64([[fx,0,cx],[0,fy,cy],[0,0,1]])


def board_pose(image, K):
    detector = cv2.aruco.ArucoDetector(DICT, cv2.aruco.DetectorParameters())
    corners, ids, _ = detector.detectMarkers(image)
    pairs = [(int(i), c.reshape(4,2)) for c,i in zip(corners, [] if ids is None else ids.flatten()) if 0 <= int(i) < 4]
    if len(pairs) < 2:
        raise ValueError('Keep at least two printed markers visible; move back or look down slightly')
    obj = np.concatenate([marker_corners(i) for i,c in pairs])
    pix = np.concatenate([c for i,c in pairs])
    ok, r, t = cv2.solvePnP(obj, pix, K, None, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        raise ValueError('Board pose failed')
    R = cv2.Rodrigues(r)[0]
    camera_center = -R.T @ t.reshape(3)
    projected = cv2.projectPoints(obj,r,t,K,None)[0].reshape(-1,2)
    error = float(np.sqrt(np.mean(np.sum((projected-pix)**2,axis=1))))
    if error > 3.0 or camera_center[2] < .06:
        raise ValueError(f'Board pose unreliable (error {error:.1f}px); check print size and camera calibration')
    return R, t.reshape(3), error, len(pairs)


def project(points, R, t, K):
    cam = points @ R.T + t
    uv = cam @ K.T
    return uv[:,:2] / np.maximum(uv[:,2:3], 1e-8), cam[:,2]


class Hull:
    def __init__(self, size=(.13,.16,.20), resolution=80):
        self.size = np.asarray(size, dtype=np.float32)
        self.resolution = int(resolution)
        self.lower = np.float32([-self.size[0]/2,-self.size[1]/2,.003])
        self.step = self.size / self.resolution
        grid = np.indices((self.resolution,)*3).reshape(3,-1).T
        self.points = (self.lower + (grid+.5)*self.step).astype(np.float32)
        self.occupied = np.ones(len(self.points), bool)
        self.views = []
        self.bins = set()

    def segment(self, image, R, t, K):
        h,w = image.shape[:2]
        box = np.array([[x,y,z] for x in [-self.size[0]/2,self.size[0]/2]
                         for y in [-self.size[1]/2,self.size[1]/2] for z in [.003,self.size[2]]],np.float32)
        uv, depth = project(box,R,t,K)
        if np.any(depth <= .02):
            raise ValueError('Too close to scan volume')
        polygon = cv2.convexHull(np.int32(np.round(uv)))
        region = np.zeros((h,w),np.uint8)
        cv2.fillConvexPoly(region,polygon,1)
        # A completely clipped ROI cannot provide a complete silhouette.
        if np.any(uv[:,0]<3) or np.any(uv[:,0]>w-4) or np.any(uv[:,1]<3) or np.any(uv[:,1]>h-4):
            raise ValueError('Move back: the entire scan volume must fit in the image')
        hsv = cv2.cvtColor(image,cv2.COLOR_BGR2HSV)
        nonwhite = ((hsv[:,:,1] > 35) | (hsv[:,:,2] < 155)).astype(np.uint8)
        seed = nonwhite & region
        # Exclude the board footprint boundary to suppress printed markers/table.
        count, labels, stats, _ = cv2.connectedComponentsWithStats(seed)
        if count < 2:
            raise ValueError('No object silhouette found; use a coloured/matte object on the white board')
        components = [(stats[i,cv2.CC_STAT_AREA],i) for i in range(1,count)]
        biggest = max(components)[1]
        if stats[biggest,cv2.CC_STAT_AREA] < 100:
            raise ValueError('Object too small or too similar to background')
        seed = (labels == biggest).astype(np.uint8)
        # GrabCut refines the colour seed, bounded by the projected scan volume.
        gc = np.where(region,cv2.GC_PR_BGD,cv2.GC_BGD).astype(np.uint8)
        gc[seed>0] = cv2.GC_PR_FGD
        inner = cv2.erode(seed,np.ones((3,3),np.uint8))
        gc[inner>0] = cv2.GC_FGD
        if inner.any():
            try:
                cv2.grabCut(image,gc,None,np.zeros((1,65),np.float64),np.zeros((1,65),np.float64),2,cv2.GC_INIT_WITH_MASK)
                seed = np.isin(gc,[cv2.GC_FGD,cv2.GC_PR_FGD]).astype(np.uint8)
            except cv2.error:
                pass
        seed = cv2.morphologyEx(seed,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
        seed = binary_fill_holes(seed).astype(np.uint8)
        fraction = float(seed.sum()/max(region.sum(),1))
        if fraction < .005 or fraction > .85:
            raise ValueError('Silhouette unreliable; check object/background contrast and volume size')
        return seed

    def integrate(self, image, K, R, t, mask=None):
        mask = self.segment(image,R,t,K) if mask is None else mask.astype(np.uint8)
        # Conservative 2-pixel padding limits damage from segmentation jitter.
        sample_mask = cv2.dilate(mask,np.ones((5,5),np.uint8))
        uv, depth = project(self.points,R,t,K)
        px = np.rint(uv).astype(np.int32)
        h,w = mask.shape
        valid = (depth>0)&(px[:,0]>=0)&(px[:,0]<w)&(px[:,1]>=0)&(px[:,1]<h)
        inside = np.zeros(len(self.points),bool)
        inside[valid] = sample_mask[px[valid,1],px[valid,0]]>0
        proposed = self.occupied & inside
        if proposed.sum() < 30:
            raise ValueError('Frame would erase the model; rejected. Check mask/board registration')
        self.occupied = proposed
        center = -R.T @ t
        angle = float(np.arctan2(center[1],center[0]))
        self.bins.add(int((angle+np.pi)/(2*np.pi)*12)%12)
        # Keep enough views for texture baking while bounding memory.
        if len(self.views)<72:
            self.views.append((image.copy(),K.copy(),R.copy(),t.copy()))
        return mask

    def mesh(self, textured=False):
        field = self.occupied.reshape((self.resolution,)*3)
        labels, n = label(field)
        if n:
            sizes = np.bincount(labels.ravel()); sizes[0]=0
            field = labels == np.argmax(sizes)
        padded = np.pad(field.astype(np.float32),1)
        if not field.any():
            raise ValueError('No reconstructed surface')
        vertices,faces,_,_ = marching_cubes(padded,.5,spacing=self.step)
        vertices += self.lower - self.step*.5
        mesh = trimesh.Trimesh(vertices,faces,process=False)
        self.colour(mesh,textured)
        return mesh

    def colour(self, mesh, textured):
        if not self.views:
            mesh.visual.vertex_colors = [67,205,184,255]
            return
        centers = mesh.triangles_center
        normals = mesh.face_normals
        best = np.full(len(centers),-np.inf)
        indices = np.zeros(len(centers),np.int32)
        for i,(image,K,R,t) in enumerate(self.views):
            C = -R.T @ t
            ray = C-centers
            score = np.sum(normals*ray,axis=1)/np.maximum(np.linalg.norm(ray,axis=1),1e-9)
            uv,z = project(centers,R,t,K)
            score[(z<=0)|(uv[:,0]<0)|(uv[:,0]>=image.shape[1])|(uv[:,1]<0)|(uv[:,1]>=image.shape[0])] = -np.inf
            update = score>best; best[update]=score[update]; indices[update]=i
        if not textured:
            colors = np.zeros((len(centers),4),np.uint8); colors[:,3]=255
            for i,(image,K,R,t) in enumerate(self.views):
                ids = np.where(indices==i)[0]
                uv,_ = project(centers[ids],R,t,K)
                x=np.clip(np.rint(uv[:,0]).astype(int),0,image.shape[1]-1)
                y=np.clip(np.rint(uv[:,1]).astype(int),0,image.shape[0]-1)
                colors[ids,:3]=image[y,x,::-1]
            mesh.visual.face_colors=colors
            return
        # Independent triangle UV islands, sampled from their best-facing photo.
        tile = 12
        cols = int(np.ceil(np.sqrt(len(mesh.faces))))
        rows = int(np.ceil(len(mesh.faces)/cols))
        atlas = np.zeros((rows*tile,cols*tile,3),np.uint8)
        uv_out = np.zeros((len(mesh.faces)*3,2),np.float64)
        new_vertices = mesh.vertices[mesh.faces].reshape(-1,3)
        target = np.float32([[1,1],[tile-2,1],[1,tile-2]])
        for f,vi in enumerate(indices):
            image,K,R,t = self.views[vi]
            source,_ = project(new_vertices[f*3:f*3+3],R,t,K)
            transform = cv2.getAffineTransform(source.astype(np.float32),target)
            patch = cv2.warpAffine(image,transform,(tile,tile),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_REPLICATE)
            row,col = divmod(f,cols)
            atlas[row*tile:(row+1)*tile,col*tile:(col+1)*tile] = patch[:,:,::-1]
            coords = target+[col*tile,row*tile]
            uv_out[f*3:f*3+3,0] = coords[:,0]/atlas.shape[1]
            uv_out[f*3:f*3+3,1] = 1-coords[:,1]/atlas.shape[0]
        mesh.vertices = new_vertices
        mesh.faces = np.arange(len(new_vertices)).reshape(-1,3)
        mesh.visual = trimesh.visual.texture.TextureVisuals(uv=uv_out,image=Image.fromarray(atlas))

    def export(self, folder):
        folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
        mesh = self.mesh(textured=True)
        # Board Z-up to glTF Y-up; metres preserved.
        mesh.apply_transform(np.array([[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]],float))
        mesh.export(folder/'model.glb')
        obj, files = trimesh.exchange.obj.export_obj(mesh,include_texture=True,return_texture=True)
        (folder/'model.obj').write_text(obj,encoding='utf-8')
        for name,data in files.items():
            (folder/Path(name).name).write_bytes(data if isinstance(data,bytes) else data.encode())
        mesh.export(folder/'model.ply')
        return {'vertices':len(mesh.vertices),'faces':len(mesh.faces),'units':'metres','method':'marker-registered visual hull','concavities_measured':False}

    def preview(self):
        if not self.views:
            return {'vertices':[],'faces':[],'colors':[]}
        m=self.mesh()
        return {'vertices':np.round(m.vertices,5).tolist(),'faces':m.faces.tolist(),
                'colors':m.visual.face_colors[:,:3].tolist()}


class DepthFusion(Hull):
    """Projective TSDF using Meta's actual depth reprojection matrix.

    RGB and depth have independent camera poses. No assumption of pixel alignment
    or of depth/RGB timestamps being equal is made. The depth matrix supplied by
    EnvironmentDepthManager already accounts for its own capture pose.
    """
    def __init__(self,size=(.3,.3,.3),resolution=96,origin=(0,1,1),rotation=(0,0,0,1),planes=None):
        super().__init__(size,resolution)
        self.lower=-self.size/2
        grid=np.indices((self.resolution,)*3).reshape(3,-1).T
        self.points=(self.lower+(grid+.5)*self.step).astype(np.float32)
        self.origin=np.asarray(origin,np.float64)
        self.local_to_world=np.eye(4)
        from scipy.spatial.transform import Rotation
        basis=np.array([[1,0,0],[0,0,1],[0,-1,0]],float)
        world_rotation=Rotation.from_quat(rotation).as_matrix()
        self.preview_rotation=basis.T@world_rotation@basis
        self.local_to_world[:3,:3]=world_rotation@basis
        self.local_to_world[:3,3]=self.origin
        self.world=self.points@self.local_to_world[:3,:3].T+self.origin
        from pc.plane_region import inside_planes
        self.region_mask=inside_planes(self.world,planes)
        self.tsdf=np.ones(len(self.points),np.float32)
        self.weights=np.zeros(len(self.points),np.float32)

    def integrate_depth(self,image,K,meta,depth):
        M=np.asarray(meta['depth_world_to_clip'],np.float64).reshape(4,4)
        params=np.asarray(meta['depth_z_params'],np.float64)
        if not np.isfinite(M).all() or not np.isfinite(params).all() or abs(np.linalg.det(M))<1e-10:
            raise ValueError('Invalid depth projection matrix')
        clip=self.world@M[:,:3].T+M[:,3]
        ndc=clip[:,:3]/np.maximum(clip[:,3:4],1e-9)
        dh,dw=depth.shape
        uv=(ndc[:,:2]+1)*.5
        x=np.clip((uv[:,0]*dw).astype(int),0,dw-1)
        y=np.clip((uv[:,1]*dh).astype(int),0,dh-1)
        raw=depth[y,x]
        with np.errstate(divide='ignore',invalid='ignore'):
            measured=params[0]/(raw*2-1+params[1])
            predicted=params[0]/(ndc[:,2]+params[1])
        trunc=max(float(self.step.max()*4),.008)
        sdf=measured-predicted
        valid=(clip[:,3]>0)&(uv[:,0]>=0)&(uv[:,0]<1)&(uv[:,1]>=0)&(uv[:,1]<1)
        valid&=np.isfinite(measured)&np.isfinite(predicted)&(raw>0)&(raw<.999999)&(measured>.05)&(measured<5)&(sdf>=-trunc)
        valid&=self.region_mask
        surface=valid&(abs(sdf)<trunc)
        if surface.sum()<20:
            raise ValueError('No depth surface inside scan box; reposition the box around the object')
        values=np.clip(sdf[valid]/trunc,-1,1)
        weight=self.weights[valid]
        self.tsdf[valid]=(self.tsdf[valid]*weight+values)/(weight+1)
        self.weights[valid]=np.minimum(weight+1,32)
        self.occupied=(self.tsdf<0)&(self.weights>0)
        rgb_world_to_camera=np.asarray(meta['rgb_world_to_camera'],np.float64).reshape(4,4)
        transform=rgb_world_to_camera@self.local_to_world
        R,t=transform[:3,:3],transform[:3,3]
        C=-R.T@t
        self.bins.add(int((np.arctan2(C[1],C[0])+np.pi)/(2*np.pi)*12)%12)
        if len(self.views)<72:
            self.views.append((image.copy(),K.copy(),R.copy(),t.copy()))
        # Diagnostic overlay: pixels reached by measured near-surface voxels.
        pixels,z=project(self.points[surface],R,t,K)
        pixels=np.rint(pixels).astype(int);h,w=image.shape[:2]
        good=(z>0)&(pixels[:,0]>=0)&(pixels[:,0]<w)&(pixels[:,1]>=0)&(pixels[:,1]<h)
        mask=np.zeros((h,w),np.uint8)
        mask[pixels[good,1],pixels[good,0]]=1
        return cv2.dilate(mask,np.ones((3,3),np.uint8))

    def mesh(self,textured=False):
        field=self.tsdf.reshape((self.resolution,)*3)
        observed=self.weights.reshape(field.shape)>=1
        if not np.any(field[observed]<0) or not np.any(field[observed]>0):
            raise ValueError('Need more depth observations before a surface can be extracted')
        vertices,faces,_,_=marching_cubes(field,0,spacing=self.step,mask=observed)
        vertices+=self.lower+self.step*.5
        mesh=trimesh.Trimesh(vertices,faces,process=False)
        # Preserve separate measured parts; do not silently fill unseen holes.
        self.colour(mesh,textured)
        # Preview/export remain world-oriented offsets, matching the existing Quest protocol.
        transform=np.eye(4);transform[:3,:3]=self.preview_rotation
        mesh.apply_transform(transform)
        return mesh

    def export(self,folder):
        result=super().export(folder)
        result.pop('concavities_measured',None)
        result.update(method='Meta Environment Depth projective TSDF',unobserved_surfaces_filled=False,
                      warning='Only visible depth surfaces are measured. Unseen holes are left open; precision depends on Quest depth data and tracking.')
        return result
