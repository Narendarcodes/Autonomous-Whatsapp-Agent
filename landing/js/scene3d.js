// scene3d.js — real 3D scenes: hero "signal core" + CTA wireframe torus knot
import * as THREE from "three";

/* ---------- GLSL simplex noise (Ashima / Stefan Gustavson) ---------- */
const SNOISE = /* glsl */ `
vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
float snoise(vec3 v){
  const vec2 C=vec2(1.0/6.0,1.0/3.0);
  const vec4 D=vec4(0.0,0.5,1.0,2.0);
  vec3 i=floor(v+dot(v,C.yyy));
  vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz);
  vec3 l=1.0-g;
  vec3 i1=min(g.xyz,l.zxy);
  vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx;
  vec3 x2=x0-i2+C.yyy;
  vec3 x3=x0-D.yyy;
  i=mod289(i);
  vec4 p=permute(permute(permute(
      i.z+vec4(0.0,i1.z,i2.z,1.0))
    + i.y+vec4(0.0,i1.y,i2.y,1.0))
    + i.x+vec4(0.0,i1.x,i2.x,1.0));
  float n_=0.142857142857;
  vec3 ns=n_*D.wyz-D.xzx;
  vec4 j=p-49.0*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z);
  vec4 y_=floor(j-7.0*x_);
  vec4 x=x_*ns.x+ns.yyyy;
  vec4 y=y_*ns.x+ns.yyyy;
  vec4 h=1.0-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy);
  vec4 b1=vec4(x.zw,y.zw);
  vec4 s0=floor(b0)*2.0+1.0;
  vec4 s1=floor(b1)*2.0+1.0;
  vec4 sh=-step(h,vec4(0.0));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy;
  vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x);
  vec3 p1=vec3(a0.zw,h.y);
  vec3 p2=vec3(a1.xy,h.z);
  vec3 p3=vec3(a1.zw,h.w);
  vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
  p0*=norm.x;p1*=norm.y;p2*=norm.z;p3*=norm.w;
  vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0);
  m=m*m;
  return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
`;

const BLOB_VERT = /* glsl */ `
uniform float uTime;
varying vec3 vNormal;
varying vec3 vViewDir;
${"" /* noise injected below via template concat */}
float fbm3(vec3 p){
  float v=0.0,a=0.5;
  for(int i=0;i<4;i++){v+=a*snoise(p);p*=2.05;a*=0.5;}
  return v;
}
void main(){
  float n = fbm3(normal * 1.4 + vec3(0.0, uTime * 0.18, uTime * 0.12));
  float disp = n * 0.22;
  vec3 newPos = position + normal * disp;
  // recompute approximate normal via neighbor sampling for better shading
  vec3 tangent = normalize(cross(normal, vec3(0.0, 1.0, 0.001)));
  vec3 bitangent = normalize(cross(normal, tangent));
  float eps = 0.08;
  vec3 pT = position + tangent * eps;
  vec3 pB = position + bitangent * eps;
  float nT = fbm3(normalize(pT) * 1.4 + vec3(0.0, uTime * 0.18, uTime * 0.12));
  float nB = fbm3(normalize(pB) * 1.4 + vec3(0.0, uTime * 0.18, uTime * 0.12));
  vec3 newPT = pT + normalize(pT) * nT * 0.22;
  vec3 newPB = pB + normalize(pB) * nB * 0.22;
  vec3 newNormal = normalize(cross(newPT - newPos, newPB - newPos));
  if (dot(newNormal, normal) < 0.0) newNormal = -newNormal;

  vec4 mvPosition = modelViewMatrix * vec4(newPos, 1.0);
  vNormal = normalize(normalMatrix * newNormal);
  vViewDir = normalize(-mvPosition.xyz);
  gl_Position = projectionMatrix * mvPosition;
}
`.replace("${\"\" /* noise injected below via template concat */}", "");

