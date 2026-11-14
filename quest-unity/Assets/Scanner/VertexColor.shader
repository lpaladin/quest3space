Shader "Quest3Space/VertexColor"
{
    Properties { _MainTex ("Photo texture", 2D) = "white" {} _Tint ("Tint", Color) = (1,1,1,1) _UseTexture ("Photo enabled", Float) = 0 _IgnoreEnvironmentDepth ("Ignore environment depth", Float) = 0 _EnvironmentDepthBias ("Depth tolerance (relative)", Float) = 0.015 _ZWrite ("Write depth", Float) = 1 }
    SubShader { Tags { "RenderType"="Opaque" } Cull Off
        Blend One OneMinusSrcAlpha
        ZWrite [_ZWrite]
        Pass { CGPROGRAM
            #pragma target 3.5
            #pragma vertex vert
            #pragma fragment frag
            #pragma multi_compile_instancing
            #pragma multi_compile _ HARD_OCCLUSION SOFT_OCCLUSION
            #include "UnityCG.cginc"
            #include "DepthCue.cginc"
            #include "Packages/com.meta.xr.sdk.core/Shaders/EnvironmentDepth/BiRP/EnvironmentOcclusionBiRP.cginc"
            sampler2D _MainTex; float _UseTexture; float _EnvironmentDepthBias; float _IgnoreEnvironmentDepth; fixed4 _Tint;
            struct a { float4 vertex:POSITION; fixed4 color:COLOR; float2 uv:TEXCOORD0; UNITY_VERTEX_INPUT_INSTANCE_ID };
            struct v { float4 position:SV_POSITION; fixed4 color:COLOR; float2 uv:TEXCOORD0; float3 worldPos:TEXCOORD1; UNITY_VERTEX_OUTPUT_STEREO };
            v vert(a i){v o;UNITY_SETUP_INSTANCE_ID(i);UNITY_INITIALIZE_VERTEX_OUTPUT_STEREO(o);o.position=UnityObjectToClipPos(i.vertex);o.worldPos=mul(unity_ObjectToWorld,i.vertex).xyz;o.color=i.color;o.uv=i.uv;return o;}
            fixed4 frag(v i):SV_Target{
                UNITY_SETUP_STEREO_EYE_INDEX_POST_VERTEX(i);
                fixed4 color=(_UseTexture>.5?tex2D(_MainTex,i.uv):i.color)*_Tint;
                color.rgb*=QuestDepthBrightness(i.worldPos);
                if(_IgnoreEnvironmentDepth<.5){META_DEPTH_OCCLUDE_OUTPUT_PREMULTIPLY_WORLDPOS(i.worldPos,color,_EnvironmentDepthBias);}
                clip(color.a-.001);
                return color;
            }
        ENDCG }
    }
}
