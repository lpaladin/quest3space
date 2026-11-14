using System;
using System.Collections;
using System.Collections.Concurrent;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Meta.XR;
using Meta.XR.EnvironmentDepth;
using Meta.XR.MRUtilityKit;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;
using UnityEngine.Android;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.Networking;
using UnityEngine.Rendering;
using UnityEngine.UI;

public class QuestScanner : MonoBehaviour
{
    public OVRCameraRig Rig;
    public PassthroughCameraAccess CameraAccess;
    public EnvironmentDepthManager DepthManager;
    public Shader DepthCopyShader, RgbCopyShader, VertexColorShader, LineShader, UiDepthShader;
    private const string DefaultServer="http://192.168.31.193:8765";
    private string server=DefaultServer,token="",session="",spool="";
    private bool connected,scanning,finishing,creating,polling;
    private volatile bool capturing;
    private string message="Connect PC, then place scan box around the object.";
    private Vector3 origin=new Vector3(0,1,1);
    private Quaternion boxRotation=Quaternion.identity;
    private bool boxFollowing,boxScaling;
    private float boxScaleStartDistance,boxScaleStartSide;
    private PlaneRegion planeRegion;
    private bool planeGripHeld;
    private float side=.3f;
    private Text status,mainProgress,details;
    private Image mainProgressFill,worldProgressFill;
    private Transform settingsPanel,errorDialog;
    private Text errorBody;
    private bool ErrorVisible=>errorDialog&&errorDialog.gameObject.activeSelf;
    private bool settingsOpen;
    private Button connectButton,newButton,placeButton,planeStartButton,planePlaceButton,planeDeleteButton,startButton,pauseButton,finishButton,settingsButton,sizeButton;
    private InputField addressInput,tokenInput;
    private Transform panel;
    private LineRenderer ray,box;
    private MeshFilter model;
    private MeshRenderer modelRenderer,vertexRenderer;
    private MeshFilter vertexModel;
    private Transform progressPanel;
    private Text scanProgress;
    private int acceptedFrames,coverageDirections,displayedPoints;
    private const int RecommendedFrames=60,RecommendedDirections=10,MaxVisiblePoints=4000;
    private bool verticesHeld;
    private WorldDepthPoints worldPoints;
    private Texture2D previewTexture;
    private RenderTexture rgbRT,depthRT;
    private Material depthCopy,rgbCopy,uiDepthMaterial;
    private readonly ConcurrentQueue<string> notes=new ConcurrentQueue<string>();
    private float lastCapture,lastPoll,lastPreview;
    private string completed="";
    private int sent;
    private bool lastRgb,lastDepth;
    private bool imageVerified,boxPlaced;
    private bool overlayHeld;
    private bool requestNewSession,resetting;
    private Vector3 previewOrigin;
    private Button hoveredButton,pressedButton;
    private float pressedUntil,hapticUntil;
    private bool hapticActive;
    private static readonly Color ButtonIdle=new Color(.12f,.30f,.29f,.97f),ButtonHover=new Color(.18f,.48f,.44f,1),ButtonPressed=new Color(.32f,.76f,.61f,1);
    private float lastProbe;
    private float nextStatus;
    private int pendingDisplay,previewRevision=-1;
    private string previewId="";
    private string previewRun="",previewSource="live";
    private int perfFrames,perfSlow;
    private float perfStart,perfWorst;
    private MRUK worldLock;
    private OVRDisplay recenterDisplay;
    private float trackingSettleUntil;
    private bool previousWorldLock;
    private bool worldLockEstablished;
    private bool recenterControlsPending;
    private OVRSpatialAnchor fallbackWorldAnchor;
    private OVRControllerHelper[] controllerVisuals;
    private bool CanResizeBox=>WorldCoordinatesReady&&boxPlaced&&string.IsNullOrEmpty(session)&&string.IsNullOrEmpty(completed)&&!creating&&!finishing&&!resetting&&!planeRegion.Active;
    private bool WorldCoordinatesReady=>worldLock&&worldLock.IsWorldLockActive&&Time.unscaledTime>=trackingSettleUntil;

    void Start()
    {
        Application.targetFrameRate=72;
        DepthManager.RemoveHands=false;
        DepthManager.OcclusionShadersMode=OcclusionShadersMode.SoftOcclusion;
        spool=Path.Combine(Application.persistentDataPath,"pending");Directory.CreateDirectory(spool);
        session=PlayerPrefs.GetString("session","");
        if(!string.IsNullOrEmpty(session)){origin=new Vector3(PlayerPrefs.GetFloat("originX"),PlayerPrefs.GetFloat("originY",1),PlayerPrefs.GetFloat("originZ",1));boxRotation=new Quaternion(PlayerPrefs.GetFloat("boxQx"),PlayerPrefs.GetFloat("boxQy"),PlayerPrefs.GetFloat("boxQz"),PlayerPrefs.GetFloat("boxQw",1));side=PlayerPrefs.GetFloat("side",.3f);boxPlaced=true;}
        finishing=PlayerPrefs.GetInt("finishing",0)==1;
        server=PlayerPrefs.GetString("server",server);token=PlayerPrefs.GetString("token",token);
        string config=Path.Combine(Application.persistentDataPath,"connection.json");
        if(File.Exists(config)){try{var c=JObject.Parse(File.ReadAllText(config));server=(string)c["server"]??server;token=(string)c["token"]??token;requestNewSession=(bool?)c["new_session"]??false;if(requestNewSession){c.Remove("new_session");File.WriteAllText(config,c.ToString());}}catch(Exception e){Debug.LogWarning(e);}}
        server=DefaultServer;
        depthCopy=new Material(DepthCopyShader);rgbCopy=new Material(RgbCopyShader);
        depthRT=new RenderTexture(256,256,0,RenderTextureFormat.RFloat,RenderTextureReadWrite.Linear);depthRT.Create();
        // MRUK 203 provides sceneless world locking. Keep geometry and PC coordinates fixed;
        // the SDK compensates recentering by adjusting the rig's TrackingSpace.
        worldLock=MRUK.Instance;
        if(!worldLock){
            var lockObject=new GameObject("MRUK world coordinate lock");lockObject.SetActive(false);
            worldLock=lockObject.AddComponent<MRUK>();
            worldLock.SceneSettings=new MRUK.MRUKSettings{LoadSceneOnStartup=false};
            worldLock.EnableWorldLock=true;lockObject.SetActive(true);
        }else worldLock.EnableWorldLock=true;
        controllerVisuals=Rig.GetComponentsInChildren<OVRControllerHelper>(true);
        BuildUI();StartCoroutine(CaptureLoop());StartCoroutine(UploadLoop());StartCoroutine(InitialRecenter());
        StartCoroutine(EnsureWorldLock());
        // OVRManager requests both permissions once; avoid duplicate startup requests.
        Debug.Log("QUEST3SPACE_START sdk=203 version=0.2 depth+PCA spool="+spool);
        StartCoroutine(Connect());
    }

