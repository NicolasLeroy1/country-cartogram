varying vec3 noisePosition;
uniform float surfaceNoise,noiseFrequency,noiseWorldScale;
vec3 cartogramGradient(vec3 p){
 vec3 h=vec3(dot(p,vec3(127.1,311.7,74.7)),dot(p,vec3(269.5,183.3,246.1)),dot(p,vec3(113.5,271.9,124.6)));
 return normalize(fract(sin(h)*43758.5453)*2.0-1.0);
}
float cartogramPerlin(vec3 p){
 vec3 cell=floor(p),f=fract(p),u=f*f*f*(f*(f*6.0-15.0)+10.0);
 float n000=dot(cartogramGradient(cell),f);
 float n100=dot(cartogramGradient(cell+vec3(1,0,0)),f-vec3(1,0,0));
 float n010=dot(cartogramGradient(cell+vec3(0,1,0)),f-vec3(0,1,0));
 float n110=dot(cartogramGradient(cell+vec3(1,1,0)),f-vec3(1,1,0));
 float n001=dot(cartogramGradient(cell+vec3(0,0,1)),f-vec3(0,0,1));
 float n101=dot(cartogramGradient(cell+vec3(1,0,1)),f-vec3(1,0,1));
 float n011=dot(cartogramGradient(cell+vec3(0,1,1)),f-vec3(0,1,1));
 float n111=dot(cartogramGradient(cell+vec3(1,1,1)),f-vec3(1,1,1));
 return mix(mix(mix(n000,n100,u.x),mix(n010,n110,u.x),u.y),mix(mix(n001,n101,u.x),mix(n011,n111,u.x),u.y),u.z);
}
