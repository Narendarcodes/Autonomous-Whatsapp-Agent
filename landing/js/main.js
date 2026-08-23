// main.js — orchestrates all landing page motion
import { gsap, ScrollTrigger, reducedMotion, initSmoothScroll, splitChars, prepareReveals } from "./motion.js";
import { createSignalCore, createCtaScene } from "./scene3d.js";

initSmoothScroll();
prepareReveals();

/* ---------- 3D scenes ---------- */
const core = { api: null };
{
  const heroCanvas = document.getElementById("shader-canvas");
  if (heroCanvas) {
    // keep the flat aurora as a base layer behind the 3D object
    import("./shader-bg.js").then(({ createShaderBackground }) =>
      createShaderBackground(heroCanvas, { intensity: 0.55 })
    );
    const objCanvas = document.createElement("canvas");
    objCanvas.id = "hero-3d-canvas";
    heroCanvas.parentElement.insertBefore(objCanvas, heroCanvas.nextSibling);
    core.api = createSignalCore(objCanvas);
  }
}

// hero scroll → 3D core reacts (spin + shrink away)
if (core.api && !reducedMotion) {
  ScrollTrigger.create({
    trigger: ".hero",
    start: "top top",
    end: "bottom top",
    scrub: true,
    onUpdate: (self) => {
      const p = self.progress;
      core.api.state.scrollRotY = p * 1.6;
      core.api.state.scrollRotX = p * -0.5;
      core.api.state.scale = Math.max(0.25, 1 - p * 0.9);
    },
  });
}

/* ---------- nav glass on scroll ---------- */
const nav = document.getElementById("nav");
ScrollTrigger.create({
  start: 60,
  end: "max",
  onUpdate: (self) => nav.classList.toggle("is-scrolled", self.scroll() > 60),
  onToggle: (self) => nav.classList.toggle("is-scrolled", self.isActive),
});

/* ---------- hero intro timeline ---------- */
if (!reducedMotion) {
  const chars = splitChars(document.querySelector(".hero__title"));
  gsap.set(chars, { yPercent: 110 });

  const intro = gsap.timeline({ defaults: { ease: "power4.out" } });
  intro
    .to(".hero__eyebrow", { opacity: 1, y: 0, duration: 0.7 }, 0.15)
    .to(chars, { yPercent: 0, duration: 0.9, stagger: 0.018 }, 0.25)
    .to(".hero__sub", { opacity: 1, y: 0, duration: 0.8 }, "-=0.45")
    .to(".hero__actions", { opacity: 1, y: 0, duration: 0.8 }, "-=0.55")
    .to(".hero__scrollcue", { opacity: 1, duration: 0.8 }, "-=0.3");

  // hero parallax out on scroll
  gsap.to(".hero__content", {
    opacity: 0.15,
    yPercent: -12,
    ease: "none",
    scrollTrigger: {
      trigger: ".hero",
      start: "top top",
      end: "bottom top",
      scrub: true,
    },
  });
} else {
  document
    .querySelectorAll(".reveal-fade")
    .forEach((el) => (el.style.opacity = 1));
}

/* ---------- marquee: duplicate group for seamless loop ---------- */
const track = document.getElementById("marquee-track");
if (track && !reducedMotion) {
  track.appendChild(track.querySelector(".marquee__group").cloneNode(true));
}

/* ---------- generic scroll reveals ---------- */
if (!reducedMotion) {
  // fade-ups
  document.querySelectorAll(".reveal-fade").forEach((el) => {
    if (el.closest(".hero")) return; // handled by intro
    gsap.to(el, {
      opacity: 1,
      y: 0,
      duration: 0.9,
      ease: "power3.out",
      scrollTrigger: { trigger: el, start: "top 85%" },
    });
  });

  // feature cards stagger
  document.querySelectorAll(".features__grid").forEach((grid) => {
    gsap.to(grid.querySelectorAll(".card"), {
      opacity: 1,
      y: 0,
      duration: 0.85,
      ease: "power3.out",
      stagger: 0.09,
      scrollTrigger: { trigger: grid, start: "top 78%" },
    });
  });

  // card cursor-glow + true 3D tilt follows mouse
  document.querySelectorAll(".card").forEach((card) => {
    const parent = card.parentElement;
    parent.style.perspective = "900px";

    card.addEventListener("pointermove", (e) => {
      const r = card.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width;
      const py = (e.clientY - r.top) / r.height;
      card.style.setProperty("--mx", `${e.clientX - r.left}px`);
      card.style.setProperty("--my", `${e.clientY - r.top}px`);
      gsap.to(card, {
        rotationY: (px - 0.5) * 14,
        rotationX: -(py - 0.5) * 12,
        y: -6,
        transformPerspective: 900,
        duration: 0.4,
        ease: "power2.out",
      });
    });
    card.addEventListener("pointerleave", () => {
      gsap.to(card, { rotationY: 0, rotationX: 0, y: 0, duration: 0.7, ease: "elastic.out(1, 0.55)" });
    });
  });
}