    private Text Label(Transform parent,string text,int font=24)
    {
        var go=new GameObject("Label",typeof(RectTransform),typeof(Text));go.transform.SetParent(parent,false);
        var t=go.GetComponent<Text>();t.font=Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");t.text=text;t.fontSize=font;t.color=Color.white;t.alignment=TextAnchor.MiddleLeft;
        t.horizontalOverflow=HorizontalWrapMode.Wrap;t.verticalOverflow=VerticalWrapMode.Overflow;
        var layout=go.AddComponent<LayoutElement>();layout.preferredHeight=font*2;return t;
    }
    private Button Button(Transform parent,string text,Action action)
    {
        var go=new GameObject(text,typeof(RectTransform),typeof(Image),typeof(Button),typeof(LayoutElement));go.transform.SetParent(parent,false);
        go.GetComponent<Image>().color=ButtonIdle;go.GetComponent<LayoutElement>().preferredHeight=48;
        var t=Label(go.transform,text,22);var r=t.rectTransform;r.anchorMin=Vector2.zero;r.anchorMax=Vector2.one;r.offsetMin=new Vector2(10,0);r.offsetMax=Vector2.zero;
        var b=go.GetComponent<Button>();b.transition=Selectable.Transition.None;b.onClick.AddListener(()=>{Pressed(b);action();});
        go.AddComponent<BoxCollider>().size=new Vector3(700,48,1);return b;
    }
    private InputField Input(Transform parent,string text)
    {
        var go=new GameObject("Input",typeof(RectTransform),typeof(Image),typeof(InputField),typeof(LayoutElement));go.transform.SetParent(parent,false);
        go.GetComponent<Image>().color=new Color(.1f,.14f,.2f,.98f);go.GetComponent<LayoutElement>().preferredHeight=46;
        Text t=Label(go.transform,"",22);t.supportRichText=false;t.rectTransform.anchorMin=Vector2.zero;t.rectTransform.anchorMax=Vector2.one;t.rectTransform.offsetMin=new Vector2(10,0);t.rectTransform.offsetMax=Vector2.zero;
        var field=go.GetComponent<InputField>();field.textComponent=t;field.text=text;field.lineType=InputField.LineType.SingleLine;
        go.AddComponent<BoxCollider>().size=new Vector3(700,46,1);return field;
    }
    private void BuildUI()
    {
        if(!FindFirstObjectByType<EventSystem>())new GameObject("EventSystem",typeof(EventSystem),typeof(InputSystemUIInputModule));
        var go=new GameObject("Scan controls",typeof(RectTransform),typeof(Canvas),typeof(CanvasScaler),typeof(GraphicRaycaster));panel=go.transform;
        var canvas=go.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;canvas.worldCamera=Rig.centerEyeAnchor.GetComponent<Camera>();
        var rect=go.GetComponent<RectTransform>();rect.sizeDelta=new Vector2(740,1100);rect.localScale=Vector3.one*.00085f;
        var image=go.AddComponent<Image>();image.color=new Color(.035f,.06f,.09f,.94f);
        var layout=go.AddComponent<VerticalLayoutGroup>();layout.padding=new RectOffset(20,20,15,15);layout.spacing=8;layout.childForceExpandHeight=false;layout.childForceExpandWidth=true;
        Label(panel,"Quest3Space | Quest 3 MR Scanner",30);
        go.AddComponent<ContentSizeFitter>().verticalFit=ContentSizeFitter.FitMode.PreferredSize;
        status=Label(panel,"Starting…",23);status.GetComponent<LayoutElement>().preferredHeight=64;
        mainProgress=Label(panel,"Frames 0/60  |  Views 0/10",24);mainProgress.GetComponent<LayoutElement>().preferredHeight=62;
        mainProgressFill=ProgressBar(panel,true);
        connectButton=Button(panel,"Reconnect PC",()=>StartCoroutine(Connect()));
        placeButton=Button(panel,"Box region: hold RIGHT GRIP to place",PlaceBox);
        planeStartButton=Button(panel,"Plane region: Start",StartPlaneRegion);
        planePlaceButton=Button(panel,"Place plane (A / RGrip release)",PlaceRegionPlane);
        planeDeleteButton=Button(panel,"Delete pointed plane (B)",DeleteRegionPlane);
        startButton=Button(panel,"Start / resume (A)",()=>StartCoroutine(Begin()));
        pauseButton=Button(panel,"Pause (B)",()=>{scanning=false;message="Paused. Pending frames continue uploading.";});
        finishButton=Button(panel,"Finish + export (X)",Finish);
        newButton=Button(panel,"New session",()=>StartCoroutine(NewSession()));
        settingsButton=Button(panel,"Settings +",()=>{settingsOpen=!settingsOpen;RefreshControls();});
        var settings=new GameObject("Less frequent settings",typeof(RectTransform),typeof(VerticalLayoutGroup),typeof(LayoutElement));settings.transform.SetParent(panel,false);settingsPanel=settings.transform;
        var settingsLayout=settings.GetComponent<VerticalLayoutGroup>();settingsLayout.spacing=8;settingsLayout.childForceExpandHeight=false;settingsLayout.childForceExpandWidth=true;
        addressInput=Input(settingsPanel,server);tokenInput=Input(settingsPanel,token);
        Button(settingsPanel,"Apply connection",()=>StartCoroutine(Connect()));
        sizeButton=Button(settingsPanel,"Box size: 10 / 20 / 30 / 40 / 50 cm",()=>{if(!string.IsNullOrEmpty(session)||creating)return;side=Mathf.Clamp(side+.1f,.1f,.6f);if(side>.59f)side=.1f;DrawBox();});
        Button(settingsPanel,"Recenter controls (Y)",Recenter);
        Button(settingsPanel,"Depth occlusion: Soft / Hard / Off",CycleOcclusion);
        details=Label(settingsPanel,"",18);details.GetComponent<LayoutElement>().preferredHeight=100;
        settings.gameObject.SetActive(false);
        Label(panel,"Before scan: RGrip move | LGrip resize. After: LGrip preview",19);
        ray=new GameObject("Controller ray",typeof(LineRenderer)).GetComponent<LineRenderer>();ray.positionCount=2;ray.startWidth=.002f;ray.endWidth=.001f;ray.material=new Material(VertexColorShader);ray.material.SetFloat("_ZWrite",0);ray.material.renderQueue=3002;ray.startColor=ray.endColor=new Color(.3f,1,.8f);
        box=new GameObject("Scan volume",typeof(LineRenderer)).GetComponent<LineRenderer>();box.material=new Material(VertexColorShader);box.material.SetFloat("_ZWrite",0);box.material.renderQueue=3000;box.startWidth=box.endWidth=.002f;box.startColor=box.endColor=Color.cyan;box.useWorldSpace=true;
        var mg=new GameObject("PC reconstructed model",typeof(MeshFilter),typeof(MeshRenderer));model=mg.GetComponent<MeshFilter>();modelRenderer=mg.GetComponent<MeshRenderer>();modelRenderer.material=new Material(VertexColorShader);
        var vg=new GameObject("Reconstructed vertices inside scan box",typeof(MeshFilter),typeof(MeshRenderer));vertexModel=vg.GetComponent<MeshFilter>();vertexRenderer=vg.GetComponent<MeshRenderer>();vertexRenderer.material=new Material(VertexColorShader);vertexRenderer.enabled=false;
        worldPoints=new GameObject("Live world depth points").AddComponent<WorldDepthPoints>();worldPoints.Initialize(VertexColorShader,DepthCopyShader,DepthManager,Rig.centerEyeAnchor);
        var pg=new GameObject("Scan progress beside box",typeof(RectTransform),typeof(Canvas));progressPanel=pg.transform;pg.GetComponent<Canvas>().renderMode=RenderMode.WorldSpace;
        pg.GetComponent<RectTransform>().sizeDelta=new Vector2(400,170);progressPanel.localScale=Vector3.one*.0006f;
        pg.AddComponent<Image>().color=new Color(.025f,.045f,.06f,.88f);
        scanProgress=Label(progressPanel,"Place box to begin",23);scanProgress.raycastTarget=false;scanProgress.alignment=TextAnchor.MiddleCenter;
        scanProgress.rectTransform.anchorMin=Vector2.zero;scanProgress.rectTransform.anchorMax=Vector2.one;scanProgress.rectTransform.offsetMin=new Vector2(10,34);scanProgress.rectTransform.offsetMax=new Vector2(-10,-8);
        worldProgressFill=ProgressBar(progressPanel,false);
        planeRegion=new GameObject("Plane region").AddComponent<PlaneRegion>();planeRegion.Initialize(Rig.rightControllerAnchor,VertexColorShader);
        if(!UiDepthShader)throw new Exception("UI depth shader missing");
        uiDepthMaterial=new Material(UiDepthShader);BuildErrorDialog();
        foreach(var graphic in panel.GetComponentsInChildren<Graphic>(true))graphic.material=uiDepthMaterial;
        foreach(var graphic in progressPanel.GetComponentsInChildren<Graphic>(true))graphic.material=uiDepthMaterial;
        foreach(var graphic in errorDialog.GetComponentsInChildren<Graphic>(true))graphic.material=uiDepthMaterial;
        Recenter();DrawBox();
    }
    private void Recenter(){var head=Rig.centerEyeAnchor;Vector3 forward=Vector3.ProjectOnPlane(head.forward,Vector3.up).normalized;if(forward.sqrMagnitude<.1f)forward=Vector3.forward;panel.position=head.position+forward*1.15f-Vector3.up*.12f;panel.rotation=Quaternion.LookRotation(forward,Vector3.up);if(ErrorVisible)PositionErrorDialog();PositionPreview();}
    private void BuildErrorDialog()
    {
        var go=new GameObject("Scan error dialog",typeof(RectTransform),typeof(Canvas),typeof(Image),typeof(VerticalLayoutGroup));errorDialog=go.transform;
        var canvas=go.GetComponent<Canvas>();canvas.renderMode=RenderMode.WorldSpace;canvas.worldCamera=Rig.centerEyeAnchor.GetComponent<Camera>();canvas.overrideSorting=true;canvas.sortingOrder=100;
        var rect=go.GetComponent<RectTransform>();rect.sizeDelta=new Vector2(700,420);rect.localScale=Vector3.one*.00085f;
        go.GetComponent<Image>().color=new Color(.14f,.035f,.04f,.98f);
        var layout=go.GetComponent<VerticalLayoutGroup>();layout.padding=new RectOffset(22,22,18,18);layout.spacing=12;layout.childForceExpandHeight=false;layout.childForceExpandWidth=true;
        Label(errorDialog,"Cannot start scan",28).GetComponent<LayoutElement>().preferredHeight=44;
        errorBody=Label(errorDialog,"",22);errorBody.supportRichText=false;errorBody.GetComponent<LayoutElement>().preferredHeight=248;
        Button(errorDialog,"OK (A / B)",DismissError);
        var blocker=go.AddComponent<BoxCollider>();blocker.size=new Vector3(700,420,1);blocker.center=new Vector3(0,0,3);
        go.SetActive(false);
    }
    private void PositionErrorDialog(){if(!errorDialog)return;var head=Rig.centerEyeAnchor;errorDialog.SetPositionAndRotation(head.position+head.forward*.8f,head.rotation);}
    private void ShowStartError(string reason)
    {
        message=reason;boxFollowing=boxScaling=false;
        errorBody.text=reason.Length>650?reason.Substring(0,650)+"…":reason;
        PositionErrorDialog();errorDialog.gameObject.SetActive(true);
        Debug.LogWarning("QUEST3SPACE_SCAN_BLOCKED "+reason.Replace("\n"," | "));
    }
    private void DismissError(){if(errorDialog)errorDialog.gameObject.SetActive(false);}
    private Image ProgressBar(Transform parent,bool layout)
    {
        var bg=new GameObject("Progress track",typeof(RectTransform),typeof(Image));bg.transform.SetParent(parent,false);bg.GetComponent<Image>().color=new Color(.13f,.2f,.24f);bg.GetComponent<Image>().raycastTarget=false;
        var rect=bg.GetComponent<RectTransform>();
        if(layout)bg.AddComponent<LayoutElement>().preferredHeight=16;
        else{rect.anchorMin=new Vector2(0,0);rect.anchorMax=new Vector2(1,0);rect.offsetMin=new Vector2(14,12);rect.offsetMax=new Vector2(-14,28);}
        var fill=new GameObject("Progress fill",typeof(RectTransform),typeof(Image));fill.transform.SetParent(bg.transform,false);
        var image=fill.GetComponent<Image>();image.color=new Color(.25f,.9f,.66f);image.raycastTarget=false;
        var r=fill.GetComponent<RectTransform>();r.anchorMin=Vector2.zero;r.anchorMax=Vector2.zero;r.offsetMin=r.offsetMax=Vector2.zero;return image;
    }
    private static void FillProgress(Image image,float fraction){if(image)image.rectTransform.anchorMax=new Vector2(Mathf.Clamp01(fraction),1);}
    private void RefreshControls()
    {
        bool active=!string.IsNullOrEmpty(session),idle=!active&&!creating&&!finishing&&!resetting;
        bool editing=idle&&string.IsNullOrEmpty(completed),planes=planeRegion&&planeRegion.Active;
        bool openPlanes=planes&&!planeRegion.Closed;
        connectButton.gameObject.SetActive(!connected&&!creating&&!resetting);
        placeButton.gameObject.SetActive(editing);
        placeButton.GetComponentInChildren<Text>().text=planes?"Switch to BOX mode":"Box region: hold RIGHT GRIP to place";
        planeStartButton.gameObject.SetActive(editing&&!planes);
        planeStartButton.GetComponentInChildren<Text>().text=boxPlaced?"Switch to PLANE mode":"Plane region: Start";
        planePlaceButton.gameObject.SetActive(editing&&openPlanes);
        planeDeleteButton.gameObject.SetActive(editing&&planes&&planeRegion.Count>0);
        startButton.gameObject.SetActive(!scanning&&!finishing&&!creating&&!resetting&&(active||(editing&&boxPlaced&&!openPlanes)));
        startButton.interactable=true; // Always explain rejected start attempts in the modal.
        placeButton.interactable=planeStartButton.interactable=planePlaceButton.interactable=WorldCoordinatesReady;
        pauseButton.gameObject.SetActive(scanning&&!finishing);
        finishButton.gameObject.SetActive(active&&!finishing&&!creating&&!resetting);
        newButton.gameObject.SetActive(!scanning&&!creating&&!finishing&&!resetting&&(active||!string.IsNullOrEmpty(completed)||boxPlaced||planes));
        settingsPanel.gameObject.SetActive(settingsOpen);
        settingsButton.GetComponentInChildren<Text>().text=settingsOpen?"Settings -":"Settings +";
        sizeButton.gameObject.SetActive(editing&&!planes);
        if(details)details.text=$"Box {side*100:F0} cm | Sent {sent} | Model: {previewSource}\n{message}";
        float fraction=Mathf.Min(1,Mathf.Min(acceptedFrames/(float)RecommendedFrames,coverageDirections/(float)RecommendedDirections));
        FillProgress(mainProgressFill,fraction);
        string phase=finishing?"Saving":creating?"Starting":scanning?"Scanning":!string.IsNullOrEmpty(completed)?"Finished":active?"Paused":openPlanes?"Place planes":"Ready";
        mainProgress.text=CanResizeBox?$"Box {side*100:F0} cm\nRGrip: move / rotate  |  LGrip: resize":openPlanes?$"Planes {planeRegion.Count}/16 max\nPlace until region closes":$"{phase}  {Mathf.FloorToInt(fraction*100)}% of target\nFrames {acceptedFrames}/{RecommendedFrames}  |  Views {coverageDirections}/{RecommendedDirections}";
    }
    private void PositionPreview()
    {
        if(!model)return;
        Quaternion rotation=Quaternion.identity;Vector3 position=previewOrigin;float scale=1;
        if(overlayHeld&&model.sharedMesh){
            var hand=Rig.leftControllerAnchor;var bounds=model.sharedMesh.bounds;
            scale=Mathf.Min(1,.22f/Mathf.Max(.01f,Mathf.Max(bounds.size.x,Mathf.Max(bounds.size.y,bounds.size.z))));
            rotation=hand.rotation;position=hand.position+rotation*new Vector3(0,.14f,.12f)-rotation*(bounds.center*scale);
        }
        model.transform.SetPositionAndRotation(position,rotation);model.transform.localScale=Vector3.one*scale;modelRenderer.enabled=true;
        if(vertexModel){vertexModel.transform.SetPositionAndRotation(position,rotation);vertexModel.transform.localScale=Vector3.one*scale;}
    }
    private void LateUpdate(){if(overlayHeld)PositionPreview();}
    private void PaintButton(Button b){if(b)b.GetComponent<Image>().color=b==pressedButton&&Time.unscaledTime<pressedUntil?ButtonPressed:b==hoveredButton?ButtonHover:ButtonIdle;}
    private void Hover(Button b){if(b==hoveredButton)return;var previous=hoveredButton;hoveredButton=b;PaintButton(previous);PaintButton(b);}
    private void Pressed(Button b){var previous=pressedButton;pressedButton=b;pressedUntil=Time.unscaledTime+.18f;PaintButton(previous);PaintButton(b);OVRInput.SetControllerVibration(.5f,.22f,OVRInput.Controller.RTouch);hapticUntil=Time.unscaledTime+.045f;hapticActive=true;}
    private IEnumerator InitialRecenter(){yield return new WaitForSecondsRealtime(1.2f);Recenter();}
    private IEnumerator EnsureWorldLock()
    {
        // Native sceneless initialization can fail while the headset is waking up.
        // Retry with a real tracked anchor near the viewer instead of accepting an unlocked map.
        while(true){
            yield return new WaitForSecondsRealtime(4);
            if(!worldLock||worldLockEstablished||worldLock.IsWorldLockActive||!OVRManager.hasVrFocus||!DepthManager.IsDepthAvailable)continue;
            var anchorObject=new GameObject("Temporary scan world reference");
            var initial=new Pose(Rig.centerEyeAnchor.position+Rig.centerEyeAnchor.forward*.6f,Quaternion.identity);
            anchorObject.transform.SetPositionAndRotation(initial.position,initial.rotation);
            fallbackWorldAnchor=anchorObject.AddComponent<OVRSpatialAnchor>();
            float deadline=Time.unscaledTime+12;
            while(fallbackWorldAnchor&&(!fallbackWorldAnchor.Created||!fallbackWorldAnchor.Localized)&&Time.unscaledTime<deadline)yield return null;
            if(fallbackWorldAnchor&&fallbackWorldAnchor.Created&&fallbackWorldAnchor.Localized){
                worldLock.SetCustomWorldLockAnchor(fallbackWorldAnchor,initial);
                Debug.Log("QUEST3SPACE_WORLD_LOCK custom localized anchor selected; no disk save");
                deadline=Time.unscaledTime+4;
                while(!worldLock.IsWorldLockActive&&Time.unscaledTime<deadline)yield return null;
                if(worldLock.IsWorldLockActive){worldLockEstablished=true;continue;}
                worldLock.SetCustomWorldLockAnchor(null,Pose.identity);
            }
            if(anchorObject)Destroy(anchorObject);fallbackWorldAnchor=null;
            Debug.LogWarning("QUEST3SPACE_WORLD_LOCK anchor unavailable; retry when tracking ready");
        }
    }
    private void OnTrackingRecentered()
    {
        trackingSettleUntil=Time.unscaledTime+.75f;
        recenterControlsPending=true;
        if(worldPoints)worldPoints.Clear();
        Debug.Log("QUEST3SPACE_TRACKING_RECENTER worldLock="+(worldLock&&worldLock.IsWorldLockActive)+" geometry coordinates preserved; capture settling 0.75s");
    }

