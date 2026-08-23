// shader-bg.js — Three.js fullscreen fbm-noise aurora background
// Green-on-black flowing noise; mouse parallax; pauses off-screen/hidden.
import * as THREE from "three";

const VERT = /* glsl */ `
void main() {
  gl_Position = vec4(position, 1.0);
}
`;

const FRAG = /* glsl */ `
precision highp float;
uniform vec2 uRes;
uniform float uTime;
uniform vec2 uMouse;
uniform float uIntensity;

// hash + value noise + 2D fbm
float hash(vec2 p) {
  p = fract(p * vec2(234.34, 435.345));
  p += dot(p, p + 34.23);
  return fract(p.x * p.y);
}
float noise(vec2 p) {
  vec2 i = floor(p), f = fract(p);
  f = f * f * (3.0 - 2.0 * f);
  float a = hash(i);
  float b = hash(i + vec2(1.0, 0.0));
  float c = hash(i + vec2(0.0, 1.0));
  float d = hash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
float fbm(vec2 p) {
  float v = 0.0, a = 0.5;
  mat2 rot = mat2(0.8, 0.6, -0.6, 0.8);
  for (int i = 0; i < 5; i++) {
    v += a * noise(p);
    p = rot * p * 2.02;
    a *= 0.55;
  }
  return v;
}

void main() {
  vec2 uv = gl_FragCoord.xy / uRes.xy;
  vec2 p = uv;
  p.x *= uRes.x / uRes.y;

  // mouse parallax drift
  vec2 parallax = uMouse * 0.08;

  // domain-warped flow field
  float t = uTime * 0.06;
  vec2 q = p * 1.6 + parallax;
  float warp = fbm(q + t);
  float f = fbm(q + warp * 1.4 + vec2(t * 0.7, -t * 0.4));

  // shape into aurora bands rising from bottom
  float band = f * smoothstep(0.0, 0.9, uv.y * 0.85 + 0.15);
  float glow = pow(band, 2.6) * uIntensity;

  vec3 base = vec3(0.016, 0.031, 0.024);           // near-black green tint
  vec3 accent = vec3(0.145, 0.827, 0.4);            // #25D366-ish
  vec3 deep = vec3(0.05, 0.35, 0.18);               // mid tone

  vec3 col = base;
  col += deep * glow * 0.85;
  col += accent * pow(glow, 1.6) * 0.55;

  // vignette to keep text zones dark
  float vig = smoothstep(1.25, 0.35, distance(uv, vec2(0.5, 0.42)));
  col *= vig;

  // subtle grain
  col += (hash(gl_FragCoord.xy + uTime) - 0.5) * 0.028;

  gl_FragColor = vec4(col, 1.0);
}
`;

export function createShaderBackground(canvas, opts = {}) {
  const intensity = opts.intensity ?? 1.0;

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: false,
      alpha: false,
      powerPreference: "low-power",
    });
  } catch (e) {
    canvas.style.background =
      "radial-gradient(ellipse at 50% 100%, rgba(37,211,102,.14), transparent 60%), #050807";
    return null;
  }

  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);

  const uniforms = {
    uRes: { value: new THREE.Vector2(1, 1) },
    uTime: { value: 0 },
    uMouse: { value: new THREE.Vector2(0, 0) },
    uIntensity: { value: intensity },
  };

  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(2, 2),
    new THREE.ShaderMaterial({ vertexShader: VERT, fragmentShader: FRAG, uniforms })
  );
  scene.add(mesh);

  const clock = new THREE.Clock();
  let visible = true;
  let onScreen = true;
  let rafId = null;
  const mouseTarget = new THREE.Vector2(0, 0);
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function resize() {
    const w = canvas.clientWidth || window.innerWidth;
    const h = canvas.clientHeight || window.innerHeight;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5); // capped for perf
    renderer.setPixelRatio(dpr);
    renderer.setSize(w, h, false);
    uniforms.uRes.value.set(w * dpr, h * dpr);
  }

  function tick() {
    rafId = requestAnimationFrame(tick);
    if (!visible || !onScreen) return;
    uniforms.uTime.value = reduced ? 12.0 : clock.getElapsedTime();
    // ease mouse toward target
    uniforms.uMouse.value.lerp(mouseTarget, 0.04);
    renderer.render(scene, camera);
  }

  function start() {
    resize();
    if (!reduced) window.addEventListener("resize", resize);
    document.addEventListener("visibilitychange", () => {
      visible = !document.hidden;
    });

    // pause when canvas scrolled out of view
    const io = new IntersectionObserver(
      ([entry]) => {
        onScreen = entry.isIntersecting;
      },
      { threshold: 0 }
    );
    io.observe(canvas);

    // gentle mouse parallax
    if (!reduced) {
      window.addEventListener("pointermove", (e) => {
        mouseTarget.set(
          (e.clientX / window.innerWidth) * 2 - 1,
          (e.clientY / window.innerHeight) * 2 - 1
        );
      });
    }

    tick();
  }

  start();
  return { destroy() { cancelAnimationFrame(rafId); renderer.dispose(); } };
}
