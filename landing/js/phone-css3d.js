// phone-css3d.js — photoreal CSS 3D iPhone (ported from Styly IO's MIT-licensed
// IPhoneMockup) + live WhatsApp chat screen driven by scroll.
// Vanilla JS port: same geometry (360x800, 52px frame radius), same materials
// (titanium gradient body, metallic edge highlight, dynamic island w/ camera
// dot, side buttons, glass reflection), plus GSAP-scrubbed chat.

export function createIPhone(container, opts = {}) {
  const scale = opts.scale ?? 0.82;

  container.classList.add("iphone-scene");
  container.innerHTML = `
    <div class="ip-wrapper" style="width:${360 * scale}px;height:${800 * scale}px">
      <div class="ip-device">
        <div class="ip-frame">
          <div class="ip-edge"></div>
          <div class="ip-bezel">
            <div class="ip-screen-shell">
              <div class="ip-island"><span class="ip-cam"></span></div>
              <div class="ip-screen">
                <div class="chat-ui" id="${opts.screenId || "ip-chat"}"></div>
                <div class="ip-glass"></div>
              </div>
            </div>
          </div>
          <span class="ip-btn ip-btn--vol1"></span>
          <span class="ip-btn ip-btn--vol2"></span>
          <span class="ip-btn ip-btn--action"></span>
          <span class="ip-btn ip-btn--power"></span>
        </div>
        <div class="ip-shadow"></div>
      </div>
    </div>`;

  const device = container.querySelector(".ip-device");
  const chatUI = container.querySelector(".chat-ui");

  /* ---------- rotation state ---------- */
  const LIMITS = { minX: -8, maxX: 12, minY: -25, maxY: 15 };
  const BASE = { x: 2, y: -12 };
  let rot = { ...BASE };
  let dragging = false;
  let phase = Math.random() * Math.PI * 2;
  const dragStart = { x: 0, y: 0, rx: 0, ry: 0 };
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const interactive = opts.interactive !== false && !reduced;

  const clamp = () => {
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
      rot.y = dragStart.ry + (e.clientX - dragStart.x) * 0.3;
      rot.x = dragStart.rx - (e.clientY - dragStart.y) * 0.15;
      clamp();
    });
    const end = () => (dragging = false);
    container.addEventListener("pointerup", end);
    container.addEventListener("pointercancel", end);
  }

  function tick() {
    requestAnimationFrame(tick);
    if (reduced) return;
    phase += 0.022;
    if (!dragging) {
      // ease back toward base while auto-swaying
      rot.x += (BASE.x - rot.x) * 0.05;
      rot.y += (BASE.y - rot.y) * 0.05;
    }
    const swayY = dragging ? 0 : Math.sin(phase) * 9;
    device.style.transform =
      `rotateY(${rot.y + swayY}deg) rotateX(${rot.x}deg)`;
  }
  tick();

  /* ---------- WhatsApp chat on the screen ---------- */
  const SCRIPT = [
    { t: "Hi! Are you open for a consult call this Friday?", out: false },
    { t: "Unknown sender held for review", sys: true },
    { t: "Access granted by owner", sys: true, ok: true },
    { t: "Hey Ananya! Let me check… 📅", out: true },
    { t: "Friday's open after 2pm. Booked you for 3:00 PM IST — invite sent ✅", out: true },
    { t: "Perfect, see you then! 🙌", out: false },
  ];

  function renderChat(state) {
    const n = Math.round(Math.min(state, SCRIPT.length));
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
        const time = m.out ? "10:22 <b class='ticks'>✓✓</b>" : "10:21";
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
    el: device,
  };
}