    private void CycleOcclusion(){var mode=DepthManager.OcclusionShadersMode;DepthManager.OcclusionShadersMode=mode==OcclusionShadersMode.SoftOcclusion?OcclusionShadersMode.HardOcclusion:mode==OcclusionShadersMode.HardOcclusion?OcclusionShadersMode.None:OcclusionShadersMode.SoftOcclusion;message="Depth occlusion: "+DepthManager.OcclusionShadersMode;Debug.Log("QUEST3SPACE_OCCLUSION mode="+DepthManager.OcclusionShadersMode+" removeHands="+DepthManager.RemoveHands);}
    private void StartPlaneRegion(){if(!WorldCoordinatesReady){message="Wait for world lock.";return;}if(!string.IsNullOrEmpty(session)||creating){message="Finish current scan first.";return;}completed="";boxFollowing=false;boxPlaced=false;planeRegion.StartRegion();origin=planeRegion.Seed;message=planeRegion.Hint;}
    private void PlaceRegionPlane(){if(!WorldCoordinatesReady||!planeRegion.Active||!string.IsNullOrEmpty(session)||creating)return;bool closed=planeRegion.Place();message=planeRegion.Hint;if(closed){origin=planeRegion.Center;side=Mathf.Max(planeRegion.Extent.x,Mathf.Max(planeRegion.Extent.y,planeRegion.Extent.z));boxRotation=Quaternion.identity;boxPlaced=true;StartCoroutine(Begin());}RebuildVertices();}
    private void DeleteRegionPlane(){if(!planeRegion.Active||!string.IsNullOrEmpty(session)||creating){message="Finish current scan before changing its region.";return;}planeRegion.Delete();boxPlaced=planeRegion.Closed;if(boxPlaced){origin=planeRegion.Center;side=Mathf.Max(planeRegion.Extent.x,Mathf.Max(planeRegion.Extent.y,planeRegion.Extent.z));}message=planeRegion.Hint;RebuildVertices();}
    private void PlaceBox(){if(!WorldCoordinatesReady){message="Wait for world lock.";return;}if(!string.IsNullOrEmpty(session)||creating){message="Finish the active scan before moving its box.";return;}planeRegion.Clear();completed="";origin=Rig.rightControllerAnchor.position+Rig.rightControllerAnchor.forward*.25f;boxRotation=Rig.rightControllerAnchor.rotation;boxPlaced=true;DrawBox(!boxFollowing);message="RGrip: move / rotate. LGrip: resize. Release both, A to scan.";}
    private void DrawBox(bool rebuildPoints=true){if(!box)return;var p=new Vector3[8];for(int i=0;i<8;i++)p[i]=origin+boxRotation*(new Vector3((i&1)==0?-.5f:.5f,(i&2)==0?-.5f:.5f,(i&4)==0?-.5f:.5f)*side);int[] e={0,1,3,2,0,4,5,1,5,7,3,7,6,2,6,4};box.positionCount=e.Length;box.SetPositions(e.Select(i=>p[i]).ToArray());if(rebuildPoints)RebuildVertices();}

