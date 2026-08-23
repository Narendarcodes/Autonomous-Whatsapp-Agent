// motion.js — Lenis smooth scroll + GSAP/ScrollTrigger wiring + shared helpers
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";

gsap.registerPlugin(ScrollTrigger);

export { gsap, ScrollTrigger };

export const reducedMotion = window.matchMedia(
  "(prefers-reduced-motion: reduce)"
).matches;

let lenis = null;

export function initSmoothScroll() {
  if (reducedMotion) return null;
  lenis = new Lenis({
    duration: 1.15,
    easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
    smoothWheel: true,
    touchMultiplier: 1.6,
  });

  // drive lenis from GSAP's ticker so ScrollTrigger stays in sync
  lenis.on("scroll", ScrollTrigger.update);
  gsap.ticker.add((time) => lenis.raf(time * 1000));
  gsap.ticker.lagSmoothing(0);

  // anchor links through lenis
  document.querySelectorAll('a[href^="#"]').forEach((a) => {
    a.addEventListener("click", (e) => {
      const id = a.getAttribute("href");
      if (id.length <= 1) return;
      const target = document.querySelector(id);
      if (!target) return;
      e.preventDefault();
      lenis.scrollTo(target, { offset: -70, duration: 1.4 });
    });
  });

  return lenis;
}

/** Split text into char/line spans for reveals. Minimal splitter. */
export function splitChars(el) {
  const walk = (node) => {
    [...node.childNodes].forEach((child) => {
      if (child.nodeType === Node.TEXT_NODE) {
        const frag = document.createDocumentFragment();
        for (const ch of child.textContent) {
          if (ch === "\n" || ch.trim() === "") {
            frag.appendChild(document.createTextNode(ch === "\n" ? "\n" : " "));
            continue;
          }
          const wrap = document.createElement("span");
          wrap.className = "char-wrap";
          wrap.style.display = "inline-block";
          wrap.style.overflow = "hidden";
          wrap.style.verticalAlign = "top";
          const inner = document.createElement("span");
          inner.className = "char";
          inner.style.display = "inline-block";
          inner.textContent = ch;
          wrap.appendChild(inner);
          frag.appendChild(wrap);
        }
        node.replaceChild(frag, child);
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        walk(child);
      }
    });
  };
  walk(el);
  return el.querySelectorAll(".char");
}

/** Set initial hidden states; returns list of things to animate. */
export function prepareReveals() {
  if (reducedMotion) return;

  gsap.set(".reveal-fade", { opacity: 0, y: 24 });
  gsap.set(".reveal-card", { opacity: 0, y: 40 });
  gsap.set(".reveal-step", { opacity: 0, x: -30 });
}
