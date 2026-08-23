// phone3d.js — a real 3D phone model (not a flat plane):
// extruded rounded body, recessed screen with live CanvasTexture,
// camera island, floating idle motion. Two instances: hero + demo.
import * as THREE from "three";

const INK = 0x1c1e21;
const CREAM = 0xfcf5eb;
const GREEN = 0x25d366;

/** Draws the WhatsApp chat screen onto an offscreen canvas → texture.
 *  `drawState(n)` renders the conversation up to step n so scroll can scrub it. */
function makeScreenTexture() {
  const c = document.createElement("canvas");
  c.width = 512;
  c.height = 1024;
  const g = c.getContext("2d");

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // conversation steps: each entry [text, out?, sys?]
  const SCRIPT = [
    { t: "Hi! Are you open for a consult this Friday?", out: false },
    { t: "Unknown sender held for review", sys: true },
    { t: "Access granted by owner", sys: true, ok: true },
    { t: "Hey Ananya! Let me check…", out: true },
    { t: "Booked Friday 3:00 PM IST ✓ invite sent", out: true },
    { t: "Perfect, see you then!", out: false },
  ];

  function draw(state) {
    // wallpaper
    g.fillStyle = "#ece5dd";
    g.fillRect(0, 0, c.width, c.height);

    // header
    g.fillStyle = "#075e54";
    g.fillRect(0, 0, c.width, 110);
    g.fillStyle = "#ffffff";
    g.beginPath(); g.arc(70, 55, 30, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#075e54";
    g.font = "600 28px Inter, sans-serif";
    g.textBaseline = "middle";
    g.fillText("A", 62, 56);
    g.fillStyle = "#ffffff";
    g.font = "500 26px Inter, sans-serif";
    g.fillText("Ananya Rao", 118, 44);
    g.fillStyle = "rgba(255,255,255,.72)";
    g.font = "20px Inter, sans-serif";
    g.fillText("online", 118, 78);

    // encryption banner
    g.fillStyle = "#fff5c4";
    roundRect(g, 40, 132, c.width - 80, 52, 12); g.fill();
    g.fillStyle = "#54636d";
    g.font = "19px Inter, sans-serif";
    g.textBaseline = "alphabetic";
    g.fillText("🔒 Messages are end-to-end encrypted", 66, 165);

    let y = 216;
    for (let i = 0; i < Math.min(state, SCRIPT.length); i++) {
      const m = SCRIPT[i];
      if (m.sys) {
        const w = g.measureText(m.t).width + 60;
        g.fillStyle = m.ok ? "rgba(37,211,102,.16)" : "rgba(255,255,255,.92)";
        roundRect(g, (c.width - w) / 2, y, w, 46, 23); g.fill();
        g.fillStyle = m.ok ? "#1c7a45" : "#5e5e5e";
        g.font = "19px Inter, sans-serif";
        g.fillText((m.ok ? "✓ " : "") + m.t, (c.width - w) / 2 + 30, y + 30);
        y += 70;
        continue;
      }
      g.font = "22px Inter, sans-serif";
      const tw = Math.min(g.measureText(m.t).width, 380);
      const bw = tw + 48;
      const bx = m.out ? c.width - bw - 36 : 36;
      g.fillStyle = m.out ? "#d9fdd3" : "#ffffff";
      roundRect(g, bx, y, bw, 84, 18); g.fill();
      // tail nub
      g.beginPath();
      if (m.out) { g.moveTo(bx + bw - 14, y + 82); g.lineTo(bx + bw - 2, y + 96); g.lineTo(bx + bw - 14, y + 96); }
      else { g.moveTo(bx + 2, y + 82); g.lineTo(bx + 14, y + 96); g.lineTo(bx + 2, y + 96); }
      g.fill();
      // text (wrap naive single line)
      g.fillStyle = "#1c1e21";
      g.fillText(m.t.slice(0, 34), bx + 22, y + 38);
      if (m.t.length > 34) g.fillText(m.t.slice(34), bx + 22, y + 66);
      else {
        g.fillStyle = "#8696a0"; g.font = "17px Inter, sans-serif";
        g.fillText(m.out ? "10:22 ✓✓" : "10:2" + (i % 9), bx + 22 + tw - 40, y + 68);
      }
      y += 112;
    }

    // typing dots when mid-conversation
    if (state >= 3 && state < 4.5) {
      g.fillStyle = "#25d366";
      for (let i = 0; i < 3; i++) {
        g.beginPath(); g.arc(420 - i * 34, y + 20, 8, 0, Math.PI * 2); g.fill();
      }
    }

    // compose bar
    g.fillStyle = "#f0f2f5";
    g.fillRect(0, c.height - 96, c.width, 96);
    g.fillStyle = "#ffffff";
    roundRect(g, 36, c.height - 76, 360, 56, 28); g.fill();
    g.fillStyle = "#25d366";
    g.beginPath(); g.arc(c.width - 66, c.height - 48, 32, 0, Math.PI * 2); g.fill();
    // paper plane hint
    g.strokeStyle = "#ffffff"; g.lineWidth = 5;
    g.beginPath();
    g.moveTo(c.width - 80, c.height - 58); g.lineTo(c.width - 50, c.height - 44); g.lineTo(c.width - 74, c.height - 34);
    g.stroke();

    tex.needsUpdate = true;
  }

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  draw(SCRIPT.length);

  return { texture: tex, draw };
}

export function createPhone3D(canvas, opts = {}) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch {
    return null;
  }
  renderer.outputColorSpace = THREE.SRGBColorSpace;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 0.5, 0.1, 50);
  camera.position.set(0, 0, 7.2);

  // lights — soft studio, matches cream world
  scene.add(new THREE.AmbientLight(0xffffff, 1.15));
  const key = new THREE.DirectionalLight(0xfff6e8, 1.6);
  key.position.set(3, 4, 6);
  scene.add(key);
  const rim = new THREE.DirectionalLight(0xdfe9ff, 0.7);
  rim.position.set(-4, -2, -3);
  scene.add(rim);

  // ---- phone group ----
  const phone = new THREE.Group();
  const W = 2.35, H = 4.9, D = 0.24, R = 0.42;

  // body: extruded rounded-rect shape
  const shape = new THREE.Shape();
  const hw = W / 2 - R, hh = H / 2 - R;
  shape.absarc(hw, hh, R, 0, Math.PI / 2);
  shape.absarc(-hw, hh, R, Math.PI / 2, Math.PI);
  shape.absarc(-hw, -hh, R, Math.PI, Math.PI * 1.5);
  shape.absarc(hw, -hh, R, Math.PI * 1.5, Math.PI * 2);
  const bodyGeo = new THREE.ExtrudeGeometry(shape, {
    depth: D - 0.06,
    bevelEnabled: true,
    bevelThickness: 0.03,
    bevelSize: 0.03,
    bevelSegments: 4,
    curveSegments: 24,
  });
  bodyGeo.center();
  const bodyMat = new THREE.MeshPhysicalMaterial({
    color: INK,
    roughness: 0.32,
    metalness: 0.55,
    clearcoat: 0.6,
    clearcoatRoughness: 0.25,
  });
  const body = new THREE.Mesh(bodyGeo, bodyMat);
  phone.add(body);

  // screen: recessed slightly in front of body face
  const screenTex = makeScreenTexture();
  const screenW = W - 0.16, screenH = H - 0.16;
  const screenGeo = new THREE.PlaneGeometry(screenW, screenH);
  const screenMat = new THREE.MeshBasicMaterial({
    map: screenTex.texture,
    toneMapped: false,
  });
  const screen = new THREE.Mesh(screenGeo, screenMat);
  screen.position.z = D / 2 + 0.005;
  phone.add(screen);

  // glass overlay (subtle sheen)
  const glass = new THREE.Mesh(
    new THREE.PlaneGeometry(screenW, screenH),
    new THREE.MeshPhysicalMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.06,
      roughness: 0.05,
      metalness: 0,
      transmission: 0,
    })
  );
  glass.position.z = screen.position.z + 0.002;
  phone.add(glass);

  // camera island (back)
  const island = new THREE.Mesh(
    new THREE.RoundedBox ? new THREE.BoxGeometry(0.7, 0.7, 0.06) : new THREE.BoxGeometry(0.7, 0.7, 0.06),
    new THREE.MeshPhysicalMaterial({ color: 0x111214, roughness: 0.4, metalness: 0.6 })
  );
  island.position.set(-W / 2 + 0.62, H / 2 - 0.62, -D / 2 - 0.02);
  phone.add(island);
  const lens = new THREE.Mesh(
    new THREE.CylinderGeometry(0.16, 0.16, 0.05, 24),
    new THREE.MeshPhysicalMaterial({ color: 0x06070a, roughness: 0.15, metalness: 0.8 })
  );
  lens.rotation.x = Math.PI / 2;
  lens.position.set(island.position.x + 0.12, island.position.y - 0.12, island.position.z - 0.04);
  phone.add(lens);

  // side button
  const btn = new THREE.Mesh(
    new THREE.BoxGeometry(0.05, 0.5, 0.08),
    bodyMat
  );
  btn.position.set(W / 2 + 0.02, 0.4, 0);
  phone.add(btn);

  scene.add(phone);

  const clock = new THREE.Clock();
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let visible = true, onScreen = true, rafId = null;
  const mouseT = { x: 0, y: 0 };
  const state = opts.state || {};
  let baseRotY = opts.baseRotY ?? -0.12;

  function resize() {
    const w = canvas.clientWidth || 480, h = canvas.clientHeight || 720;
    const dpr = Math.min(devicePixelRatio || 1, 2);
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
      // idle float + gentle sway, plus external scroll/mouse targets
      const targetY = baseRotY + mouseT.x * 0.28 + (state.rotY || 0);
      const targetX = -mouseT.y * 0.14 + (state.rotX || 0);
      phone.rotation.y += (targetY - phone.rotation.y) * 0.06;
      phone.rotation.x += (targetX - phone.rotation.x) * 0.06;
      phone.position.y = Math.sin(t * 0.9) * 0.07 + (state.posY || 0);
    }
    renderer.render(scene, camera);
  }

  addEventListener("pointermove", (e) => {
    mouseT.x = (e.clientX / innerWidth) * 2 - 1;
    mouseT.y = (e.clientY / innerHeight) * 2 - 1;
  });
  new IntersectionObserver(([en]) => (onScreen = en.isIntersecting), { threshold: 0 }).observe(canvas);
  document.addEventListener("visibilitychange", () => (visible = !document.hidden));
  addEventListener("resize", resize);

  resize();
  tick();

  return {
    state,
    setChatProgress(p) {
      // scrub the conversation on the phone's own texture (0 → SCRIPT.length)
      screenTex.draw(Math.round(p));
    },
    destroy() { cancelAnimationFrame(rafId); renderer.dispose(); },
  };
}