    private void UpdateScanFeedback()
    {
        bool active=boxPlaced||!string.IsNullOrEmpty(session)||!string.IsNullOrEmpty(completed)||planeRegion.Active;
        box.enabled=active&&!planeRegion.Active;progressPanel.gameObject.SetActive(active);
        vertexRenderer.enabled=active&&verticesHeld&&displayedPoints>0;
        if(!active)return;
        bool ready=acceptedFrames>=RecommendedFrames&&coverageDirections>=RecommendedDirections;
        bool blocked=scanning&&(!connected||!CameraAccess.IsPlaying||!DepthManager.IsDepthAvailable);
        Color tint=blocked?new Color(1,.3f,.2f):finishing?new Color(.6f,.45f,1):scanning?(Time.unscaledTime-lastCapture<.22f?Color.white:ready?new Color(.2f,1,.45f):new Color(1,.7f,.15f)):Color.cyan;
        box.startColor=box.endColor=tint;
        progressPanel.position=origin+boxRotation*(Vector3.right*(side*.5f+.14f)+Vector3.up*(side*.5f));
        var direction=progressPanel.position-Rig.centerEyeAnchor.position;
        if(direction.sqrMagnitude>.001f)progressPanel.rotation=Quaternion.LookRotation(direction,Vector3.up);
        if(Time.unscaledTime<nextFeedback)return;nextFeedback=Time.unscaledTime+.25f;
        if(planeRegion.Active&&!planeRegion.Closed){FillProgress(worldProgressFill,0);scanProgress.text=$"PLANE REGION | {planeRegion.Count}/16 planes\nDouble-sided until closed\nRGrip release / A: place | B: delete\n"+planeRegion.Hint;scanProgress.color=Color.cyan;return;}
        if(CanResizeBox){FillProgress(worldProgressFill,0);scanProgress.text=$"BOX {side*100:F0} cm\nRGrip: move / rotate\nLGrip: resize";scanProgress.color=Color.cyan;return;}
        int percent=Mathf.FloorToInt(100*Mathf.Min(1,Mathf.Min(acceptedFrames/(float)RecommendedFrames,coverageDirections/(float)RecommendedDirections)));
        string phase=blocked?"CHECK CONNECTION / SENSORS":finishing?"SAVING":scanning?"CAPTURING":!string.IsNullOrEmpty(completed)?"FINISHED":"PAUSED / READY";
        FillProgress(worldProgressFill,percent/100f);
        scanProgress.text=$"{phase}  {percent}% of target\nFrames {acceptedFrames}/{RecommendedFrames}\nViews {coverageDirections}/{RecommendedDirections}";
        scanProgress.color=ready?new Color(.4f,1,.6f):Color.white;
    }
    private float nextFeedback;
    private void RebuildVertices()
    {
        if(!vertexModel)return;
        displayedPoints=0;
        if(vertexModel.sharedMesh){Destroy(vertexModel.sharedMesh);vertexModel.sharedMesh=null;}
        if(!model||!model.sharedMesh)return;
        var unique=new System.Collections.Generic.HashSet<Vector3Int>();var points=new System.Collections.Generic.List<Vector3>();
        foreach(var p in model.sharedMesh.vertices){var world=p+previewOrigin;var d=Quaternion.Inverse(boxRotation)*(world-origin);if(planeRegion.Active?!planeRegion.Closed||!planeRegion.Contains(world,.0001f):Mathf.Abs(d.x)>side*.5f||Mathf.Abs(d.y)>side*.5f||Mathf.Abs(d.z)>side*.5f)continue;
            var key=new Vector3Int(Mathf.RoundToInt(p.x*100000),Mathf.RoundToInt(p.y*100000),Mathf.RoundToInt(p.z*100000));if(unique.Add(key))points.Add(p);}
        int count=Mathf.Min(MaxVisiblePoints,points.Count);if(count==0)return;
        var positions=new Vector3[count*6];var colors=new Color32[positions.Length];var indices=new int[count*24];
        Vector3[] offsets={Vector3.right,Vector3.left,Vector3.up,Vector3.down,Vector3.forward,Vector3.back};int[] octa={0,2,4,2,1,4,1,3,4,3,0,4,2,0,5,1,2,5,3,1,5,0,3,5};float radius=Mathf.Clamp(side*.0025f,.0006f,.0015f);
        for(int i=0;i<count;i++){var p=points[(int)((long)i*points.Count/count)];for(int j=0;j<6;j++){positions[i*6+j]=p+offsets[j]*radius;colors[i*6+j]=new Color32(70,255,180,255);}for(int j=0;j<24;j++)indices[i*24+j]=i*6+octa[j];}
        var mesh=new Mesh{indexFormat=IndexFormat.UInt32};mesh.vertices=positions;mesh.colors32=colors;mesh.triangles=indices;mesh.RecalculateBounds();vertexModel.sharedMesh=mesh;displayedPoints=count;
    }

