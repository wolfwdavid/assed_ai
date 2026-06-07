# AssedGuard AI v2 — 6 Extensions

## Plan
- [x] Ext 1: Geodo Compliance Counsel (Agent 5) — backend/agents/counsel.py + pipeline wire
- [x] Ext 2: /risk-score endpoint + RiskGauge component
- [x] Ext 3: /chat endpoint + ChatPanel component
- [x] Ext 4: /action-checklist endpoint + ActionChecklist + PDF section
- [x] Ext 5: /data-diff endpoint + DataDiff component
- [x] Ext 6: frontend wiring (5 agents, new sections, CSS)
- [x] Verify all live in browser (both themes)

## Review
All 6 extensions built and verified live in a real browser, zero console errors:
- 5 agents fire (counsel writes Cognee key "counsel"); risk gauge animates to
  100/CRITICAL; 12-item checklist with real finding IDs; data-diff shows
  8 removed / 7 modified / 42 flagged with before→after; chat answers
  dataset-specific questions and routes lot/submit/urgent intents correctly.
- PDF includes Regulatory Context (Geodo) + Pre-Audit Action Plan sections.
- Everything offline-robust (deterministic fallbacks) and dual-theme.

## Key decisions
- Brand stays **AssedGuard AI** (user's explicit rename), overriding the spec's "AuditGuard".
- All AI/Geodo paths are best-effort with DETERMINISTIC fallbacks (no keys in env),
  so every endpoint works offline for the demo.
- Counsel runs as agent 5 (after narrator). PDF is assembled AFTER counsel so it
  can include the regulatory + checklist sections (resolves the spec's ordering conflict).
- New component CSS uses the existing Aurora dual-theme tokens (not the spec's
  undefined --text-1/--fixed/etc.), so everything works in light AND dark mode.
