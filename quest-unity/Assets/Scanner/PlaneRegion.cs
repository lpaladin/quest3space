using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEngine;

// Planes are immutable after placement. Sides are resolved only for the cell containing Seed.
public class PlaneRegion : MonoBehaviour
{
    public bool Active {get;private set;}
    public bool Closed {get;private set;}
    public Vector3 Seed {get;private set;}
    public Vector3 Center {get;private set;}
    public Vector3 Extent {get;private set;}
    public string Hint {get;private set;}="";
    public int Count=>planes.Count;
    private readonly List<Plane> planes=new List<Plane>();
    private readonly List<LineRenderer> drawings=new List<LineRenderer>();
    private List<Vector3> corners=new List<Vector3>();
    private Transform hand;
    private Material material;
    private LineRenderer ghost;
    private LineRenderer reference;
    private int selected=-1;
    public void Initialize(Transform controller,Shader shader){hand=controller;material=new Material(shader);material.SetFloat("_ZWrite",0);material.renderQueue=3000;ghost=NewLine("Next plane");ghost.enabled=false;reference=NewLine("Region reference point");reference.enabled=false;}
    private LineRenderer NewLine(string name){var go=new GameObject(name);go.transform.SetParent(transform,false);var line=go.AddComponent<LineRenderer>();line.material=material;line.useWorldSpace=true;line.startWidth=line.endWidth=.002f;return line;}
    public void StartRegion(){Clear();Active=true;Seed=hand.position+hand.forward*.25f;Center=Seed;Extent=Vector3.one*.3f;reference.enabled=true;reference.startColor=reference.endColor=Color.magenta;Vector3 x=Vector3.right*.015f,y=Vector3.up*.015f,z=Vector3.forward*.015f;Vector3[] marker={Seed+x,Seed-x,Seed,Seed+y,Seed-y,Seed,Seed+z,Seed-z};reference.positionCount=marker.Length;reference.SetPositions(marker);Hint="Enclose the magenta start point. RGrip release / A: place; B: delete.";}
    public void Clear(){foreach(var d in drawings)Destroy(d.gameObject);drawings.Clear();planes.Clear();corners.Clear();Active=Closed=false;if(ghost)ghost.enabled=false;if(reference)reference.enabled=false;selected=-1;}
    public void Preview(bool visible){if(!ghost)return;ghost.enabled=Active&&!Closed&&visible;if(ghost.enabled)DrawPatch(ghost,hand.position+hand.forward*.25f,hand.forward,Color.yellow);}
    public bool Place(){if(!Active||Closed)return false;if(planes.Count>=16){Hint="Maximum 16 planes; delete a plane first.";return false;}
        var plane=new Plane(hand.forward,hand.position+hand.forward*.25f);
        if(Mathf.Abs(plane.GetDistanceToPoint(Seed))<.003f){Hint="Plane is too close to the start point; move the hand farther out.";return false;}
        planes.Add(plane);drawings.Add(NewLine("Fixed plane "+planes.Count));Resolve();return Closed;}
    public void Delete(){if(selected<0||selected>=planes.Count){Hint="Point at a plane patch, then delete.";return;}Destroy(drawings[selected].gameObject);drawings.RemoveAt(selected);planes.RemoveAt(selected);selected=-1;Resolve();}
    public void Point(Ray ray){int previous=selected;selected=-1;float distance=3;
        for(int i=0;i<planes.Count;i++){if(!planes[i].Raycast(ray,out float t)||t<0||t>=distance)continue;
            Vector3 center=-planes[i].distance*planes[i].normal;Vector3 target=ray.GetPoint(t);
            // Select the rendered patch, not an invisible infinite continuation.
            if(Closed){if(!Contains(target,.01f))continue;}else if((target-centers[i]).sqrMagnitude>.35f*.35f)continue;
            distance=t;selected=i;}
        // Keep the highlighted selection when the ray moves to the Delete button.
        if(selected<0)selected=previous;
        if(previous!=selected)Paint();}
    private readonly List<Vector3> centers=new List<Vector3>();
    private Plane Inward(Plane plane)=>plane.GetDistanceToPoint(Seed)>=0?plane:new Plane(-plane.normal,-plane.distance);
    public bool Contains(Vector3 point,float tolerance=0){return planes.All(p=>Inward(p).GetDistanceToPoint(point)>=-tolerance);}
    public JArray Json(){return new JArray(planes.Select(p=>{var q=Inward(p);return new JArray(q.normal.x,q.normal.y,q.normal.z,q.distance);}));}
    public void Restore(JArray data,JArray seed){Clear();Active=true;Seed=new Vector3((float)seed[0],(float)seed[1],(float)seed[2]);foreach(JArray a in data){planes.Add(new Plane(new Vector3((float)a[0],(float)a[1],(float)a[2]),(float)a[3]));drawings.Add(NewLine("Fixed plane"));}Resolve();}
    private void Resolve(){
        Closed=false;corners.Clear();centers.Clear();foreach(var p in planes)centers.Add(Seed-p.normal*p.GetDistanceToPoint(Seed));
        var inward=planes.Select(Inward).ToArray();
        if(planes.Count>=4){
            var directions=new List<Vector3>{Vector3.right,Vector3.up,Vector3.forward};
            for(int i=0;i<inward.Length;i++){directions.Add(Vector3.Cross(inward[i].normal,Vector3.right));directions.Add(Vector3.Cross(inward[i].normal,Vector3.up));directions.Add(Vector3.Cross(inward[i].normal,Vector3.forward));for(int j=i+1;j<inward.Length;j++)directions.Add(Vector3.Cross(inward[i].normal,inward[j].normal));}
            bool unbounded=directions.Where(d=>d.sqrMagnitude>1e-10f).Any(d=>inward.All(p=>Vector3.Dot(p.normal,d.normalized)>=-1e-5f)||inward.All(p=>Vector3.Dot(p.normal,-d.normalized)>=-1e-5f));
            if(!unbounded){
                for(int i=0;i<inward.Length;i++)for(int j=i+1;j<inward.Length;j++)for(int k=j+1;k<inward.Length;k++){
                    var a=inward[i];var b=inward[j];var c=inward[k];float det=Vector3.Dot(a.normal,Vector3.Cross(b.normal,c.normal));if(Mathf.Abs(det)<1e-6f)continue;
                    var v=(-a.distance*Vector3.Cross(b.normal,c.normal)-b.distance*Vector3.Cross(c.normal,a.normal)-c.distance*Vector3.Cross(a.normal,b.normal))/det;
                    if(Contains(v,.0001f)&&!corners.Any(p=>(p-v).sqrMagnitude<1e-8f))corners.Add(v);
                }
                if(corners.Count>=4){var min=corners.Aggregate(Vector3.Min);var max=corners.Aggregate(Vector3.Max);Extent=max-min;Center=(min+max)*.5f;
                    Closed=Extent.x>=.03f&&Extent.y>=.03f&&Extent.z>=.03f&&Extent.x<=4&&Extent.y<=4&&Extent.z<=4;}
            }
        }
        if(reference)reference.enabled=Active&&!Closed;
        Hint=Closed?"Closed. Inward sides resolved; starting scan.":"Open / invalid extent. Enclose magenta point; 30 mm–4 m per axis.";Paint();
    }
    private void Paint(){for(int i=0;i<planes.Count;i++){
        Color color=i==selected?new Color(1,.3f,.15f):Closed?new Color(.2f,1,.5f):Color.cyan;
        if(!Closed){DrawPatch(drawings[i],centers[i],planes[i].normal,color);continue;}
        var face=corners.Where(v=>Mathf.Abs(planes[i].GetDistanceToPoint(v))<.0003f).ToList();if(face.Count<3){drawings[i].positionCount=0;continue;}
        var center=face.Aggregate(Vector3.zero,(sum,v)=>sum+v)/face.Count;Basis(planes[i].normal,out var x,out var y);
        face=face.OrderBy(v=>Mathf.Atan2(Vector3.Dot(v-center,y),Vector3.Dot(v-center,x))).ToList();face.Add(face[0]);
        // Only closed faces get a short inward-normal arrow.
        face.Add(center);face.Add(center+Inward(planes[i]).normal*.06f);
        drawings[i].positionCount=face.Count;drawings[i].SetPositions(face.ToArray());drawings[i].startColor=drawings[i].endColor=color;
    }}
    private static void Basis(Vector3 n,out Vector3 x,out Vector3 y){x=Vector3.Cross(n,Mathf.Abs(n.y)<.9f?Vector3.up:Vector3.right).normalized;y=Vector3.Cross(n,x).normalized;}
    private static void DrawPatch(LineRenderer line,Vector3 center,Vector3 normal,Color color){Basis(normal,out var x,out var y);x*=.25f;y*=.25f;Vector3[] points={center-x-y,center+x-y,center+x+y,center-x+y,center-x-y,center+x+y,center,center-x+y,center+x-y};line.positionCount=points.Length;line.SetPositions(points);line.startColor=line.endColor=color;}
}
