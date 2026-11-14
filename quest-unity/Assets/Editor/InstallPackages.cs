using System;
using System.IO;
using UnityEditor;
using UnityEditor.PackageManager;
using UnityEditor.PackageManager.Requests;
using UnityEngine;

public static class InstallPackages
{
    private static AddAndRemoveRequest request;
    public static void Run()
    {
        request=Client.AddAndRemove(new[]{
            "file:E:/Quest3Space/tools/packages/core203/package",
            "file:E:/Quest3Space/tools/packages/mruk203/package",
            "com.unity.xr.management@4.5.1",
            "com.unity.xr.openxr@1.15.1",
            "com.unity.xr.meta-openxr@2.1.0",
            "com.unity.ugui@2.0.0"
        });
        EditorApplication.update+=Poll;
    }
    private static void Poll()
    {
        if(!request.IsCompleted)return;
        EditorApplication.update-=Poll;
        if(request.Status==StatusCode.Failure){Debug.LogError(request.Error.message);EditorApplication.Exit(1);return;}
        File.WriteAllText(Path.Combine(Directory.GetCurrentDirectory(),"packages-ready.txt"),DateTime.UtcNow.ToString("O"));
        Debug.Log("QUEST3SPACE_PACKAGES_READY");EditorApplication.Exit(0);
    }
}