    void Update()
    {
        while(notes.TryDequeue(out var text)){message=text;Debug.Log("QUEST3SPACE "+text);}
        if(!Rig)return;
        if(recenterDisplay==null&&OVRManager.display!=null){recenterDisplay=OVRManager.display;recenterDisplay.RecenteredPose+=OnTrackingRecentered;}
        bool locked=worldLock&&worldLock.IsWorldLockActive;
        if(locked)worldLockEstablished=true;
        if(locked!=previousWorldLock){previousWorldLock=locked;trackingSettleUntil=Time.unscaledTime+.75f;if(worldPoints)worldPoints.Clear();Debug.Log("QUEST3SPACE_WORLD_LOCK active="+locked+" trackingSpace="+Rig.trackingSpace.localToWorldMatrix);}
        if(recenterControlsPending&&Time.unscaledTime>=trackingSettleUntil){recenterControlsPending=false;Recenter();Debug.Log("QUEST3SPACE_CONTROLS_RECENTER after tracking reset; scan geometry unchanged");}
        if(hapticActive&&Time.unscaledTime>=hapticUntil){OVRInput.SetControllerVibration(0,0,OVRInput.Controller.RTouch);hapticActive=false;}
        if(pressedButton&&Time.unscaledTime>=pressedUntil){var released=pressedButton;pressedButton=null;PaintButton(released);}
        bool held=OVRInput.Get(OVRInput.Button.PrimaryHandTrigger,OVRInput.Controller.LTouch);
        bool handPreview=held&&(!string.IsNullOrEmpty(session)||!string.IsNullOrEmpty(completed));
        if(handPreview!=overlayHeld){overlayHeld=handPreview;PositionPreview();Debug.Log("QUEST3SPACE_OVERLAY held="+handPreview+" origin="+previewOrigin);}
        verticesHeld=OVRInput.Get(OVRInput.Axis1D.PrimaryIndexTrigger,OVRInput.Controller.LTouch)>.15f;
        worldPoints.SetHeld(verticesHeld&&WorldCoordinatesReady);
        UpdateScanFeedback();
        if(lastRgb!=CameraAccess.IsPlaying||lastDepth!=DepthManager.IsDepthAvailable){lastRgb=CameraAccess.IsPlaying;lastDepth=DepthManager.IsDepthAvailable;Debug.Log($"QUEST3SPACE_SENSORS rgb={lastRgb} depth={lastDepth}");}
        if(CameraAccess.IsPlaying&&!scanning&&!capturing&&Time.unscaledTime-lastProbe>2){lastProbe=Time.unscaledTime;ProbeCamera();}
        bool errorWasVisible=ErrorVisible;
        if(errorWasVisible){if(OVRInput.GetDown(OVRInput.Button.One)||OVRInput.GetDown(OVRInput.Button.Two))DismissError();}
        else if(OVRInput.GetDown(OVRInput.Button.One)){if(planeRegion.Active&&!planeRegion.Closed)PlaceRegionPlane();else StartCoroutine(Begin());}
        if(!errorWasVisible&&OVRInput.GetDown(OVRInput.Button.Two)){if(planeRegion.Active&&string.IsNullOrEmpty(session)&&!creating)DeleteRegionPlane();else{scanning=false;message="Paused";}}
        if(!errorWasVisible&&OVRInput.GetDown(OVRInput.Button.Three))Finish();
        if(OVRInput.GetDown(OVRInput.Button.Four))Recenter();
        bool rightHeld=OVRInput.Get(OVRInput.Button.PrimaryHandTrigger,OVRInput.Controller.RTouch);
        if(ErrorVisible||errorWasVisible){boxFollowing=boxScaling=false;planeGripHeld=rightHeld;}
        else if(planeRegion.Active){planeRegion.Preview(string.IsNullOrEmpty(session)&&!creating);if(planeGripHeld&&!rightHeld&&string.IsNullOrEmpty(session)&&!creating)PlaceRegionPlane();planeGripHeld=rightHeld;}
        else if(rightHeld&&WorldCoordinatesReady&&string.IsNullOrEmpty(session)&&!creating&&!resetting){boxFollowing=true;PlaceBox();}
        else if(boxFollowing){boxFollowing=false;RebuildVertices();Debug.Log("QUEST3SPACE_BOX_LOCK position="+origin+" rotation="+boxRotation);}
        else if(rightHeld&&OVRInput.GetDown(OVRInput.Button.PrimaryHandTrigger,OVRInput.Controller.RTouch))message="Finish the active scan before moving its box.";
        bool resizing=held&&CanResizeBox&&!ErrorVisible;
        if(resizing){
            float distance=Vector3.Distance(Rig.leftControllerAnchor.position,origin);
            if(!boxScaling){boxScaleStartDistance=Mathf.Max(.05f,distance);boxScaleStartSide=side;boxScaling=true;Debug.Log("QUEST3SPACE_BOX_RESIZE start side="+side);}
            float target=Mathf.Clamp(boxScaleStartSide*distance/boxScaleStartDistance,.1f,.5f);
            side=Mathf.Lerp(side,target,1-Mathf.Exp(-20*Time.unscaledDeltaTime));DrawBox(false);
        }else if(boxScaling){boxScaling=false;RebuildVertices();Debug.Log("QUEST3SPACE_BOX_RESIZE locked side="+side);}
        var r=new Ray(Rig.rightControllerAnchor.position,Rig.rightControllerAnchor.forward);Vector3 end=r.GetPoint(2);
        if(planeRegion.Active)planeRegion.Point(r);
        Button pointed=null;
        if(Physics.Raycast(r,out var hit,3)){end=hit.point;pointed=hit.collider.GetComponent<Button>();if(OVRInput.GetDown(OVRInput.Button.PrimaryIndexTrigger,OVRInput.Controller.RTouch)){
            var pointer=new PointerEventData(EventSystem.current){button=PointerEventData.InputButton.Left};
            if(hit.collider.GetComponent<Button>() is Button b&&b.interactable&&b.isActiveAndEnabled&&(!ErrorVisible||b.transform.IsChildOf(errorDialog)))b.onClick.Invoke();
            else if(!ErrorVisible&&hit.collider.GetComponent<InputField>() is InputField f){f.Select();f.ActivateInputField();}
        }}Hover(pointed);ray.startColor=ray.endColor=pointed?ButtonHover:new Color(.3f,1,.8f);ray.SetPosition(0,r.origin);ray.SetPosition(1,end);
        if(status&&Time.unscaledTime>=nextStatus){nextStatus=Time.unscaledTime+.25f;RefreshControls();status.text=$"PC {(connected?"OK":"OFFLINE")} | RGB {(CameraAccess.IsPlaying&&imageVerified?"OK":"WAIT")} | Depth {(DepthManager.IsDepthAvailable?"OK":"WAIT")}\nWorld lock {(WorldCoordinatesReady?"OK":"WAIT")} | Upload queue: {pendingDisplay}";}
        perfFrames++;perfWorst=Mathf.Max(perfWorst,Time.unscaledDeltaTime);if(Time.unscaledDeltaTime>.025f)perfSlow++;
        if(Time.unscaledTime-perfStart>=10){Debug.Log($"QUEST3SPACE_PERF fps={perfFrames/(Time.unscaledTime-perfStart):F1} slow25ms={perfSlow} worstMs={perfWorst*1000:F1} scanning={scanning} occlusion={DepthManager.OcclusionShadersMode} depth={DepthManager.IsDepthAvailable} softKeyword={Shader.IsKeywordEnabled("SOFT_OCCLUSION")} removeHands={DepthManager.RemoveHands}");foreach(var helper in controllerVisuals){var visual=helper.m_controller==OVRInput.Controller.LTouch?helper.m_modelMetaTouchPlusLeftController:helper.m_modelMetaTouchPlusRightController;Debug.Log("QUEST3SPACE_CONTROLLER hand="+helper.m_controller+" touchPlusVisible="+(visual&&visual.activeInHierarchy)+" depthCue=on uiOcclusion=off");}perfFrames=perfSlow=0;perfWorst=0;perfStart=Time.unscaledTime;}
        if(connected&&(!string.IsNullOrEmpty(session)||!string.IsNullOrEmpty(completed))&&Time.unscaledTime-lastPoll>2&&!polling){lastPoll=Time.unscaledTime;StartCoroutine(Poll());}
    }
    private UnityWebRequest Request(string method,string path,byte[] body=null)
    {
        var r=new UnityWebRequest(server.TrimEnd('/')+path,method);r.downloadHandler=new DownloadHandlerBuffer();r.timeout=15;r.SetRequestHeader("X-Scan-Token",token);
        if(body!=null){r.uploadHandler=new UploadHandlerRaw(body);r.SetRequestHeader("Content-Type",path.Contains("/rgbd/")?"application/octet-stream":"application/json");}return r;
    }
    private IEnumerator Connect()
    {
        string next=addressInput.text.Trim().TrimEnd('/');if(!next.Contains("://"))next="http://"+next;if(!Uri.TryCreate(next,UriKind.Absolute,out var uri)){message="Invalid PC address";yield break;}if(uri.IsDefaultPort)next=next+":8765";
        string nextToken=tokenInput.text.Trim().ToUpperInvariant();
        if(Pending().Length>0&&(next!=server||nextToken!=token)){message="Upload pending frames to the original PC first.";yield break;}
        server=next;token=nextToken;PlayerPrefs.SetString("server",server);PlayerPrefs.SetString("token",token);PlayerPrefs.Save();
        using(var req=Request("GET","/api/sessions")){yield return req.SendWebRequest();connected=req.result==UnityWebRequest.Result.Success;message=connected?"PC connected. Right grip to place box, A to start.":"PC connection failed: "+req.error;Debug.Log("QUEST3SPACE_CONNECT "+req.responseCode+" "+req.error);
            if(connected&&!requestNewSession&&PlayerPrefs.GetInt("freshSession",0)==0&&string.IsNullOrEmpty(session)&&string.IsNullOrEmpty(completed)){try{foreach(JObject saved in JArray.Parse(req.downloadHandler.text)){if((string)saved["status"]=="complete"&&(string)saved["method"]=="depth-tsdf"){completed=(string)saved["id"];Debug.Log("QUEST3SPACE_PREVIEW_RESTORE "+completed);break;}}}catch(Exception e){Debug.LogWarning(e);}}
        }
        if(connected&&requestNewSession){requestNewSession=false;yield return NewSession();}
    }
    private IEnumerator NewSession()
    {
        if(resetting||creating)yield break;
        resetting=true;scanning=false;
        if(!string.IsNullOrEmpty(session)){Finish();message="Saving previous session before starting fresh…";while(!string.IsNullOrEmpty(session))yield return null;}
        while(polling||capturing)yield return null;
        completed="";boxPlaced=false;finishing=false;
        planeRegion.Clear();planeGripHeld=false;if(worldPoints)worldPoints.Clear();
        previewId="";previewRevision=-1;previewRun="";previewSource="live";
        acceptedFrames=coverageDirections=displayedPoints=0;if(vertexModel.sharedMesh){Destroy(vertexModel.sharedMesh);vertexModel.sharedMesh=null;}
        if(model.sharedMesh){Destroy(model.sharedMesh);model.sharedMesh=null;}
        PlayerPrefs.SetInt("freshSession",1);PlayerPrefs.Save();
        message="New session ready. RIGHT GRIP: place box, then A: start.";
        Debug.Log("QUEST3SPACE_NEW_SESSION_READY previous files preserved");resetting=false;
    }
    private IEnumerator Begin()
    {
        Debug.Log($"QUEST3SPACE_SCAN_ATTEMPT pc={connected} world={WorldCoordinatesReady} rgb={CameraAccess.IsPlaying} pixels={imageVerified} depth={DepthManager.IsDepthAvailable} box={boxPlaced} following={boxFollowing} scaling={boxScaling} session={session}");
        if(!WorldCoordinatesReady){ShowStartError("World lock is not ready.\nLook at a fixed, well-lit room surface and wait for World lock OK.\nAfter resetting view, wait briefly before starting.");yield break;}
        if(!connected||creating||resetting){ShowStartError(!connected?"PC is not connected.\nUse Reconnect PC. Keep PC and Quest on the same Wi-Fi, with the PC service running.":"A session request is still in progress.\nWait for it to finish, then try again.");yield break;}
        if(finishing){ShowStartError("The previous scan is still saving.\nWait for pending uploads and PC export to finish.");yield break;}
        if(!CameraAccess.IsPlaying||!DepthManager.IsDepthAvailable){ShowStartError("Camera or environment depth is not ready.\nWear the headset and return to this app.\nCheck camera and spatial-data permissions; wait for RGB and Depth OK.");yield break;}
        if(!imageVerified){ShowStartError("Camera image is not ready yet.\nLook at a well-lit surface and wait for RGB OK.\nUniform or empty camera frames cannot be scanned.");yield break;}
        if(!boxPlaced){ShowStartError("No scan region is placed.\nHold RGrip to place the box. LGrip resizes it.\nRelease both grips, then press A.");yield break;}
        if(planeRegion.Active&&!planeRegion.Closed){ShowStartError("The plane region is still open.\nAdd planes until they enclose the start point, or switch to BOX mode.");yield break;}
        if(OVRInput.Get(OVRInput.Button.PrimaryHandTrigger,OVRInput.Controller.RTouch)||(!planeRegion.Active&&OVRInput.Get(OVRInput.Button.PrimaryHandTrigger,OVRInput.Controller.LTouch))){ShowStartError("Release BOTH GRIP buttons, then press A.\nRGrip moves the box; LGrip resizes it.\nThe region must be locked before capture can start.");yield break;}
        if(!string.IsNullOrEmpty(session)){scanning=true;yield break;}
        creating=true;
        PlayerPrefs.SetFloat("boxQx",boxRotation.x);PlayerPrefs.SetFloat("boxQy",boxRotation.y);PlayerPrefs.SetFloat("boxQz",boxRotation.z);PlayerPrefs.SetFloat("boxQw",boxRotation.w);
        var body=new JObject{["method"]="depth-tsdf",["origin"]=new JArray(origin.x,origin.y,origin.z),["rotation"]=new JArray(boxRotation.x,boxRotation.y,boxRotation.z,boxRotation.w),["size_mm"]=new JArray(side*1000,side*1000,side*1000),["resolution"]=96};
        if(planeRegion.Active){body["planes"]=planeRegion.Json();body["region_seed"]=new JArray(planeRegion.Seed.x,planeRegion.Seed.y,planeRegion.Seed.z);}
        using(var req=Request("POST","/api/sessions",Encoding.UTF8.GetBytes(body.ToString(Formatting.None)))){
            yield return req.SendWebRequest();
            if(req.result==UnityWebRequest.Result.Success){try{session=(string)JObject.Parse(req.downloadHandler.text)["id"];completed="";acceptedFrames=coverageDirections=0;previewOrigin=origin;previewId="";previewRun="";if(model.sharedMesh){Destroy(model.sharedMesh);model.sharedMesh=null;}RebuildVertices();PositionPreview();PlayerPrefs.SetString("session",session);PlayerPrefs.SetFloat("originX",origin.x);PlayerPrefs.SetFloat("originY",origin.y);PlayerPrefs.SetFloat("originZ",origin.z);PlayerPrefs.SetFloat("side",side);PlayerPrefs.Save();scanning=true;message="Scanning";Debug.Log("QUEST3SPACE_SCAN_START "+session);}catch(Exception e){ShowStartError("Could not initialize the scan.\n"+e.Message);}}
            else ShowStartError($"PC could not create the scan (HTTP {req.responseCode}).\n"+(string.IsNullOrWhiteSpace(req.downloadHandler.text)?req.error:req.downloadHandler.text)+"\nCheck PC connection / pairing and try again.");
        }creating=false;
    }
    private void Finish(){if(string.IsNullOrEmpty(session)){message="No active scan";return;}scanning=false;finishing=true;PlayerPrefs.SetInt("finishing",1);PlayerPrefs.Save();message="Uploading remaining frames, then exporting…";}

