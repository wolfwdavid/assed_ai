# AssedGuard AI — Submission Pack
*M-AGENTS Hackathon · Track 01 — Data Rescue · June 7, 2026*

This file has everything you paste/record for Devpost. Three sections:
1. **Devpost written description** (paste-ready)
2. **Trupeer shot-list** (timed, one-take)
3. **Geodo research checklist** (exact searches + how to cite)

Plus a final submission checklist at the bottom.

---

# 1 · DEVPOST WRITTEN DESCRIPTION (paste-ready)

> Paste the block below into the Devpost "What it does / How we built it" field.
> Track selection on the form: **Track 01 — Data Rescue.**

---

## AssedGuard AI
**Turn corrupted factory data into a signed-ready regulatory audit document — with every decision explained.**

### The problem
A compliance officer at a mid-size manufacturer has an FDA audit in days and a spreadsheet full of corrupted production data: duplicate batches, physically impossible sensor readings, units logged two different ways, missing dates, and — worst of all — lots marked **PASS** that ran over the legal temperature limit. She works in Excel, not SQL. A failed data-integrity audit can shut a line down. She needs answers she can sign her name to, not a database tool.

### What it does
AssedGuard AI is a **multi-agent pipeline** (five specialized agents) that rescues the dataset end to end. The officer drags in one CSV, clicks once, and:
1. **Scout** detects every data-integrity issue across the six Track-01 benchmark classes (exact duplicates, near-duplicate variants, unit-format drift, orphaned customer references, decimal-shift weights, impossible values) plus domain rules (conflicting lot quantities, unit conflicts, statistical outliers, missing timestamps, and PASS/over-temperature compliance contradictions).
2. **Ranker** orders every finding by regulatory audit risk using a transparent rubric — each rank carries a written, plain-English reason.
3. **Fixer** auto-corrects what is safe, flags what needs review, and escalates what a human must sign off — logging a reason for every action and preserving the original value in an audit-trail column.
4. **Narrator** reads the full pipeline memory and writes a plain-English, signed-ready audit narrative.
5. **Counsel** uses Geodo regulatory-entity research to attach real-world regulatory history for the facilities involved and rates overall audit exposure.

The output: a **signed-ready audit PDF** plus a **corrected dataset CSV** — produced in seconds, operable by someone who has never opened a database.

### How we built it
- **Backend:** FastAPI; five agents that hand off through a shared **Cognee** memory layer — each agent writes its structured output and the next reads it before acting (real coordination, not file passing). PDF via reportlab.
- **Bayesian anomaly detection (PyMC):** out-of-range sensor readings are scored by a robust Student-T model that resists being skewed by the outliers themselves — so it pins each sensor's true in-spec range and attaches a calibrated anomaly probability + 95% credible range to every finding. Best-effort with a deterministic 3σ fallback, so detection never breaks.
- **Frontend:** single-page React (CDN) — drag-and-drop upload, live SSE agent progress, a density-based risk gauge, an expandable, paginated findings table, a before/after data diff, and a pre-audit checklist.
- **Explainable by design:** every *decision* — what's wrong, how risky, how to fix it — is deterministic Python with a logged, plain-English reason and the specific regulation cited (FDA 21 CFR Part 11, ISO 13485). The language model is used only to polish wording, never to decide. No decision is ever an unexplained model verdict.
- **Bulletproof for a live demo:** every AI/Cognee/Geodo path has a deterministic fallback, so the full pipeline runs offline with zero keys and never breaks.

### Mandatory tools
- **Cognee** — the shared memory bus all five agents hand off through (write/read handoffs; deterministic in-process mirror as fallback).
- **Geodo** — powers the Counsel agent's regulatory-entity research (facility compliance history, exposure rating).
- **Trupeer** — demo video (linked in this submission).

*Also used:* **PyMC** — robust Bayesian model that scores out-of-range sensor readings (calibrated anomaly probability + credible normal range).

### Results on the demo dataset (200 records)
- 35 integrity issues found (18 critical, 16 moderate, 1 minor)
- 25 auto-corrected, 10 escalated for human sign-off
- Audit risk score 85/100 — HIGH (density-based: issues affect ~32.5% of the 200 records; biggest driver: 8 exact-duplicate records). The #1 ranked finding is a lot marked PASS over the FDA temperature limit; Counsel rates regulatory exposure CRITICAL.
- 8 duplicate rows removed, 34 rows corrected; 12-item pre-audit action plan
- 7-page signed-ready PDF + corrected CSV

### What we deliberately did NOT build
Enterprise SSO / multi-tenant auth, database connectors (CSV upload only), or real-time monitoring. Scope is a single audit run a non-technical officer can complete alone.

---

# 2 · TRUPEER SHOT-LIST (one-take, ~5 min)

**✅ Recorded video:** https://app.trupeer.ai/view/5U2nTdaab/asset-guard-ai-user-manual

Record at **trupeer.ai**. Record **as the compliance officer**, not as an engineer explaining code. Pre-flight: backend up on `:8000`, frontend open on `:8080` at the landing page, `product_brief.pdf` open in a second tab.

