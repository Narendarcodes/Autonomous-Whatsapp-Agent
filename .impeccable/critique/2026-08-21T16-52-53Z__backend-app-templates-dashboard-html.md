---
target: omniWA dashboard
total_score: 25
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 2
timestamp: 2026-08-21T16-52-53Z
slug: backend-app-templates-dashboard-html
---
# Impeccable Critique — omniWA dashboard.html (snapshot body)
Score 25/40 (Nielsen sum; no n/a). P0 fixed during run: unbalanced script tags killed all dashboard JS — regression test added. Detector: exit 2, 9 warnings (side-tab x2, overused-font x2, bounce-easing x2, broken-image x2, dark-glow x1), regex-fallback undercount. P1: icon-only buttons missing aria-labels (L2115, L3731); AI-slop visual tells. P2: jargon copy ("ACL", "connectors"); broken-image placeholders for profile pics. P3: no keyboard paths. Strengths: token system, QR flow, built-in 10-step tutorial, toast+confirm patterns. Recommended: $impeccable audit -> $impeccable polish -> $impeccable clarify -> $impeccable harden.


## ADDENDUM — Complete Assessment A findings (full subagent report)

Score refined: 24/40 (A's verified table). Additional findings beyond condensed snapshot:

**New issues:**
- [P1] Destructive-action hierarchy inverted: 'Disconnect Session' is the filled primary button; 'Reset Whitelist' one click away in table header; neither has undo.
- [P1] Hardcoded placeholder identity data at trust-critical connected state ('Alex Thompson', '+1 (555) 012-3456') — renders before real data loads.
- [P2] Connection IA scattered across FOUR homes: Google card, Connectors tab, API Keys panel, System Resources.
- [P2] Mobile bottom nav omits Agent Soul section entirely.
- [P1] 12-option flat provider dropdown mixes LLM providers with integrations (Database Inspector, Playwright Scraper...) — wall of options for non-technical owners.
- QR countdown expiry offers no recovery guidance; Privacy reassurance arrives only post-connect; Google OAuth card states no data-access summary.
- Three competing greens (#059669/#00a884/#10b981); tablist roles lack aria-selected; no tab deep-linking; 'TTS speech-to-audio' mislabeled; 13 font weights across two families.

**Confirmed strengths:** QR choreography (L483-536), accessibility floor (skip link, focus rings, reduced-motion), 10-step guided tour (L350-381).

Provocative Qs: (1) Why does setup feel like VPS mission control when the promise is 'text your business assistant'? (2) What if the dashboard were ONE checklist — WhatsApp -> Google -> contacts -> Done — everything else behind Advanced?


## ADDENDUM 2 — Assessment B mechanical a11y counts (exact)

- Icon-only buttons missing aria-label: 2 of 32 (L2115 connector-modal-close, L3731 agent-QR close)
- Form controls without labels: 4 (user-filter L1248, checkbox-key-active L1447, dynamic trust-level select L2644 +1 same pattern)
- onclick on non-interactive elements: 3 (h1 logo reload L387, suggestion-item divs L3505/L3628 — need role=button/tabindex or real buttons)
- Inline style attributes: 36
- alert/confirm/prompt: 0 (custom modal used — good)
- Landmarks: lang/skip-link/main all present
- DUPLICATE IDs: connector-field-api_key x3 (L1998, L2017, L2055) — will break JS getElementById lookups for the connector form

Detector false positives: broken-image x2 (avatars populated at runtime, hidden until loaded); dark-glow single-shadow judgment call.
Detector ran degraded (regex fallback; parser modules missing) — undercount. Full parse possible via npm install htmlparser2 css-select css-tree domutils in skill scripts dir.
