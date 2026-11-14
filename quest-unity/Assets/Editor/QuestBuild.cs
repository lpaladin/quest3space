using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Xml;
using Meta.XR;
using Meta.XR.EnvironmentDepth;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEditor.XR.Management;
using UnityEditor.XR.Management.Metadata;
using UnityEditor.XR.OpenXR.Features;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.XR.Management;
using UnityEngine.XR.OpenXR;

public static class QuestBuild
{
    public static void ValidateStableWorldPoints()
    {
        var map=new StableWorldPointMap();var p=new Vector3(.012f,.012f,1.012f);
        for(int i=0;i<20;i++)map.Observe(p,1,0);
        if(map.Points.Count!=0)throw new Exception("One image must not confirm a point");
        map.Observe(p,2,.2f);map.Observe(p,3,.4f);
        if(map.Points.Count!=1)throw new Exception("Three frames must confirm stable depth");
        var original=map.Points[0];
        for(int f=4;f<30;f++)map.Observe(p+Vector3.right*.006f,f,f*.2f);
        if(map.Points.Count!=1||map.Points[0]!=original)throw new Exception("Confirmed points moved");
        // Neighbor across a voxel boundary, but too close to the first surfel.
        var close=p+Vector3.left*.015f;
        for(int f=30;f<35;f++)map.Observe(close,f,f*.2f);
        if(map.Points.Count!=1)throw new Exception("Cross-cell minimum spacing failed");
        for(int f=40;f<43;f++)for(int x=-4;x<=4;x++)for(int y=-4;y<=4;y++)
            map.Observe(p+new Vector3(x*.05f,y*.05f,0),f,f*.2f);
        if(map.Points.Count<60)throw new Exception("Stable surface did not fill");
        for(int a=0;a<map.Points.Count;a++)for(int b=a+1;b<map.Points.Count;b++)
            if(Vector3.Distance(map.Points[a],map.Points[b])<StableWorldPointMap.MinimumSpacing-.00001f)throw new Exception("Point spacing violated");
        map.Clear();map.Observe(p,1,0);map.BeginFrame(2);
        if(map.Points.Count!=0||map.PendingCount!=0)throw new Exception("Clear or stale candidate cleanup failed");
        Debug.Log("QUEST3SPACE_STABLE_MAP_TESTS_PASS same-frame rejection, confirmation, frozen positions, cross-cell spacing, surface coverage, clear, expiry");
    }
    public static void Configure()
    {
        var group=BuildTargetGroup.Android;
        PlayerSettings.companyName="Quest3Space";PlayerSettings.productName="Quest3Space Scanner";
        PlayerSettings.SetApplicationIdentifier(NamedBuildTarget.Android,"space.quest3.scanner");
        PlayerSettings.bundleVersion="0.2.0";PlayerSettings.Android.bundleVersionCode=2;
        PlayerSettings.Android.minSdkVersion=AndroidSdkVersions.AndroidApiLevel32;
        PlayerSettings.Android.targetSdkVersion=AndroidSdkVersions.AndroidApiLevel35;
        PlayerSettings.SetScriptingBackend(NamedBuildTarget.Android,ScriptingImplementation.IL2CPP);
        PlayerSettings.Android.targetArchitectures=AndroidArchitecture.ARM64;
        PlayerSettings.SetApiCompatibilityLevel(NamedBuildTarget.Android,ApiCompatibilityLevel.NET_Standard);
        PlayerSettings.colorSpace=ColorSpace.Linear;PlayerSettings.runInBackground=true;
        PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.Android,false);
        PlayerSettings.SetGraphicsAPIs(BuildTarget.Android,new[]{GraphicsDeviceType.Vulkan});
        PlayerSettings.insecureHttpOption=InsecureHttpOption.AlwaysAllowed;
        PlayerSettings.Android.forceInternetPermission=true;
        QualitySettings.antiAliasing=2;QualitySettings.vSyncCount=0;
        EditorPrefs.SetString("AndroidSdkRoot","E:/Quest3Space/tools/AndroidSDK");
        EditorPrefs.SetString("AndroidNdkRootR16b","E:/Quest3Space/tools/ndk/android-ndk-r27c");
        EditorPrefs.SetString("JdkPath","E:/Quest3Space/tools/jdk17");
        EditorPrefs.SetBool("SdkUseEmbedded",false);EditorPrefs.SetBool("NdkUseEmbedded",false);EditorPrefs.SetBool("JdkUseEmbedded",false);
        UnityEditor.Android.AndroidExternalToolsSettings.sdkRootPath="E:/Quest3Space/tools/AndroidSDK";
        UnityEditor.Android.AndroidExternalToolsSettings.ndkRootPath="E:/Quest3Space/tools/ndk/android-ndk-r27c";
        UnityEditor.Android.AndroidExternalToolsSettings.jdkRootPath="E:/Quest3Space/tools/jdk17";

