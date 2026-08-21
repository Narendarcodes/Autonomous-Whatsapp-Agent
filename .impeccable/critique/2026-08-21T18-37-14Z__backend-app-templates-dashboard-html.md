---
target: omniWA dashboard
total_score: 31
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 0
timestamp: 2026-08-21T18-37-14Z
slug: backend-app-templates-dashboard-html
---
Post-fix re-run after F2-F10 implementation batch (TDD, one commit per unit).
Detector: 9 -> 6 findings. Remaining 6 assessed:
- side-tab x2: now 3px translucent accents on toasts (semantic success/error
  color-coding, not decoration) — accepted deviation.
- overused-font x2: detector flags Instrument Sans as common; pairing kept
  deliberately (Bricolage Grotesque carries identity; Instrument Sans is the
  workhorse). Banned faces Geist/Space Grotesk are gone.
- broken-image x2: false positives (avatars populated at runtime, hidden until load).
Bounce easing x2 and dark glow x1 eliminated; single primary green enforced;
a11y labels added; jargon copy replaced; mobile parity + deep-linking added;
placeholder identity removed; typed-confirmation for destructive actions.
Nielsen estimate post-fix: ~31/40 (up from 24/40).
