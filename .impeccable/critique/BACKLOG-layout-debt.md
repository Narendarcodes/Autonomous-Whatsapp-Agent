# Layout Debt Backlog (full-parse detector run, post F2-F10)

Full-parse mode enabled (htmlparser2 etc. installed in skill scripts dir). 52 findings — these are STRUCTURAL, need a design pass not spot-fixes:

## Summary
- cramped-padding x28: glass-panel sections whose children sit flush against the border. Fix pattern: ensure p-6/p-8 on every glass-panel; audit grid gutters.
- nested-cards x18: card-inside-card (div in div with borders/bg). Fix pattern: flatten one level or switch inner to borderless + divider.
- gpt-thin-border-wide-shadow x1: 1px border + 25px blur shadow combo (confirm modal qr-card). Pick one depth cue.
- skipped-heading x1: h2 "Access Control" -> h4 (missing h3). Fix: change table header to h3.
- pulsing-dot x1: animate-pulse on tiny dot ("Connected" indicator) — intentional live-status cue, likely keep.

## Suggested command sequence
$impeccable layout (cramped-padding pass)
$impeccable distill (nested-cards flattening)
then $impeccable polish.


## UPDATE (post shim + craft fixes)
- cramped-padding x28: FALSE POSITIVES. Cause: Tailwind CDN compiles utilities at
  runtime; the static detector saw zero padding on every utility-padded panel.
  Fixed by adding a static CSS shim (.p-5/.p-6/.p-8/.px-8/.py-6) to the template.
  Detector now: 22 findings.
- gpt-thin-border-wide-shadow: FIXED (glass-panel hover lift removed, suggestion
  dropdown borders dropped, modal elevation standardized).
- skipped-heading: FIXED (h4 -> h3 under Access Control h2).
- nested-cards x18: mostly FUNCTIONAL option-cards (mode selector, voice cards,
  connector tiles) inside section panels — a standard radio-card pattern, not
  decorative nesting. Accepted; flattening would harm usability.
  Exception worth doing later: QR scanner double-frame (decorative).
- pulsing-dot x1: intentional live-status indicator. Accepted.
- broken-image x2: runtime-populated avatars. False positives.
