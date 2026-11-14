using Meta.XR.EnvironmentDepth;
using UnityEngine;
using UnityEngine.Rendering;

public class WorldDepthPoints : MonoBehaviour
{
    private EnvironmentDepthManager depth;
    private MeshRenderer rendererComponent;
    private Mesh pointMesh;
    private Material material,copyMaterial;
    private RenderTexture sampledDepth;
    private Vector3[] positions;
    private bool held,reading;
    private float nextSample,nextDiagnostic;
    private int frame,generation;
    private readonly StableWorldPointMap map=new StableWorldPointMap();
    public const int SampleBudget=64*64;
    public void Initialize(Shader shader,Shader copyShader,EnvironmentDepthManager manager,Transform eye)
    {
        depth=manager;
        var filter=gameObject.AddComponent<MeshFilter>();rendererComponent=gameObject.AddComponent<MeshRenderer>();
        material=new Material(shader);material.SetFloat("_ZWrite",0);material.renderQueue=3001;
        copyMaterial=new Material(copyShader);sampledDepth=new RenderTexture(64,64,0,RenderTextureFormat.RFloat,RenderTextureReadWrite.Linear);sampledDepth.Create();
        rendererComponent.sharedMaterial=material;rendererComponent.enabled=false;
        positions=new Vector3[StableWorldPointMap.Capacity*6];var colors=new Color32[positions.Length];
        for(int i=0;i<colors.Length;i++)colors[i]=new Color32(135,135,255,255);
        int[] octa={0,2,4,2,1,4,1,3,4,3,0,4,2,0,5,1,2,5,3,1,5,0,3,5};
        var indices=new int[StableWorldPointMap.Capacity*24];
        for(int i=0;i<StableWorldPointMap.Capacity;i++)for(int j=0;j<24;j++)indices[i*24+j]=i*6+octa[j];
        pointMesh=new Mesh{indexFormat=IndexFormat.UInt32};pointMesh.MarkDynamic();pointMesh.vertices=positions;pointMesh.colors32=colors;pointMesh.triangles=indices;pointMesh.SetSubMesh(0,new SubMeshDescriptor(0,0),MeshUpdateFlags.DontRecalculateBounds);filter.sharedMesh=pointMesh;
        Application.onBeforeRender+=Refresh;
    }
    public void Clear()
    {
        generation++;map.Clear();if(pointMesh)pointMesh.SetSubMesh(0,new SubMeshDescriptor(0,0),MeshUpdateFlags.DontRecalculateBounds);nextSample=0;
        Debug.Log("QUEST3SPACE_WORLD_MAP cleared");
    }
    public void SetHeld(bool value)
    {
        if(held!=value){held=value;if(!held)Clear();Debug.Log("QUEST3SPACE_WORLD_POINTS held="+held+" temporary map until LT release; minimum spacing 25mm; radius 2.5mm; 3-frame confirmation");}
        if(rendererComponent)rendererComponent.enabled=held&&depth&&depth.IsDepthAvailable;
    }
    private void Refresh()
    {
        if(!rendererComponent)return;
        rendererComponent.enabled=held&&depth&&depth.IsDepthAvailable;
        if(!rendererComponent.enabled||reading||Time.unscaledTime<nextSample)return;
        var matrices=Shader.GetGlobalMatrixArray("_EnvironmentDepthReprojectionMatrices");
        if(matrices==null||matrices.Length==0||Mathf.Abs(matrices[0].determinant)<1e-10f)return;
        var inverse=matrices[0].inverse;var z=Shader.GetGlobalVector("_EnvironmentDepthZBufferParams");int requestGeneration=generation;
        reading=true;nextSample=Time.unscaledTime+.2f;
        Graphics.Blit(null,sampledDepth,copyMaterial);
        AsyncGPUReadback.Request(sampledDepth,0,TextureFormat.RFloat,request=>{
            try{
                if(!this||!held||request.hasError||requestGeneration!=generation)return;
                var pixels=request.GetData<float>();int valid=0,before=map.Points.Count;float now=Time.unscaledTime;
                frame++;map.BeginFrame(now);
                for(int i=0;i<SampleBudget;i++){
                    float raw=pixels[i],distance=z.x/(raw*2-1+z.y);
                    if(raw<=0||raw>=.999999f||float.IsNaN(distance)||distance<.2f||distance>5)continue;
                    var clip=new Vector4(((i%64+.5f)/64f)*2-1,((i/64+.5f)/64f)*2-1,raw*2-1,1);var world=inverse*clip;
                    if(Mathf.Abs(world.w)<=1e-6f)continue;
                    var point=new Vector3(world.x,world.y,world.z)/world.w;
                    if(float.IsNaN(point.x)||float.IsNaN(point.y)||float.IsNaN(point.z)||float.IsInfinity(point.sqrMagnitude))continue;
                    valid++;map.Observe(point,frame,now);
                }
                if(map.Points.Count!=before)Rebuild();
                if(now>=nextDiagnostic){nextDiagnostic=now+2;Debug.Log($"QUEST3SPACE_WORLD_DEPTH valid={valid} stable={map.Points.Count} pending={map.PendingCount} added={map.Points.Count-before} capacity={StableWorldPointMap.Capacity} spacingMeters={StableWorldPointMap.MinimumSpacing}");}
            }catch(System.Exception e){Debug.LogWarning(e);}finally{reading=false;}
        });
    }
    private void Rebuild()
    {
        const float radius=.0025f;
        Vector3 min=Vector3.positiveInfinity,max=Vector3.negativeInfinity;
        // Previously accepted centers remain bit-for-bit unchanged.
        for(int i=0;i<map.Points.Count;i++){
            var p=map.Points[i];int n=i*6;
            positions[n]=p+Vector3.right*radius;positions[n+1]=p-Vector3.right*radius;
            positions[n+2]=p+Vector3.up*radius;positions[n+3]=p-Vector3.up*radius;
            positions[n+4]=p+Vector3.forward*radius;positions[n+5]=p-Vector3.forward*radius;
            min=Vector3.Min(min,p);max=Vector3.Max(max,p);
        }
        pointMesh.vertices=positions;pointMesh.SetSubMesh(0,new SubMeshDescriptor(0,map.Points.Count*24),MeshUpdateFlags.DontRecalculateBounds);
        if(map.Points.Count>0)pointMesh.bounds=new Bounds((min+max)*.5f,max-min+Vector3.one*.01f);
    }
    private void OnApplicationPause(bool paused){if(paused){SetHeld(false);Clear();if(rendererComponent)rendererComponent.enabled=false;}}
    private void OnDestroy(){Application.onBeforeRender-=Refresh;if(pointMesh)Destroy(pointMesh);if(material)Destroy(material);if(copyMaterial)Destroy(copyMaterial);if(sampledDepth)sampledDepth.Release();}
}

