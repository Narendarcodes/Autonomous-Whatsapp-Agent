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
