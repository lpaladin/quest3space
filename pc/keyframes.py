"""Sharp photograph selection with explicit directional coverage preservation."""
import numpy as np

def view_bin(frame):
    E=np.asarray(frame['E'],float)
    center=-E[:3,:3].T@E[:3,3]
    radius=np.linalg.norm(center)
    if radius<1e-8:return (0,1)
    azimuth=int(np.floor((np.arctan2(center[2],center[0])+np.pi)*12/(2*np.pi)))%12
    elevation=int(np.digitize(center[1]/radius,[-.35,.35]))
    return azimuth,elevation

def select_texture_frames(frames):
    if not frames:return [],{'policy':'sharpness-with-directional-fallback','direction_bins':0}
    threshold=float(np.percentile([f['sharpness'] for f in frames],35))
    chosen={f['name'] for f in frames if f['sharpness']>=threshold}
    best={}
    for frame in frames:
        key=view_bin(frame)
        if key not in best or frame['sharpness']>best[key]['sharpness']:best[key]=frame
    fallbacks=[f['name'] for f in best.values() if f['name'] not in chosen]
    chosen.update(fallbacks)
    return [f for f in frames if f['name'] in chosen],{
        'policy':'sharpness-with-directional-fallback','sharpness_threshold':threshold,
        'direction_bins':len(best),'directional_fallbacks':fallbacks}
