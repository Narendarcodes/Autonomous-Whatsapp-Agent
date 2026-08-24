// phone-css3d.js — pro-rendered phone frame (PNG asset from the facilpay
// clone, same technique as whatsapp.com / facilpay.io: image frame + live
// HTML screen) with GSAP-scrubbed WhatsApp chat.

export function createPhone(container, opts = {}) {
  const scale = opts.scale ?? 0.8;

  container.classList.add("ph-scene");
  container.innerHTML = `
    <div class="ph-wrapper" style="width:${Math.round(340 * scale)}px">
      <div class="ph-device">
        <img class="ph-img" src="assets/phone-frame.png" alt="" draggable="false" />
        <div class="ph-screen">
          <div class="chat-ui"></div>
          <div class="ph-glass"></div>
        </div>
      </div>
    </div>`;

  const device = container.querySelector(".ph-device");
  const chatUI = container.querySelector(".chat-ui");

  /* ---------- drag rotation (Apple fluid: 1:1, clamped, eases home) ---------- */
  const LIMITS = { minX: -7, maxX: 11, minY: -22, maxY: 14 };
  const BASE = { x: 2, y: -12 };
  let rot = { ...BASE };
  let dragging = false;
  let phase = Math.random() * Math.PI * 2;
  const dragStart = { x: 0, y: 0, rx: 0, ry: 0 };
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const interactive = opts.interactive !== false && !reduced;

  const clampRot = () => {
    rot.x = Math.max(LIMITS.minX, Math.min(LIMITS.maxX, rot.x));
    rot.y = Math.max(LIMITS.minY, Math.min(LIMITS.maxY, rot.y));
  };

  if (interactive) {
    container.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 && e.pointerType === "mouse") return;
      dragging = true;
      Object.assign(dragStart, { x: e.clientX, y: e.clientY, rx: rot.x, ry: rot.y });
      container.setPointerCapture(e.pointerId);
    });
    container.addEventListener("pointermove", (e) => {
      if (!dragging) return;
      // 1:1 tracking — screen glued to pointer
      rot.y = dragStart.ry + (e.clientX - dragStart.x) * 0.28;
      rot.x = dragStart.rx - (e.clientY - dragStart.y) * 0.14;
      clampRot();
    });
    const end = () => (dragging = false);
    container.addEventListener("pointerup", end);
    container.addEventListener("pointercancel", end);
  }

  function tick() {
    requestAnimationFrame(tick);
    if (reduced) return;
    phase += 0.02; // idle sway
    if (!dragging) {
      rot.x += (BASE.x - rot.x) * 0.05; // ease home
      rot.y += (BASE.y - rot.y) * 0.05;
    }
    const swayY = dragging ? 0 : Math.sin(phase) * 7;
    device.style.transform = `rotateY(${rot.y + swayY}deg) rotateX(${rot.x}deg)`;
  }
  tick();

  /* ---------- WhatsApp chat ---------- */
  const SCRIPT = [
    { t: "Hi! Are you open for a consult call this Friday?", out: false },
    { t: "Unknown sender held for review", sys: true },
    { t: "Access granted by owner", sys: true, ok: true },
    { t: "Hey Ananya! Let me check… 📅", out: true },
    { t: "Friday's open after 2pm. Booked you for 3:00 PM IST — invite sent ✅", out: true },
    { t: "Perfect, see you then! 🙌", out: false },
  ];

  let lastState = -1;
  function renderChat(state) {
    const n = Math.round(Math.min(state, SCRIPT.length));
    if (n === lastState) return; // only redraw on step change
    lastState = n;
    let html = `
      <div class="wa-header">
        <span class="wa-avatar">A</span>
        <span class="wa-name">Ananya Rao</span>
        <span class="wa-status">online</span>
      </div>
      <div class="wa-banner">🔒 Messages are end-to-end encrypted</div>
      <div class="wa-body">`;
    for (let i = 0; i < n; i++) {
      const m = SCRIPT[i];
      if (m.sys) {
        html += `<div class="wa-sys${m.ok ? " wa-sys--ok" : ""}">${m.ok ? "✓ " : ""}${m.t}</div>`;
      } else {
        const time = m.out ? '10:22 <b class="ticks">✓✓</b>' : "10:21";
        html += `<div class="wa-bubble ${m.out ? "wa-bubble--out" : "wa-bubble--in"}">${m.t}<span class="wa-time">${time}</span></div>`;
      }
    }
    if (state > 3.2 && state < 4.4) {
      html += `<div class="wa-typing"><span></span><span></span><span></span></div>`;
    }
    html += `</div><div class="wa-compose"><span>Message</span><i class="wa-send"></i></div>`;
    chatUI.innerHTML = html;
  }
  renderChat(SCRIPT.length);

  return {
    setChatProgress(p) {
      renderChat(p);
    },
  };
}