const BLOB_FRAG = /* glsl */ `
precision highp float;
uniform float uTime;
uniform vec3 uAccent;
uniform vec3 uDeep;
varying vec3 vNormal;
varying vec3 vViewDir;
void main(){
  float fresnel = pow(1.0 - max(dot(vNormal, vViewDir), 0.0), 2.2);
  float lambert = max(dot(vNormal, normalize(vec3(0.6, 0.8, 0.75))), 0.0);
  vec3 base = mix(uDeep * 0.35, uDeep, lambert);
  vec3 col = base + uAccent * fresnel * 1.35;
  col += uAccent * 0.06 * sin(uTime * 1.4); // breathing pulse
  gl_FragColor = vec4(col, 1.0);
}
`;

function makeRenderer(canvas) {
  try {
    return new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      alpha: true,
      powerPreference: "high-performance",
    });
  } catch {
    return null;
  }
}

const ACCENT = new THREE.Color("#25d366");
const DEEP = new THREE.Color("#12805c");

/**
 * Hero "signal core": noise-displaced blob + particle swarm.
 * Mouse parallax tilt + scroll spin handled externally via returned api.
 */
export function createSignalCore(canvas, opts = {}) {
  const renderer = makeRenderer(canvas);
  if (!renderer) {
    canvas.style.display = "none";
    return null;
  }

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const mobile = window.innerWidth < 768;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 50);
  camera.position.set(0, 0, 5.2);

  // --- blob ---
  const uniforms = {
    uTime: { value: 0 },
    uAccent: { value: ACCENT },
    uDeep: { value: DEEP },
  };
  const blobMat = new THREE.ShaderMaterial({
    vertexShader: SNOISE + BLOB_VERT_RAW(),
    fragmentShader: BLOB_FRAG,
    uniforms,
  });
  const blob = new THREE.Mesh(
    new THREE.IcosahedronGeometry(1.35, mobile ? 24 : 40),
    blobMat
  );

  // --- particles ---
  const COUNT = mobile ? 350 : 800;
  const positions = new Float32Array(COUNT * 3);
  const speeds = new Float32Array(COUNT);
  for (let i = 0; i < COUNT; i++) {
    // random point in spherical shell 2.0 – 3.4
    const r = 2.0 + Math.random() * 1.4;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);
    speeds[i] = 0.15 + Math.random() * 0.5;
  }
  const pGeo = new THREE.BufferGeometry();
  pGeo.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  const pMat = new THREE.PointsMaterial({
    color: ACCENT,
    size: 0.022,
    transparent: true,
    opacity: 0.65,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    sizeAttenuation: true,
  });
  const particles = new THREE.Points(pGeo, pMat);

  const group = new THREE.Group();
  group.add(blob);
  group.add(particles);
  scene.add(group);

  const clock = new THREE.Clock();
  let visible = true;
  let onScreen = true;
  let rafId = null;
  const mouseTarget = { x: 0, y: 0 };

  function resize() {
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.75);
    renderer.setPixelRatio(dpr);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function tick() {
    rafId = requestAnimationFrame(tick);
    if (!visible || !onScreen) return;
    const t = clock.getElapsedTime();
    uniforms.uTime.value = t;

    if (!reduced) {
      blob.rotation.y = t * 0.12;
      particles.rotation.y = t * 0.05;
      particles.rotation.z = t * 0.02;

      // ease group toward mouse tilt + external scroll offsets
      group.rotation.x += (mouseTarget.y * 0.35 + state.scrollRotX - group.rotation.x) * 0.045;
      group.rotation.y += (mouseTarget.x * 0.55 + state.scrollRotY - group.rotation.y) * 0.045;
      const s = state.scale;
      group.scale.set(s, s, s);
    }

    renderer.render(scene, camera);
  }

  const state = { scrollRotX: 0, scrollRotY: 0, scale: 1 };

  window.addEventListener("pointermove", (e) => {
    if (reduced) return;
    mouseTarget.x = (e.clientX / window.innerWidth) * 2 - 1;
    mouseTarget.y = -((e.clientY / window.innerHeight) * 2 - 1);
  });

  const io = new IntersectionObserver(([en]) => (onScreen = en.isIntersecting), { threshold: 0 });
  io.observe(canvas);
  document.addEventListener("visibilitychange", () => (visible = !document.hidden));
  window.addEventListener("resize", resize);

  resize();
  tick();

  return {
    state,
    destroy() {
      cancelAnimationFrame(rafId);
      renderer.dispose();
    },
  };
}