| # | Time | On screen | Say (short) |
|---|------|-----------|-------------|
| 1 | 0:00–0:25 | `product_brief.pdf` | "AssedGuard AI, Track 01 — Data Rescue. This is our one-page product brief: who it's for — a compliance officer with an FDA audit in days — and what success looks like." |
| 2 | 0:25–0:45 | Frontend landing page | "She works in Excel, not databases. She drags in one corrupted manufacturing CSV. The audit starts automatically." |
| 3 | 0:45–1:00 | **Click Run sample dataset** — let 5 agent cards animate | "Five specialist agents fire in sequence, handing off through a shared memory layer." |
| 4 | 1:00–1:40 | Agent cards completing | "Scout finds 35 integrity issues. Ranker orders them by audit risk. Fixer auto-corrects 25 and escalates 10 for human sign-off. Narrator writes the report. Counsel rates regulatory exposure — using Geodo research on the facilities." |
| 5 | 1:40–2:05 | Point to risk gauge | "Audit risk: 85 out of 100 — HIGH — and it tells her why: data-integrity issues affect about a third of all 200 records, with 8 duplicate batches the biggest driver. Not a black-box score." |
| 6 | 2:05–2:35 | Click the **top** finding row to expand it | "The number-one finding is a lot marked PASS that ran over the FDA temperature limit. Every finding opens to a plain-English reason and the exact regulation it violates — 21 CFR Part 11, ISO 13485. A reason a human can sign." |
| 7 | 2:35–2:55 | Point at a sensor row's **PyMC** badge | "The out-of-range sensor readings carry a PyMC badge — a robust Bayesian model that rates each one ~100% likely anomalous and reports the sensor's true in-spec range. Real statistical modeling, not just a threshold." |
| 8 | 2:55–3:20 | Scroll to before/after diff | "The actual rescue: 8 duplicate rows removed, 34 corrected, cell by cell — and the original value is never destroyed, it's kept in an audit-trail column." |
| 9 | 3:20–3:55 | **Click Download Audit Narrative PDF**, open it | "Here's what she hands the auditor: a signed-ready PDF — every finding, every reason, a signature block. Plus a corrected CSV." |
| 10 | 3:55–4:35 | Scroll the PDF (regulatory + checklist sections) | "It even includes the Geodo regulatory context and a pre-audit action plan with owners and deadlines." |
| 11 | 4:35–5:00 | Back to dashboard | "Drag a corrupted dataset, click once, hand the auditor a document you can sign — with every decision explained. AssedGuard AI. Thank you." |

**Recording rules:** keep the cursor calm; let the agents animate before talking over them; say the track name (#1) and show the PDF download (#9) — both are scored. After recording, **copy the public share URL and paste it into the Devpost submission.** Verify it plays in an incognito window.

---

# 3 · GEODO RESEARCH CHECKLIST (mandatory, web platform)

Geodo is a web platform used manually by the Domain Expert — not a code dependency. Do these searches, screenshot the results, and cite them. This grounds the regulatory claims the Counsel agent and PDF make.

**Searches to run (geodo.io):**
1. **"FDA 21 CFR Part 11"** — electronic records / data integrity (ALCOA+). → This is the standard cited for compliance contradictions, decimal-shift, and impossible values.
2. **"ISO 13485 medical device quality management"** — esp. §4.2.4/§4.2.5 (control of records) and §7.5.8 (identification & traceability). → Cited for duplicates and lot/traceability findings.
3. **"FDA Form 483 manufacturing temperature excursion"** — real inspectional-observation context. → Backs the Counsel agent's facility regulatory-history narrative.
4. **Your facility/company scenario** — search the manufacturer/facility names in your dataset (e.g. `FAC-A/B/C` or the scenario company) for any regulatory entity records.
5. **"GMP good manufacturing practice data integrity"** — general framework backing the audit narrative tone.

**How to cite (pick the lightest that fits the form):**
- Add one line to the Devpost description: *"Regulatory citations (FDA 21 CFR Part 11, ISO 13485) were verified via Geodo entity research; see screenshots."*
- Attach 1–2 Geodo result screenshots to the Devpost gallery, OR drop them into a `geodo/` folder in the repo.
- Say it once in the Trupeer video at shot #4: *"…using Geodo research on the facilities."* (already in the shot-list).

**Why it matters:** judges score real-world alignment. Geodo evidence shows your compliance citations are grounded, not invented.

---

# 4 · FINAL SUBMISSION CHECKLIST (all 5 — partial submissions rejected)
- [ ] **Product Brief PDF** — `product_brief.pdf` (✅ generated, in repo root)
- [ ] **GitHub repo** — public, judges can clone and run
- [x] **Trupeer video URL** — https://app.trupeer.ai/view/5U2nTdaab/asset-guard-ai-user-manual *(verify it plays in incognito)*
- [ ] **Track selection** — Track 01 — Data Rescue
- [ ] **Written description** — paste Section 1 above
- [ ] **Team** — ≥2 people in named roles on Devpost  ⛔ *no solo submissions*
- [ ] **Geodo evidence** — screenshots cited per Section 3
- [ ] Submit **before 5:00 PM** — no extensions
