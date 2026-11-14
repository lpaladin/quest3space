"""Select the bounded cell of unoriented planes containing a reference point."""
import numpy as np
from scipy.optimize import linprog
from scipy.spatial import HalfspaceIntersection

def resolve_region(planes,seed):
    p=np.asarray(planes,float);seed=np.asarray(seed,float)
    if p.ndim!=2 or p.shape[1]!=4 or not 4<=len(p)<=16 or not np.isfinite(p).all():raise ValueError('Need 4–16 finite planes')
    if seed.shape!=(3,) or not np.isfinite(seed).all():raise ValueError('Invalid region reference point')
    norms=np.linalg.norm(p[:,:3],axis=1)
    if np.any(norms<1e-8):raise ValueError('Invalid plane normal')
    p=p/norms[:,None];distance=p[:,:3]@seed+p[:,3]
    if np.any(abs(distance)<.003):raise ValueError('Reference point is too close to a plane')
    p[distance<0]*=-1
    for direction in np.vstack([np.eye(3),-np.eye(3)]):
        result=linprog(direction,A_ub=-p[:,:3],b_ub=p[:,3],bounds=[(None,None)]*3,method='highs')
        if not result.success:raise ValueError('Planes do not enclose a bounded region')
    vertices=HalfspaceIntersection(-p,seed).intersections
    lower=vertices.min(0);upper=vertices.max(0);size=upper-lower
    if np.any(size<.03) or np.any(size>4):raise ValueError('Region extent must be 30 mm–4 m per axis')
    return p,(lower+upper)/2,size,vertices

def inside_planes(points,planes,margin=0):
    if planes is None or len(planes)==0:return np.ones(len(points),bool)
    p=np.asarray(planes,float)
    return np.all(np.asarray(points)@p[:,:3].T+p[:,3]>=-margin,axis=1)