// raw vertex shader without placeholder confusion
function BLOB_VERT_RAW() {
  return `
uniform float uTime;
varying vec3 vNormal;
varying vec3 vViewDir;
float fbm3(vec3 p){
  float v=0.0,a=0.5;
  for(int i=0;i<4;i++){v+=a*snoise(p);p*=2.05;a*=0.5;}
  return v;
}
void main(){
  float n = fbm3(normal * 1.4 + vec3(0.0, uTime * 0.18, uTime * 0.12));
  vec3 newPos = position + normal * n * 0.22;
  vec3 tangent = normalize(cross(normal, vec3(0.0, 1.0, 0.001)));
  vec3 bitangent = normalize(cross(normal, tangent));
  float eps = 0.08;
  vec3 pT = position + tangent * eps;
  vec3 pB = position + bitangent * eps;
  float nT = fbm3(normalize(pT) * 1.4 + vec3(0.0, uTime * 0.18, uTime * 0.12));
  float nB = fbm3(normalize(pB) * 1.4 + vec3(0.0, uTime * 0.18, uTime * 0.12));
  vec3 newPT = pT + normalize(pT) * nT * 0.22;
  vec3 newPB = pB + normalize(pB) * nB * 0.22;
  vec3 newNormal = normalize(cross(newPT - newPos, newPB - newPos));
  if (dot(newNormal, normal) < 0.0) newNormal = -newNormal;

  vec4 mvPosition = modelViewMatrix * vec4(newPos, 1.0);
  vNormal = normalize(normalMatrix * newNormal);
  vViewDir = normalize(-mvPosition.xyz);
  gl_Position = projectionMatrix * mvPosition;
}`;
}

/**
 * CTA finale: rotating wireframe torus knot, additive feel.
 */
export function createCtaScene(canvas) {
  const renderer = makeRenderer(canvas);
  if (!renderer) {
    canvas.style.display = "none";
    return null;
  }
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 50);
  camera.position.set(0, 0, 6);

  const geo = new THREE.TorusKnotGeometry(1.6, 0.45, 140, 20);
  const wire = new THREE.WireframeGeometry(geo);
  const mat = new THREE.LineBasicMaterial({
    color: ACCENT,
    transparent: true,
    opacity: 0.16,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });
  const knot = new THREE.LineSegments(wire, mat);
  scene.add(knot);

  // faint inner solid for depth occlusion
  const innerMat = new THREE.MeshBasicMaterial({
    color: 0x050807,
    transparent: true,
    opacity: 0.55,
  });
  const inner = new THREE.Mesh(geo, innerMat);
  knot.add(inner);

  const clock = new THREE.Clock();
  let visible = true;
  let onScreen = true;
  let rafId = null;

  function resize() {
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    renderer.setPixelRatio(dpr);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function tick() {
    rafId = requestAnimationFrame(tick);
    if (!visible || !onScreen) return;
    const t = clock.getElapsedTime();
    if (!reduced) {
      knot.rotation.x = t * 0.16;
      knot.rotation.y = t * 0.11;
    }
    renderer.render(scene, camera);
  }

  const io = new IntersectionObserver(([en]) => (onScreen = en.isIntersecting), { threshold: 0 });
  io.observe(canvas);
  document.addEventListener("visibilitychange", () => (visible = !document.hidden));
  window.addEventListener("resize", resize);

  resize();
  tick();

  return {
    destroy() {
      cancelAnimationFrame(rafId);
      renderer.dispose();
    },
  };
}