/* ---------- timeline progress line + step activation ---------- */
const timeline = document.getElementById("timeline");
if (timeline) {
  const progress = document.createElement("div");
  progress.className = "timeline-progress";
  timeline.prepend(progress);

  if (!reducedMotion) {
    gsap.to(progress, {
      scaleY: 1,
      ease: "none",
      scrollTrigger: {
        trigger: timeline,
        start: "top 70%",
        end: "bottom 55%",
        scrub: 0.6,
      },
    });

    document.querySelectorAll(".timeline__item").forEach((item) => {
      gsap.to(item, {
        opacity: 1,
        x: 0,
        duration: 0.7,
        ease: "power3.out",
        scrollTrigger: { trigger: item, start: "top 82%" },
      });
      ScrollTrigger.create({
        trigger: item,
        start: "top 65%",
        end: "bottom 40%",
        onToggle: (self) => item.classList.toggle("is-active", self.isActive),
      });
    });
  }
}

/* ---------- pinned chat showcase ---------- */
const chatSection = document.querySelector(".chatshow");
if (chatSection && !reducedMotion) {
  const msgs = [...document.querySelectorAll("#phone-screen .msg")];
  const beats = [...document.querySelectorAll(".chatshow__beats li")];
  const status = document.getElementById("phone-status");

  // beat → message indices mapping:
  // beat 0 (stranger held): msg 0 in, msg 1 sys-hold
  // beat 1 (approve):       msg 2 sys-ok, status change
  // beat 2 (real work):     msg 3 out, typing t, msg 4 out, msg 5 in

  const tl = gsap.timeline({
    scrollTrigger: {
      trigger: chatSection,
      start: "top top",
      end: "+=220%",
      pin: true,
      scrub: 0.6,
      anticipatePin: 1,
    },
  });

  const showMsg = (m, pos) =>
    tl.to(m, { opacity: 1, y: 0, scale: 1, visibility: "visible", duration: 0.5 }, pos);
  const showBeat = (b, pos) => {
    tl.call(() => beats.forEach((el, i) => el.classList.toggle("is-active", i === b)), null, pos);
  };

  showMsg(msgs[0], 0);          // "Hi! consult call friday?"
  showBeat(0, 0.5);
  showMsg(msgs[1], 0.6);        // hold notice
  tl.to(status, {}, ">");       // keep pacing

  showBeat(1, 1.4);
  showMsg(msgs[2], 1.6);        // approved
  tl.call(() => (status.textContent = "approved · member"), null, 1.9);

  showBeat(2, 2.4);
  showMsg(msgs[3], 2.5);        // "let me check…"
  showMsg(msgs[4], 3.2);        // typing indicator (CSS animates dots)
  tl.to(msgs[4], { opacity: 0, visibility: "hidden", duration: 0.01 }, 3.8);
  showMsg(msgs[5], 3.85);       // booked confirmation
  showMsg(msgs[6], 4.3);        // "perfect!"
  tl.to({}, { duration: 0.7 }); // tail padding so last messages linger
}

/* ---------- stats counters ---------- */
document.querySelectorAll(".stat__num").forEach((numEl) => {
  const target = parseFloat(numEl.dataset.count);
  const suffix = numEl.dataset.suffix || "";
  if (reducedMotion) {
    numEl.textContent = target + suffix;
    return;
  }
  const obj = { v: 0 };
  ScrollTrigger.create({
    trigger: numEl,
    start: "top 88%",
    once: true,
    onEnter: () => {
      gsap.to(obj, {
        v: target,
        duration: 1.8,
        ease: "power2.out",
        onUpdate: () => {
          numEl.textContent = Math.round(obj.v) + suffix;
        },
      });
    },
  });
});

/* ---------- CTA finale reveal + 3D knot ---------- */
{
  const ctaCanvas = document.getElementById("cta-shader-canvas");
  if (ctaCanvas) createCtaScene(ctaCanvas);
}
if (!reducedMotion) {
  const ctaChars = splitChars(document.querySelector(".cta__title"));
  gsap.set(ctaChars, { yPercent: 110 });
  gsap.to(ctaChars, {
    yPercent: 0,
    duration: 0.85,
    ease: "power4.out",
    stagger: 0.02,
    scrollTrigger: { trigger: ".cta", start: "top 62%" },
  });
  gsap.set(".cta .reveal-fade", { opacity: 0, y: 20 });
  gsap.to(".cta .reveal-fade", {
    opacity: 1,
    y: 0,
    duration: 0.8,
    stagger: 0.12,
    scrollTrigger: { trigger: ".cta", start: "top 50%" },
  });
}

/* refresh after everything settles */
window.addEventListener("load", () => ScrollTrigger.refresh());
