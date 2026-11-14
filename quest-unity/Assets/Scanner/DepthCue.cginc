#ifndef QUEST3SPACE_DEPTH_CUE
#define QUEST3SPACE_DEPTH_CUE
// Gentle distance cue shared by all virtual content; never modifies passthrough.
inline float QuestDepthBrightness(float3 worldPosition)
{
    float distanceMeters=distance(worldPosition,_WorldSpaceCameraPos.xyz);
    float t=saturate((distanceMeters-.35)/3.65);
    return lerp(1.0,.84,t*t*(3.0-2.0*t));
}
#endif