    private static JArray Matrix(Matrix4x4 matrix){var a=new JArray();for(int r=0;r<4;r++)for(int c=0;c<4;c++)a.Add(matrix[r,c]);return a;}
    private IEnumerator CaptureLoop()
    {
        while(true){yield return new WaitForEndOfFrame();
            if(!WorldCoordinatesReady||!scanning||capturing||string.IsNullOrEmpty(session)||Time.unscaledTime-lastCapture<.65f||!CameraAccess.IsPlaying||!DepthManager.IsDepthAvailable)continue;
            if(Pending().Length>=120){scanning=false;message="Upload queue full; paused until PC catches up.";continue;}
            var matrices=Shader.GetGlobalMatrixArray("_EnvironmentDepthReprojectionMatrices");var tex=Shader.GetGlobalTexture("_EnvironmentDepthTexture");
            if(matrices==null||matrices.Length==0||tex==null)continue;
            lastCapture=Time.unscaledTime;capturing=true;
            var pose=CameraAccess.GetCameraPose();var resolution=CameraAccess.CurrentResolution;
            int rw=resolution.x,rh=resolution.y;
            var r0=CameraAccess.ViewportPointToRay(Vector2.zero,pose);var r1=CameraAccess.ViewportPointToRay(Vector2.one,pose);
            var inv=Quaternion.Inverse(pose.rotation);Vector3 d0=inv*r0.direction,d1=inv*r1.direction;d0/=d0.z;d1/=d1.z;
            float fx=rw/(d1.x-d0.x),fy=rh/(d1.y-d0.y),cx=-d0.x*fx,cy=rh+d0.y*fy;
            Matrix4x4 w2c=Matrix4x4.Scale(new Vector3(1,-1,1))*Matrix4x4.TRS(pose.position,pose.rotation,Vector3.one).inverse;
            Vector4 zp=Shader.GetGlobalVector("_EnvironmentDepthZBufferParams");
            var meta=new JObject{["protocol"]=2,["timestamp_utc"]=CameraAccess.Timestamp.ToString("O"),["intrinsics"]=new JArray(fx,fy,cx,cy),["rgb_world_to_camera"]=Matrix(w2c),["depth_world_to_clip"]=Matrix(matrices[0]),["depth_z_params"]=new JArray(zp.x,zp.y),["depth_size"]=new JArray(256,256),["depth_uv_origin"]="bottom-left",["source"]="Meta XR SDK 203 / PCA + EnvironmentDepthManager"};
            string sid=session;string fid=DateTime.UtcNow.Ticks.ToString();byte[] rgba=null,depth=null;bool failed=false;int completedReads=0;
            Graphics.Blit(null,depthRT,depthCopy);
            Action finish=()=>{completedReads++;if(completedReads<2)return;if(failed){capturing=false;return;}byte[] metaBytes=Encoding.UTF8.GetBytes(meta.ToString(Formatting.None));byte[] pixels=rgba,dep=depth;
                Task.Run(()=>{try{byte[] jpg=ImageConversion.EncodeArrayToJPG(pixels,UnityEngine.Experimental.Rendering.GraphicsFormat.R8G8B8A8_UNorm,(uint)rw,(uint)rh,0,88);string dir=Path.Combine(spool,sid);Directory.CreateDirectory(dir);string path=Path.Combine(dir,fid+".qsp");using(var f=new FileStream(path+".tmp",FileMode.Create,FileAccess.Write,FileShare.None))using(var b=new BinaryWriter(f)){b.Write(metaBytes.Length);b.Write(jpg.Length);b.Write(dep.Length);b.Write(metaBytes);b.Write(jpg);b.Write(dep);b.Flush();f.Flush(true);}File.Move(path+".tmp",path);notes.Enqueue("Captured RGB-D frame");}catch(Exception e){notes.Enqueue("Cache write failed: "+e.Message);}finally{capturing=false;}});
            };
            AsyncGPUReadback.Request(CameraAccess.GetTexture(),0,TextureFormat.RGBA32,req=>{try{if(req.hasError){failed=true;notes.Enqueue("RGB GPU readback failed");}else if(!ValidateRgb(req)){failed=true;notes.Enqueue("RGB camera returned uniform pixels; frame skipped");}else rgba=req.GetData<byte>().ToArray();}catch(Exception e){failed=true;notes.Enqueue(e.Message);}finally{finish();}});
            AsyncGPUReadback.Request(depthRT,0,TextureFormat.RFloat,req=>{try{if(req.hasError){failed=true;notes.Enqueue("Depth GPU readback failed");}else depth=req.GetData<byte>().ToArray();}catch(Exception e){failed=true;notes.Enqueue(e.Message);}finally{finish();}});
        }
    }
    private bool ValidateRgb(AsyncGPUReadbackRequest req)
    {
        var pixels=req.GetData<Color32>();int stride=Mathf.Max(1,pixels.Length/2048),n=0;double sum=0,square=0;
        for(int i=0;i<pixels.Length;i+=stride){var p=pixels[i];double value=(p.r+p.g+p.b)/3.0;sum+=value;square+=value*value;n++;}
        double variance=n>0?square/n-(sum/n)*(sum/n):0;
        bool valid=variance>.25;
        if(valid!=imageVerified)Debug.Log($"QUEST3SPACE_RGB_CONTENT valid={valid} variance={variance:F2}");
        imageVerified=valid;return valid;
    }
    private void ProbeCamera()
    {
        capturing=true;
        AsyncGPUReadback.Request(CameraAccess.GetTexture(),0,TextureFormat.RGBA32,req=>{try{if(!req.hasError)ValidateRgb(req);}catch(Exception e){Debug.LogWarning(e);}finally{capturing=false;}});
    }
    private string[] Pending(){if(string.IsNullOrEmpty(spool)||!Directory.Exists(spool))return Array.Empty<string>();return Directory.GetFiles(spool,"*.qsp",SearchOption.AllDirectories).OrderBy(x=>x).ToArray();}
    private IEnumerator UploadLoop()
    {
        while(true){yield return new WaitForSecondsRealtime(.25f);if(!connected)continue;
            var files=Pending();
            pendingDisplay=files.Length;
            if(files.Length>0){string file=files[0],sid=Path.GetFileName(Path.GetDirectoryName(file)),fid=Path.GetFileNameWithoutExtension(file);byte[] packet;
                try{packet=File.ReadAllBytes(file);}catch(Exception e){message=e.Message;continue;}
                using(var req=Request("POST","/api/sessions/"+sid+"/rgbd/"+fid,packet)){
                    yield return req.SendWebRequest();if(req.result==UnityWebRequest.Result.Success){bool stored=false;try{stored=(bool)JObject.Parse(req.downloadHandler.text)["stored"];}catch{}if(stored){File.Delete(file);sent++;Debug.Log("QUEST3SPACE_FRAME_STORED "+fid+" bytes="+packet.Length);}}else{message="Upload retry: "+req.responseCode+" "+req.error;yield return new WaitForSecondsRealtime(2);}
                }
            }else if(finishing&&!capturing&&Pending().Length==0&&!string.IsNullOrEmpty(session)){
                using(var req=Request("POST","/api/sessions/"+session+"/finish",Array.Empty<byte>())){yield return req.SendWebRequest();if(req.result!=UnityWebRequest.Result.Success)message="Finish retry: "+req.downloadHandler.text;}
                yield return new WaitForSecondsRealtime(2);
            }
        }
    }
    private IEnumerator Poll()
    {
        polling=true;
        int revision=-1;
        string targetSession=string.IsNullOrEmpty(session)?completed:session;
        using(var req=Request("GET","/api/sessions/"+targetSession)){yield return req.SendWebRequest();if(req.result==UnityWebRequest.Result.Success&&targetSession==(string.IsNullOrEmpty(session)?completed:session)){try{
            var s=JObject.Parse(req.downloadHandler.text);message=(string)s["hint"]+" | accepted "+s["accepted"]+" | directions "+s["coverage_bins"]+"/12";
            revision=(int)s["accepted"];
            acceptedFrames=revision;coverageDirections=(int?)s["coverage_bins"]??0;
            if(s["size_mm"] is JArray dimensions&&dimensions.Count==3)side=(float)dimensions[0]/1000f;
            if(s["origin"] is JArray o&&o.Count==3){previewOrigin=new Vector3((float)o[0],(float)o[1],(float)o[2]);PositionPreview();}
            boxRotation=s["rotation"] is JArray q&&q.Count==4?new Quaternion((float)q[0],(float)q[1],(float)q[2],(float)q[3]):Quaternion.identity;
            if(s["planes"] is JArray region&&s["region_seed"] is JArray seed&&!planeRegion.Active)planeRegion.Restore(region,seed);
            origin=previewOrigin;boxPlaced=true;DrawBox(false);
            if(session==targetSession&&((string)s["status"]=="complete"||(string)s["status"]=="failed")){completed=session;scanning=false;finishing=false;session="";PlayerPrefs.SetString("session","");PlayerPrefs.SetInt("finishing",0);PlayerPrefs.Save();}
        }catch(Exception e){message=e.Message;}}else message="Status: "+req.downloadHandler.text;}
        string previewSession=string.IsNullOrEmpty(session)?completed:session;
        if(previewSession==targetSession&&!string.IsNullOrEmpty(previewSession)&&revision>0){
            string availableRun="";
            using(var req=Request("GET","/api/sessions/"+previewSession+"/offline")){
                yield return req.SendWebRequest();
                if(req.result==UnityWebRequest.Result.Success){try{availableRun=(string)JObject.Parse(req.downloadHandler.text)["available_run"]??"";}catch(Exception e){Debug.LogWarning(e.Message);}}
            }
            // A transient status failure must not replace an already refined mesh with the live mesh.
            if(availableRun==""&&previewId==previewSession)availableRun=previewRun;
            bool changed=previewId!=previewSession||(availableRun!=""?previewRun!=availableRun:previewRevision!=revision);
            if(changed&&Time.unscaledTime-lastPreview>5){
                lastPreview=Time.unscaledTime;
                string path="/api/sessions/"+previewSession+"/preview"+(availableRun!=""?"?textured=true&run="+UnityWebRequest.EscapeURL(availableRun):"");
                using(var req=Request("GET",path)){
                    yield return req.SendWebRequest();
                    if(req.result==UnityWebRequest.Result.Success){
                        string json=req.downloadHandler.text;var parse=Task.Run(()=>JObject.Parse(json));while(!parse.IsCompleted)yield return null;
                        string current=string.IsNullOrEmpty(session)?completed:session;
                        if(parse.IsCompletedSuccessfully&&current==previewSession&&!resetting){
                            ShowMesh(parse.Result);previewId=previewSession;previewRevision=revision;previewRun=availableRun;
                            previewSource=availableRun!=""?"PC photo textured "+availableRun:"live (waiting for PC refinement)";
                            Debug.Log("QUEST3SPACE_PREVIEW session="+previewSession+" source="+previewSource+" faces="+((JArray)parse.Result["faces"]).Count);
                        }else if(parse.IsFaulted)Debug.LogWarning(parse.Exception);
                    }
                }
            }
            message+=" | Model: "+previewSource;
        }
        polling=false;
    }
    private void ShowMesh(JObject data)
    {
        if(data["texture_jpeg"]!=null){
            var points=(JArray)data["vertices"];var tris=(JArray)data["faces"];var uvData=(JArray)data["uv"];
            var photo=new Texture2D(2,2,TextureFormat.RGB24,true);
            if(!ImageConversion.LoadImage(photo,Convert.FromBase64String((string)data["texture_jpeg"]),false)){Destroy(photo);throw new Exception("Photo texture decode failed");}
            photo.filterMode=FilterMode.Trilinear;photo.anisoLevel=4;photo.wrapMode=TextureWrapMode.Clamp;
            var positions=new Vector3[points.Count];var uvs=new Vector2[points.Count];var indices=new int[tris.Count*3];
            for(int i=0;i<points.Count;i++){var p=(JArray)points[i];positions[i]=new Vector3((float)p[0],(float)p[2],-(float)p[1]);uvs[i]=new Vector2((float)uvData[i][0],(float)uvData[i][1]);}
            for(int i=0;i<tris.Count;i++)for(int j=0;j<3;j++)indices[i*3+j]=(int)tris[i][j];
            var texturedMesh=new Mesh{indexFormat=IndexFormat.UInt32};texturedMesh.vertices=positions;texturedMesh.uv=uvs;texturedMesh.triangles=indices;texturedMesh.RecalculateBounds();
            var material=model.GetComponent<MeshRenderer>().material;material.mainTexture=photo;material.SetFloat("_UseTexture",1);
            if(model.sharedMesh)Destroy(model.sharedMesh);model.sharedMesh=texturedMesh;if(previewTexture)Destroy(previewTexture);previewTexture=photo;
            PositionPreview();RebuildVertices();return;
        }
        model.GetComponent<MeshRenderer>().material.SetFloat("_UseTexture",0);
        var vertices=(JArray)data["vertices"];var faces=(JArray)data["faces"];var colors=(JArray)data["colors"];if(vertices.Count==0)return;
        var v=new Vector3[faces.Count*3];var c=new Color32[v.Length];var idx=new int[v.Length];
        for(int i=0;i<faces.Count;i++)for(int j=0;j<3;j++){int n=i*3+j;var p=(JArray)vertices[(int)faces[i][j]];v[n]=new Vector3((float)p[0],(float)p[2],-(float)p[1]);idx[n]=n;c[n]=new Color32((byte)colors[i][0],(byte)colors[i][1],(byte)colors[i][2],255);}
        var mesh=new Mesh{indexFormat=IndexFormat.UInt32};mesh.vertices=v;mesh.triangles=idx;mesh.colors32=c;mesh.RecalculateNormals();mesh.RecalculateBounds();if(model.sharedMesh)Destroy(model.sharedMesh);model.sharedMesh=mesh;
        PositionPreview();
        RebuildVertices();
    }
    void OnApplicationPause(bool paused){if(paused){scanning=false;Hover(null);OVRInput.SetControllerVibration(0,0,OVRInput.Controller.RTouch);hapticActive=false;Debug.Log("QUEST3SPACE_PAUSED");}}
    void OnDestroy(){if(uiDepthMaterial)Destroy(uiDepthMaterial);if(recenterDisplay!=null)recenterDisplay.RecenteredPose-=OnTrackingRecentered;scanning=false;if(rgbRT)rgbRT.Release();if(depthRT)depthRT.Release();}
}



