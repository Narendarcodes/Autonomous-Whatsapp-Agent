// main.js — omniWA landing: WhatsApp marketing world + Apple-grade motion
import { gsap, ScrollTrigger, reducedMotion, initSmoothScroll } from "./motion.js";
import { createIPhone } from "./phone-css3d.js";

initSmoothScroll();

/* ---------- CSS-3D iPhones ---------- */
const heroPhoneEl = document.getElementById("phone-hero");
const demoPhoneEl = document.getElementById("phone-demo");
const heroPhone = heroPhoneEl ? createIPhone(heroPhoneEl, { scale: 0.78, interactive: true }) : null;
const demoPhone = demoPhoneEl ? createIPhone(demoPhoneEl, { scale: 0.72, interactive: false }) : null;

// hero phone: gentle rise + turn as you scroll away
if (!reducedMotion && heroPhone) {
  gsap.to(heroPhoneEl, {
    yPercent: -14,
    rotation: -3,
    ease: "none",
    scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: true },
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
  document.querySelectorAll(".steps").forEach((list) => {
    gsap.from(list.querySelectorAll(".step"), {
      opacity: 0,
      y: 16,
      duration: 0.7,
      ease: "power2.out",
      stagger: 0.08,
      scrollTrigger: { trigger: list, start: "top 78%" },
    });
  });

  // hero intro — quiet fade-rise
  gsap.from(".hero__copy > *", {
    opacity: 0,
    y: 22,
    duration: 0.85,
    ease: "power2.out",
    stagger: 0.1,
    delay: 0.15,
  });
  if (heroPhoneEl) {
    gsap.from(heroPhoneEl, {
      opacity: 0,
      y: 40,
      duration: 1.1,
      ease: "power2.out",
      delay: 0.35,
    });
  }
  gsap.from(".doodle--hero", { opacity: 0, duration: 1.2, delay: 0.9 });
}

/* ---------- pinned demo: chat scrubs ON the iPhone screen ---------- */
const demo = document.querySelector(".demo");
if (demo && demoPhone) {
  const beats = [...document.querySelectorAll(".beats li")];
  const SCRIPT_LEN = 6;

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
        // conversation advances across the pin; typing dots live between msgs 4-5
        demoPhone.setChatProgress(p * 1.25 * SCRIPT_LEN);
        const beat = Math.min(2, Math.floor(p * 3));
        beats.forEach((el, j) => el.classList.toggle("is-active", j === beat));
      },
    });
  } else {
    demoPhone.setChatProgress(SCRIPT_LEN);
    beats.forEach((b, i) => b.classList.toggle("is-active", i === 0));
  }
}

window.addEventListener("load", () => ScrollTrigger.refresh());
