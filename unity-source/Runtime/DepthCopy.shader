Shader "Quest3Space/DepthCopy"
{
    SubShader { Cull Off ZWrite Off ZTest Always
        Pass { CGPROGRAM
            #pragma target 3.5
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"
            UNITY_DECLARE_TEX2DARRAY(_EnvironmentDepthTexture);
            float frag(v2f_img i) : SV_Target {
                return UNITY_SAMPLE_TEX2DARRAY(_EnvironmentDepthTexture,float3(i.uv,0)).r;
            }
        ENDCG }
    }
}
