// main.js — omniWA landing: WhatsApp marketing world, restrained motion
import { gsap, ScrollTrigger, reducedMotion, initSmoothScroll } from "./motion.js";
import { createPhone3D } from "./phone3d.js";

initSmoothScroll();

/* ---------- 3D phones (real models) ---------- */
const heroCanvas = document.getElementById("phone3d");
const demoCanvas = document.getElementById("phone3d-demo");
const heroPhone = heroCanvas ? createPhone3D(heroCanvas) : null;
const demoPhone = demoCanvas ? createPhone3D(demoCanvas, { baseRotY: -0.18 }) : null;

// hero phone: slow scroll-driven turn as you leave the hero
if (heroPhone && !reducedMotion) {
  ScrollTrigger.create({
    trigger: ".hero",
    start: "top top",
    end: "bottom top",
    scrub: true,
    onUpdate: (self) => {
      const p = self.progress;
      heroPhone.state.rotY = p * 0.9;
      heroPhone.state.posY = p * -1.2;
    },
  });
}

/* ---------- nav hairline on scroll ---------- */
const nav = document.getElementById("nav");
ScrollTrigger.create({
  start: 24,
  end: "max",
  onUpdate: (self) => nav.classList.toggle("is-scrolled", self.scroll() > 24),
});

/* ---------- restrained reveals ---------- */
if (!reducedMotion) {
  gsap.set(".reveal", { opacity: 0, y: 16 });
  document.querySelectorAll(".steps").forEach((list) => {
    gsap.to(list.querySelectorAll(".step"), {
      opacity: 1,
      y: 0,
      duration: 0.7,
      ease: "power2.out",
      stagger: 0.08,
      scrollTrigger: { trigger: list, start: "top 78%" },
    });
  });

  // hero intro — quiet fade-rise, no char theatrics
  gsap.from(".hero__copy > *", {
    opacity: 0,
    y: 22,
    duration: 0.85,
    ease: "power2.out",
    stagger: 0.1,
    delay: 0.15,
  });
  gsap.from(".doodle--hero", { opacity: 0, duration: 1.2, delay: 0.9 });
}

/* ---------- pinned demo: chat scrubs ON the 3D phone texture ---------- */
const demo = document.querySelector(".demo");
if (demo) {
  const beats = [...document.querySelectorAll(".beats li")];
  const SCRIPT_LEN = 6; // messages in the phone's conversation script

  if (!reducedMotion) {
    ScrollTrigger.create({
      trigger: demo,
      start: "top top",
      end: "+=260%",
      pin: true,
      scrub: 0.5,
      anticipatePin: 1,
      onUpdate: (self) => {
        const p = self.progress;
        // chat advances across the first 80% of the pin
        if (demoPhone) demoPhone.setChatProgress(p * 1.25 * SCRIPT_LEN);
        // phone rotates gently through the pin for dimensionality
        demoPhone && (demoPhone.state.rotY = -0.18 + Math.sin(p * Math.PI) * 0.4);
        // beat highlighting in thirds
        const beat = Math.min(2, Math.floor(p * 3));
        beats.forEach((el, j) => el.classList.toggle("is-active", j === beat));
      },
    });
  } else if (demoPhone) {
    demoPhone.setChatProgress(SCRIPT_LEN);
    beats.forEach((b, i) => b.classList.toggle("is-active", i === 0));
  }
}

window.addEventListener("load", () => ScrollTrigger.refresh());
