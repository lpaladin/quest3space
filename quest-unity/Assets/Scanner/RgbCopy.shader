Shader "Quest3Space/RgbCopy"
{
    Properties { _MainTex ("Camera image", 2D) = "white" {} }
    SubShader { Cull Off ZWrite Off ZTest Always
        Pass { CGPROGRAM
            #pragma vertex vert_img
            #pragma fragment frag
            #include "UnityCG.cginc"
            sampler2D _MainTex;
            fixed4 frag(v2f_img i) : SV_Target {
                // CPU image row zero is the top row. Unity's texture UV is bottom-up.
                return tex2D(_MainTex,float2(i.uv.x,1-i.uv.y));
            }
        ENDCG }
    }
}
