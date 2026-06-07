# AssedGuard AI
> Track 01 — Data Rescue | M-AGENTS Hackathon | June 7, 2026

A 4-agent system that rescues corrupted manufacturing data and produces a
signed-ready, plain-English audit narrative — operable by a compliance officer
who has never opened a database.

## Setup (5 minutes)

### 1. Install backend
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
```

### 2. (Optional) Add API keys
AssedGuard runs **fully offline with no keys** — all detection, ranking, and
fixing is deterministic Python, and the narrative falls back to a deterministic
template. Add keys only to enable Claude's narrative polish and Cognee cloud:
- Anthropic: https://console.anthropic.com
- Cognee (free 14-day trial): https://www.cognee.ai

Put them in `backend/.env`:
```
ANTHROPIC_API_KEY=sk-ant-...
COGNEE_API_KEY=...
```

### 3. Run the backend
```bash
cd backend
uvicorn main:app --reload --port 8000
```

### 4. Open the frontend
Open `frontend/index.html` directly in your browser (no build step — CDN React).

### 5. Run the demo
- Drag `data/sample.csv` into the upload zone
- The audit runs automatically — watch the 4 agents fire in sequence
- Review the ranked findings table (click any row to expand the reason)
- Click **Download Audit Narrative PDF**

## Architecture

```
CSV ─► Scout ─► Ranker ─► Fixer ─► Narrator ─► PDF
        │         │         │          │
        └─────── Cognee shared memory ─┘
        (each agent writes its output; the next reads it before acting)
```

- **Agent 1 — Scout** detects 6 issue classes with deterministic Python: exact
  duplicates, conflicting lot quantities, unit conflicts, statistical outliers
  (>3σ), missing timestamps, and compliance contradictions (PASS + out-of-spec
  temperature). HIGH findings are enriched with the specific regulation violated.
- **Agent 2 — Ranker** orders findings by a transparent rubric (compliance
  contradictions → traceability → measurement). Every rank has a logged
  plain-English reason. Claude peer-reviews the ordering and attaches a note.
- **Agent 3 — Fixer** auto-fixes (dedupe keep-most-recent, flag missing dates),
  flags (unit conflicts, outliers), and escalates (contradictions, lot
  conflicts) — each with a logged reason — and emits a corrected CSV.
- **Agent 4 — Narrator** reads the full pipeline memory and writes a 6-section
  audit narrative, rendered to a professional PDF with reportlab.

**Memory layer:** Cognee is the handoff bus. If the SDK/key is unavailable it
transparently falls back to an in-process store, so the pipeline never breaks.

## Mandatory tools used
- **Cognee** — shared memory layer between all 4 agents (real read/write handoffs)
- **Trupeer** — demo video (see submission)
- **Geodo** — regulatory entity research by the Domain Expert

## Judging criteria met
1. **Multi-agent system** — 4 specialized agents with real Cognee handoffs; each
   reads upstream memory before acting and writes its output after.
2. **Solves a real data-rescue problem** — duplicates, unit conflicts,
   outliers, missing data, and compliance contradictions in factory data.
3. **Usable by a non-technical end user** — drag a CSV, one automatic run,
   download a PDF. Zero jargon, zero engineering required.
4. **Tangible deliverable** — a signed-ready audit narrative PDF plus a
   corrected dataset CSV.
5. **Every decision is explainable** — every ranking and fixing action carries a
   visible, deterministic, plain-English reason. "The model said so" appears
   nowhere in the product.

## Endpoints
| Method | Path | Purpose |
|--------|------|---------|
| POST | `/run-audit` | Upload CSV, stream agent status (SSE) |
| GET | `/findings` | Ranked findings + stats for the table |
| GET | `/download-narrative` | Audit narrative PDF |
| GET | `/download-corrected` | Corrected dataset CSV |
| GET | `/status` | Current agent statuses |
| GET | `/health` | Health check |
