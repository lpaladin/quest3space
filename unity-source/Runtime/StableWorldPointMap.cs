using System.Collections.Generic;
using UnityEngine;

// Persistent tracking-space surfels. Confirmed positions never follow a new depth frame.
public sealed class StableWorldPointMap
{
    public const float CellSize=.035f, MinimumSpacing=.025f;
    public const int Capacity=12000;
    private struct Candidate { public Vector3 mean; public int hits,frame; public float last; }
    private readonly Dictionary<Vector3Int,Candidate> pending=new Dictionary<Vector3Int,Candidate>();
    private readonly Dictionary<Vector3Int,Vector3> locked=new Dictionary<Vector3Int,Vector3>();
    private readonly List<Vector3Int> expired=new List<Vector3Int>();
    public readonly List<Vector3> Points=new List<Vector3>(Capacity);
    public int PendingCount=>pending.Count;
    private float nextPrune;
    private static Vector3Int Key(Vector3 p)=>new Vector3Int(Mathf.FloorToInt(p.x/CellSize),Mathf.FloorToInt(p.y/CellSize),Mathf.FloorToInt(p.z/CellSize));
    public void Clear(){pending.Clear();locked.Clear();Points.Clear();nextPrune=0;}
    public void BeginFrame(float now)
    {
        if(now<nextPrune)return;
        nextPrune=now+1;expired.Clear();
        foreach(var item in pending)if(now-item.Value.last>1)expired.Add(item.Key);
        foreach(var key in expired)pending.Remove(key);
    }
    public bool Observe(Vector3 point,int frame,float now)
    {
        if(Points.Count>=Capacity)return false;
        var key=Key(point);if(locked.ContainsKey(key))return false;
        if(!pending.TryGetValue(key,out var c)){
            if(pending.Count<Capacity*2)pending.Add(key,new Candidate{mean=point,hits=1,frame=frame,last=now});
            return false;
        }
        if(c.frame==frame)return false; // Pixels from one image are not independent confirmations.
        if(now-c.last>1||(point-c.mean).sqrMagnitude>.012f*.012f)c=new Candidate{mean=point,hits=1,frame=frame,last=now};
        else {c.mean=(c.mean*c.hits+point)/(c.hits+1);c.hits++;c.frame=frame;c.last=now;}
        pending[key]=c;if(c.hits<3)return false;
        // Cross-cell spacing check avoids dense pairs at voxel boundaries.
        for(int x=-1;x<=1;x++)for(int y=-1;y<=1;y++)for(int z=-1;z<=1;z++)
            if(locked.TryGetValue(key+new Vector3Int(x,y,z),out var other)&&(other-c.mean).sqrMagnitude<MinimumSpacing*MinimumSpacing){pending.Remove(key);return false;}
        locked.Add(key,c.mean);Points.Add(c.mean);pending.Remove(key);return true;
    }
}