        var container=(XRGeneralSettingsPerBuildTarget)typeof(XRGeneralSettingsPerBuildTarget).GetMethod("GetOrCreate",BindingFlags.NonPublic|BindingFlags.Static).Invoke(null,null);
        if(!container.HasManagerSettingsForBuildTarget(group))container.CreateDefaultManagerSettingsForBuildTarget(group);
        var general=container.SettingsForBuildTarget(group);general.InitManagerOnStart=true;
        if(!XRPackageMetadataStore.AssignLoader(general.Manager,"UnityEngine.XR.OpenXR.OpenXRLoader",group))throw new Exception("OpenXR loader assignment failed");
        EditorUtility.SetDirty(container);EditorUtility.SetDirty(general);EditorUtility.SetDirty(general.Manager);
        FeatureHelpers.RefreshFeatures(group);
        var xr=OpenXRSettings.GetSettingsForBuildTargetGroup(group);
        if(xr==null)throw new Exception("OpenXR settings not created");
        foreach(var feature in xr.GetFeatures<UnityEngine.XR.OpenXR.Features.OpenXRFeature>()){
            string name=feature.GetType().Name;
            if(new[]{"MetaXRFeature","MetaXRFoveationFeature","MetaQuestFeature","ARSessionFeature","AROcclusionFeature","OculusTouchControllerProfile"}.Contains(name)){
                feature.enabled=true;EditorUtility.SetDirty(feature);Debug.Log("QUEST3SPACE_FEATURE "+name);
            }
        }
        xr.renderMode=OpenXRSettings.RenderMode.SinglePassInstanced;EditorUtility.SetDirty(xr);
        var scene=EditorSceneManager.NewScene(NewSceneSetup.EmptyScene,NewSceneMode.Single);
        var prefab=AssetDatabase.LoadAssetAtPath<GameObject>("Packages/com.meta.xr.sdk.core/Prefabs/OVRCameraRig.prefab");
        if(!prefab)throw new Exception("Meta OVRCameraRig prefab missing");
        var rigGo=(GameObject)PrefabUtility.InstantiatePrefab(prefab);var rig=rigGo.GetComponent<OVRCameraRig>();
        var manager=rigGo.GetComponent<OVRManager>();var so=new SerializedObject(manager);
        so.FindProperty("isInsightPassthroughEnabled").boolValue=true;
        so.FindProperty("requestScenePermissionOnStartup").boolValue=true;
        so.FindProperty("requestPassthroughCameraAccessPermissionOnStartup").boolValue=true;
        so.ApplyModifiedProperties();
        foreach(var cam in rigGo.GetComponentsInChildren<Camera>(true)){cam.clearFlags=CameraClearFlags.SolidColor;cam.backgroundColor=new Color(0,0,0,0);}
        var passthrough=rigGo.AddComponent<OVRPassthroughLayer>();passthrough.overlayType=OVROverlay.OverlayType.Underlay;
        var cameraGo=new GameObject("Meta Passthrough Camera");var access=cameraGo.AddComponent<PassthroughCameraAccess>();access.CameraPosition=PassthroughCameraAccess.CameraPositionType.Left;access.RequestedResolution=new Vector2Int(1280,960);
        var depthGo=new GameObject("Meta Environment Depth");var depth=depthGo.AddComponent<EnvironmentDepthManager>();depth.RemoveHands=false;depth.OcclusionShadersMode=OcclusionShadersMode.SoftOcclusion;
        var scan=new GameObject("Quest3Space").AddComponent<QuestScanner>();scan.Rig=rig;scan.CameraAccess=access;scan.DepthManager=depth;
        scan.DepthCopyShader=Shader.Find("Quest3Space/DepthCopy");scan.RgbCopyShader=Shader.Find("Quest3Space/RgbCopy");scan.VertexColorShader=Shader.Find("Quest3Space/VertexColor");scan.LineShader=Shader.Find("Sprites/Default");
        if(!scan.DepthCopyShader||!scan.RgbCopyShader||!scan.VertexColorShader||!scan.LineShader)throw new Exception("Capture shaders missing");
        var config=OVRProjectConfig.CachedProjectConfig;
        config.targetDeviceTypes=new List<OVRProjectConfig.DeviceType>{OVRProjectConfig.DeviceType.Quest3};
        config.insightPassthroughSupport=OVRProjectConfig.FeatureSupport.Required;
        config.boundaryVisibilitySupport=OVRProjectConfig.FeatureSupport.None;
        config.isPassthroughCameraAccessEnabled=true;config.sceneSupport=OVRProjectConfig.FeatureSupport.Required;
        config.requiresSystemKeyboard=true;config.disableBackups=true;config.enableNSCConfig=false;
        config.minHorizonOsSdkVersion=74;config.targetHorizonOsSdkVersion=203;
        OVRProjectConfig.CommitProjectConfig(config);
        Directory.CreateDirectory("Assets/Scenes");EditorSceneManager.MarkSceneDirty(scene);EditorSceneManager.SaveScene(scene,"Assets/Scenes/Scanner.unity");
        EditorBuildSettings.scenes=new[]{new EditorBuildSettingsScene("Assets/Scenes/Scanner.unity",true)};
        OVRManifestPreprocessor.GenerateOrUpdateAndroidManifest(true);
        // Cleartext LAN transport is application-specific, not a managed Meta feature.
        var manifest=new XmlDocument();string manifestPath="Assets/Plugins/Android/AndroidManifest.xml";manifest.Load(manifestPath);
        const string ns="http://schemas.android.com/apk/res/android";
        var app=(XmlElement)manifest.SelectSingleNode("/manifest/application");app.SetAttribute("usesCleartextTraffic",ns,"true");manifest.Save(manifestPath);
        AssetDatabase.SaveAssets();AssetDatabase.Refresh();
        Debug.Log("QUEST3SPACE_CONFIGURED");
        File.WriteAllText("configured.txt",DateTime.UtcNow.ToString("O"));
    }

    public static async void FixSetup()
    {
        try{
            await OVRProjectSetup.FixAllAsync(BuildTargetGroup.Android);
            OVRManifestPreprocessor.GenerateOrUpdateAndroidManifest(true);
            AssetDatabase.SaveAssets();File.WriteAllText("setup-fixed.txt",DateTime.UtcNow.ToString("O"));
            Debug.Log("QUEST3SPACE_SETUP_FIXED");EditorApplication.Exit(0);
        }catch(Exception e){Debug.LogException(e);EditorApplication.Exit(1);}
    }

    private static void ConfigureControllerVisuals()
    {
        var scene=EditorSceneManager.OpenScene("Assets/Scenes/Scanner.unity");
        var rig=UnityEngine.Object.FindFirstObjectByType<OVRCameraRig>();
        var scan=UnityEngine.Object.FindFirstObjectByType<QuestScanner>();
        if(!rig||!scan)throw new Exception("Scanner / rig missing");
        scan.UiDepthShader=Shader.Find("Quest3Space/DepthUI");
        if(!scan.UiDepthShader)throw new Exception("UI occlusion shader missing");
        var prefab=AssetDatabase.LoadAssetAtPath<GameObject>("Packages/com.meta.xr.sdk.core/Prefabs/OVRControllerPrefab.prefab");
        if(!prefab)throw new Exception("Meta controller prefab missing");
        if(!AssetDatabase.IsValidFolder("Assets/Scanner/ControllerMaterials"))AssetDatabase.CreateFolder("Assets/Scanner","ControllerMaterials");
        foreach(bool left in new[]{true,false}){
            var controller=left?OVRInput.Controller.LTouch:OVRInput.Controller.RTouch;
            var anchor=rig.GetComponentsInChildren<Transform>(true).First(t=>t.name==(left?"LeftControllerAnchor":"RightControllerAnchor"));
            var helper=anchor.GetComponentsInChildren<OVRControllerHelper>(true).FirstOrDefault(h=>h.m_controller==controller);
            if(!helper){var visual=(GameObject)PrefabUtility.InstantiatePrefab(prefab,anchor);visual.name=left?"Quest 3 left controller":"Quest 3 right controller";helper=visual.GetComponent<OVRControllerHelper>();}
            helper.m_controller=controller;helper.m_showState=OVRInput.InputDeviceShowState.Always;helper.showWhenHandsArePoweredByNaturalControllerPoses=true;
            helper.transform.localPosition=Vector3.zero;helper.transform.localRotation=Quaternion.identity;helper.transform.localScale=Vector3.one;
            if(!helper.m_modelMetaTouchPlusLeftController||!helper.m_modelMetaTouchPlusRightController)throw new Exception("Touch Plus model assets missing");
            foreach(var model in new[]{helper.m_modelMetaTouchPlusLeftController,helper.m_modelMetaTouchPlusRightController}){
                foreach(var renderer in model.GetComponentsInChildren<Renderer>(true)){
                    var materials=renderer.sharedMaterials;
                    for(int i=0;i<materials.Length;i++){
                        var original=materials[i];if(!original)continue;
                        string materialPath="Assets/Scanner/ControllerMaterials/"+original.name.Replace(" (Instance)","")+"-depth.mat";
                        if(original.shader.name=="Quest3Space/VertexColor"){original.SetFloat("_IgnoreEnvironmentDepth",1);EditorUtility.SetDirty(original);continue;}
                        var converted=AssetDatabase.LoadAssetAtPath<Material>(materialPath);
                        if(!converted){converted=new Material(Shader.Find("Quest3Space/VertexColor"));AssetDatabase.CreateAsset(converted,materialPath);}
                        converted.SetFloat("_UseTexture",1);
                        converted.SetFloat("_IgnoreEnvironmentDepth",1);
                        converted.mainTexture=original.HasProperty("_BaseMap")?original.GetTexture("_BaseMap"):original.mainTexture;
                        converted.SetColor("_Tint",original.HasProperty("_BaseColor")?original.GetColor("_BaseColor"):Color.white);
                        EditorUtility.SetDirty(converted);materials[i]=converted;
                    }
                    renderer.sharedMaterials=materials;EditorUtility.SetDirty(renderer);
                }
            }
            foreach(var child in helper.GetComponentsInChildren<Transform>(true))child.gameObject.layer=30;
            foreach(var collider in helper.GetComponentsInChildren<Collider>(true))collider.enabled=false;
            EditorUtility.SetDirty(helper);
        }
        if(!GameObject.Find("Controller model light")){
            var lightObject=new GameObject("Controller model light");var light=lightObject.AddComponent<Light>();light.type=LightType.Directional;light.intensity=.9f;light.shadows=LightShadows.None;light.cullingMask=1<<30;lightObject.transform.rotation=Quaternion.Euler(45,-30,0);
        }
        EditorUtility.SetDirty(scan);EditorSceneManager.MarkSceneDirty(scene);EditorSceneManager.SaveScene(scene);
        Debug.Log("QUEST3SPACE_CONTROLLER_VISUALS Touch Plus assets on both controller anchors; depth UI assigned");
    }
    public static void Build()
    {
        ConfigureControllerVisuals();
        var xr=OpenXRSettings.GetSettingsForBuildTargetGroup(BuildTargetGroup.Android);
        foreach(var feature in xr.GetFeatures<UnityEngine.XR.OpenXR.Features.OpenXRFeature>()){
            if(feature.GetType().Name=="ARSessionFeature"){feature.enabled=true;EditorUtility.SetDirty(feature);}
        }
        for(int pass=0;pass<4;pass++){
            var issues=new List<UnityEngine.XR.OpenXR.Features.OpenXRFeature.ValidationRule>();
            UnityEditor.XR.OpenXR.OpenXRProjectValidation.GetCurrentValidationIssues(issues,BuildTargetGroup.Android);
            foreach(var issue in issues.Where(i=>i.error&&i.fixItAutomatic&&i.fixIt!=null)){
                Debug.Log("QUEST3SPACE_OPENXR_FIX "+issue.message);issue.fixIt();
            }
        }
        AssetDatabase.SaveAssets();
        // Generator runs again after any feature fixes, then the custom LAN flag.
        var boundaryConfig=OVRProjectConfig.CachedProjectConfig;
        boundaryConfig.boundaryVisibilitySupport=OVRProjectConfig.FeatureSupport.None;
        OVRProjectConfig.CommitProjectConfig(boundaryConfig);
        OVRManifestPreprocessor.GenerateOrUpdateAndroidManifest(true);
        string path="Assets/Plugins/Android/AndroidManifest.xml";var doc=new XmlDocument();doc.Load(path);
        ((XmlElement)doc.SelectSingleNode("/manifest/application")).SetAttribute("usesCleartextTraffic","http://schemas.android.com/apk/res/android","true");doc.Save(path);
        const string androidNs="http://schemas.android.com/apk/res/android";
        foreach(XmlElement existing in doc.SelectNodes("/manifest/uses-feature").Cast<XmlElement>().ToArray())if(existing.GetAttribute("name",androidNs)=="com.oculus.feature.BOUNDARYLESS_APP")doc.DocumentElement.RemoveChild(existing);
        var boundaryFeature=doc.CreateElement("uses-feature");
        boundaryFeature.SetAttribute("name",androidNs,"com.oculus.feature.BOUNDARYLESS_APP");
        boundaryFeature.SetAttribute("required",androidNs,"true");
        doc.DocumentElement.AppendChild(boundaryFeature);doc.Save(path);
        string manifest=File.ReadAllText(path);
        foreach(string required in new[]{"horizonos.permission.HEADSET_CAMERA","com.oculus.permission.USE_SCENE","android.hardware.vr.headtracking"})if(!manifest.Contains(required))throw new Exception("Generated manifest missing "+required);
        var info=new StringBuilder();
        var tasks=(IEnumerable)typeof(OVRProjectSetup).GetMethod("GetTasks",BindingFlags.NonPublic|BindingFlags.Static).Invoke(null,new object[]{BuildTargetGroup.Android});
        foreach(var task in tasks){var t=task.GetType();var check=t.GetProperty("IsDone").GetValue(task) as Func<BuildTargetGroup,bool>;if(check!=null&&check(BuildTargetGroup.Android))continue;var m=t.GetProperty("Message").GetValue(task);info.AppendLine(m.GetType().GetMethod("GetValue").Invoke(m,new object[]{BuildTargetGroup.Android})?.ToString());}
        File.WriteAllText("setup-outstanding.txt",info.ToString());Debug.Log("QUEST3SPACE_REMAINING_SETUP "+info);
        Directory.CreateDirectory("E:/Quest3Space/builds");
        var report=BuildPipeline.BuildPlayer(new BuildPlayerOptions{scenes=new[]{"Assets/Scenes/Scanner.unity"},locationPathName="E:/Quest3Space/builds/quest3space-0.2.0.apk",target=BuildTarget.Android,options=BuildOptions.Development});
        Debug.Log("QUEST3SPACE_BUILD "+report.summary.result+" bytes="+report.summary.totalSize+" errors="+report.summary.totalErrors);
        if(report.summary.result!=BuildResult.Succeeded)throw new Exception("Quest APK build failed");
    }
}
