"""
AssedGuard AI — FastAPI application.
Hosts the 5-agent pipeline and streams agent status to the UI over SSE.
"""
import io
import json
import os
import re
from datetime import datetime

import pandas as pd
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from agents.counsel import build_action_checklist, run_counsel
from agents.fixer import run_fixer
from agents.narrator import run_narrator
from agents.ranker import run_ranker
from agents.scout import run_scout
from memory.cognee_store import (clear_session, init_cognee, read_all_memory,
                                 read_memory)
from utils.ai import ask_claude
from utils.pdf_gen import markdown_to_pdf

load_dotenv()
app = FastAPI(title="AssedGuard AI", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store (single-user demo).
session = {
    "df_original": None,
    "df_fixed": None,
    "narrative_md": None,
    "narrative_pdf": None,
    "filename": None,
    "status": "idle",
    "action_log": None,
    "checklist": None,
    "regulatory": None,
    "agent_statuses": {"scout": "idle", "ranker": "idle", "fixer": "idle",
                       "narrator": "idle", "counsel": "idle"},
}

# Human-readable labels for issue types (mirrors narrator).
ISSUE_LABEL = {
    "compliance_contradiction": "Compliance contradiction",
    "duplicate_lot_number": "Conflicting record values",
    "exact_duplicate": "Exact duplicate record",
    "near_duplicate_variant": "Near-duplicate variant",
    "unit_conflict": "Unit of measure conflict",
    "unit_format_drift": "Unit-format drift",
    "orphaned_customer": "Orphaned customer reference",
    "decimal_shift": "Decimal-shift weight error",
    "impossible_value": "Impossible value",
    "statistical_outlier": "Out-of-range reading",
    "missing_timestamp": "Missing timestamp",
}
ACTION_LABEL = {
    "compliance_contradiction": "Escalated",
    "duplicate_lot_number": "Escalated",
    "exact_duplicate": "Auto-corrected",
    "missing_timestamp": "Auto-corrected",
    "unit_conflict": "Flagged",
    "statistical_outlier": "Flagged",
}

# Risk-score scoring (deterministic).
RISK_WEIGHTS = {"impossible_value": 22, "compliance_contradiction": 25,
                "orphaned_customer": 20, "decimal_shift": 18, "duplicate_lot_number": 15,
                "exact_duplicate": 10, "near_duplicate_variant": 8, "unit_conflict": 8,
                "unit_format_drift": 6, "statistical_outlier": 5, "missing_timestamp": 3}
GEO_MULT = {"CRITICAL": 1.3, "HIGH": 1.15, "MEDIUM": 1.05, "LOW": 1.0}
# Geodo regulatory exposure adds a small additive nudge (keeps density ordering).
GEO_BONUS = {"CRITICAL": 8, "HIGH": 4, "MEDIUM": 2, "LOW": 0}

CHAT_SYSTEM_PROMPT = """You are the AssedGuard AI compliance advisor for a
manufacturing company preparing for an FDA audit. You have just completed a full
data-integrity audit of their dataset. Use ONLY this audit context to answer.

DATASET: {filename}
TOTAL ROWS SCANNED: {rows}
AUDIT DATE: {date}

SCOUT FINDINGS SUMMARY:
{scout_summary}

TOP RANKED FINDINGS (by audit risk):
{top_findings}

ACTIONS TAKEN:
- Auto-fixed: {fixed_count} issues ({fixed_details})
- Flagged for review: {flagged_count} issues
- Escalated (critical): {escalated_count} issues ({escalated_details})

REGULATORY ENTITY CONTEXT (from Geodo research):
{geodo_summary}

RULES:
- Answer only about THIS dataset and its findings.
- Reference specific finding IDs (F001, F002) and lot numbers when relevant.
- Plain English a non-technical compliance officer understands. Under 150 words.
- Never say "AI" — say "AssedGuard AI". Never say "I don't know".
- If asked "can I submit?": answer yes/no based on whether escalated items remain.
- Calm, specific, direct compliance expert."""


@app.on_event("startup")
async def startup():
    await init_cognee()
    # Warm PyMC's compile cache in the background so the first audit run is fast.
    try:
        import threading

        from utils import bayes
        if bayes.available():
            threading.Thread(target=bayes.warmup, daemon=True).start()
            print("[bayes] PyMC available — warming compile cache in background.")
        else:
            print("[bayes] PyMC not installed — Scout will use deterministic 3σ outliers.")
    except Exception as e:  # noqa: BLE001
        print(f"[bayes] warmup skipped ({e}).")


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# ---------------------------------------------------------------------------
# Narrative assembly helpers (regulatory + checklist sections for the PDF)
# ---------------------------------------------------------------------------
def _regulatory_md(c: dict) -> str:
    if not c:
        return ""
    lines = ["\n## Regulatory Context (Geodo)\n", c.get("geodo_summary", ""), "",
             "| Facility | Geodo matches | Highest action on record | Last action | Source |",
             "|----------|---------------|--------------------------|-------------|--------|"]
    for p in c.get("facility_profiles", []):
        lines.append(f"| {p['facility_code']} | {p['geodo_matches']} | {p['highest_severity']} "
                     f"| {p['last_action_date']} | {p['source']} |")
    lines.append(f"\n**Overall regulatory exposure:** {c.get('overall_regulatory_exposure', 'LOW')}")
    return "\n".join(lines) + "\n"


def _checklist_md(ck: dict) -> str:
    items = (ck or {}).get("checklist", [])
    if not items:
        return "\n## Pre-Audit Action Plan\n\nNo outstanding actions — dataset is in good standing.\n"
    lines = ["\n## Pre-Audit Action Plan\n",
             "| # | Priority | Action | Owner | Finding | Status |",
             "|---|----------|--------|-------|---------|--------|"]
    for it in items:
        lines.append(f"| {it['item_number']} | {it['priority']} | {it['action']} "
                     f"| {it['owner']} | {it['finding_id']} | [ ] |")
    return "\n".join(lines) + "\n"


@app.post("/run-audit")
async def run_audit(file: UploadFile = File(...),
                    customers: UploadFile | None = File(None)):
    """Run all 5 agents sequentially, streaming status as SSE events.

    Optional second file `customers` is the lookup table used for the
    orphaned-customer-reference check (Track 01 Class 4).
    """
    raw = await file.read()
    cust_raw = await customers.read() if customers is not None else None
    filename = file.filename or "dataset.csv"

    async def pipeline():
        try:
            df = pd.read_csv(io.BytesIO(raw))
        except Exception as e:  # noqa: BLE001
            yield _sse({"agent": "pipeline", "status": "error",
                        "message": f"Could not read CSV: {e}"})
            return

        customers_df = None
        if cust_raw:
            try:
                customers_df = pd.read_csv(io.BytesIO(cust_raw))
            except Exception as e:  # noqa: BLE001
                print(f"[run-audit] customer lookup unreadable ({e}); skipping orphan check.")

        session.update(df_original=df, filename=filename, status="running",
                       narrative_md=None, narrative_pdf=None, action_log=None,
                       checklist=None, regulatory=None)
        for k in session["agent_statuses"]:
            session["agent_statuses"][k] = "idle"
        await clear_session()

        # --- Agent 1: Scout ---------------------------------------------
        try:
            yield _sse({"agent": "scout", "status": "running"})
            session["agent_statuses"]["scout"] = "running"
            scout = await run_scout(df, customers_df)
            session["agent_statuses"]["scout"] = "complete"
            yield _sse({"agent": "scout", "status": "complete",
                        "summary": scout["summary"]})
        except Exception as e:  # noqa: BLE001
            session["agent_statuses"]["scout"] = "error"
            yield _sse({"agent": "scout", "status": "error", "message": str(e)})
            yield _sse({"agent": "pipeline", "status": "error", "message": str(e)})
            return

        # --- Agent 2: Ranker --------------------------------------------
        try:
            yield _sse({"agent": "ranker", "status": "running"})
            session["agent_statuses"]["ranker"] = "running"
            ranker = await run_ranker()
            session["agent_statuses"]["ranker"] = "complete"
            yield _sse({"agent": "ranker", "status": "complete",
                        "summary": ranker.get("stats", {})})
        except Exception as e:  # noqa: BLE001
            session["agent_statuses"]["ranker"] = "error"
            yield _sse({"agent": "ranker", "status": "error", "message": str(e)})
            yield _sse({"agent": "pipeline", "status": "error", "message": str(e)})
            return

        # --- Agent 3: Fixer ---------------------------------------------
        try:
            yield _sse({"agent": "fixer", "status": "running"})
            session["agent_statuses"]["fixer"] = "running"
            df_fixed, action_log = await run_fixer(df)
            session["df_fixed"] = df_fixed
            session["action_log"] = action_log
            session["agent_statuses"]["fixer"] = "complete"
            yield _sse({"agent": "fixer", "status": "complete",
                        "summary": action_log.get("stats", {})})
        except Exception as e:  # noqa: BLE001
            session["agent_statuses"]["fixer"] = "error"
            yield _sse({"agent": "fixer", "status": "error", "message": str(e)})
            yield _sse({"agent": "pipeline", "status": "error", "message": str(e)})
            return

        # --- Agent 4: Narrator ------------------------------------------
        try:
            yield _sse({"agent": "narrator", "status": "running"})
            session["agent_statuses"]["narrator"] = "running"
            narrative = await run_narrator(filename)
            session["narrative_md"] = narrative
            session["agent_statuses"]["narrator"] = "complete"
            yield _sse({"agent": "narrator", "status": "complete",
                        "summary": {"chars": len(narrative)}})
        except Exception as e:  # noqa: BLE001
            session["agent_statuses"]["narrator"] = "error"
            yield _sse({"agent": "narrator", "status": "error", "message": str(e)})
            yield _sse({"agent": "pipeline", "status": "error", "message": str(e)})
            return

        # --- Agent 5: Compliance Counsel (Geodo) ------------------------
        try:
            yield _sse({"agent": "counsel", "status": "running"})
            session["agent_statuses"]["counsel"] = "running"
            counsel = await run_counsel(df)
            session["regulatory"] = counsel
            mem = await read_all_memory()
            checklist = build_action_checklist(mem)
            session["checklist"] = checklist
            # Assemble final narrative (base + regulatory + checklist) -> PDF.
            final_md = ((session.get("narrative_md") or "")
                        + _regulatory_md(counsel) + _checklist_md(checklist))
            session["narrative_md"] = final_md
            session["narrative_pdf"] = markdown_to_pdf(final_md)
            session["agent_statuses"]["counsel"] = "complete"
            yield _sse({"agent": "counsel", "status": "complete",
                        "summary": {"facilities_researched": len(counsel["facility_profiles"]),
                                    "exposure": counsel["overall_regulatory_exposure"]}})
        except Exception as e:  # noqa: BLE001
            session["agent_statuses"]["counsel"] = "error"
            # Never break the demo: still build a PDF from the base narrative.
            if session.get("narrative_md") and not session.get("narrative_pdf"):
                try:
                    session["narrative_pdf"] = markdown_to_pdf(session["narrative_md"])
                except Exception:  # noqa: BLE001
                    pass
            yield _sse({"agent": "counsel", "status": "error", "message": str(e)})

        session["status"] = "complete"
        yield _sse({"agent": "pipeline", "status": "complete"})

    return StreamingResponse(pipeline(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/findings")
async def get_findings():
    """Ranker output merged with Fixer's actions, for the findings table."""
    ranker = await read_memory("ranker") or {}
    fixer = await read_memory("fixer") or {}
    ranked = ranker.get("ranked", [])

    action_by_id = {}
    for e in fixer.get("auto_fixed", []):
        action_by_id[e["finding_id"]] = e.get("action_taken", "Auto-corrected")
    for e in fixer.get("flagged", []):
        action_by_id[e["finding_id"]] = "Flagged for review"
    for e in fixer.get("escalated", []):
        action_by_id[e["finding_id"]] = "Corrected — verify"

    findings = []
    for r in ranked:
        findings.append({
            "rank": r["rank"],
            "finding_id": r["finding_id"],
            "issue_type": ISSUE_LABEL.get(r["issue_type"], r["issue_type"]),
            "severity": r["severity"],
            "rows_affected": r["rows_affected"],
            "ranking_reason": r.get("ranking_reason", ""),
            "regulation": r.get("regulation", ""),
            "action_taken": action_by_id.get(r["finding_id"],
                                              ACTION_LABEL.get(r["issue_type"], "Reviewed")),
            "claude_note": r.get("claude_note", ""),
            "lot_numbers": r.get("lot_numbers", []),
            "bayesian_probability": r.get("raw_values", {}).get("bayesian_probability"),
            "credible_range": r.get("raw_values", {}).get("credible_range"),
        })

    stats = dict(ranker.get("stats", {"HIGH": 0, "MED": 0, "LOW": 0}))
    fstats = fixer.get("stats", {})
    stats["auto_fixed"] = fstats.get("fixed", 0)
    stats["flagged"] = fstats.get("flagged", 0)
    stats["escalated"] = fstats.get("escalated", 0)
    return {"findings": findings, "stats": stats}


SEV_WEIGHT = {"HIGH": 1.0, "MED": 0.45, "LOW": 0.2}
SEV_FLOOR = {"HIGH": 70, "MED": 35, "LOW": 10}
SEV_RANK = {"HIGH": 0, "MED": 1, "LOW": 2}


@app.get("/risk-score")
async def get_risk_score():
    """Severity-weighted DENSITY score (0–100): how widespread + how severe the
    data-integrity problems are, relative to the dataset size. A floor by worst
    severity keeps any critical finding visible; density above the floor reflects
    how much of the dataset is actually affected, so the score is gradated rather
    than pegged at 100. Nudged by Geodo regulatory exposure."""
    scout = await read_memory("scout") or {}
    counsel = await read_memory("counsel") or {}
    findings = scout.get("findings", [])
    total_rows = max(1, scout.get("summary", {}).get("rows_scanned", 0) or len(findings))

    if not findings:
        return {"score": 0, "level": "LOW", "color": "#059669", "breakdown": [],
                "geodo_multiplier": 1.0,
                "plain_english": ("Your dataset carries a LOW audit risk score of 0/100. No "
                                  "data-integrity issues were detected; it is ready for audit review.")}

    # Aggregate per issue type: findings count, affected rows, severity-weighted load.
    agg, affected = {}, set()
    worst = "LOW"
    for f in findings:
        rows = f.get("row_ids", [])
        sev = f.get("severity", "MED")
        if SEV_RANK.get(sev, 9) < SEV_RANK.get(worst, 9):
            worst = sev
        affected.update(rows)
        a = agg.setdefault(f["issue_type"], {"count": 0, "rows": 0, "weighted": 0.0})
        a["count"] += 1
        a["rows"] += len(rows)
        a["weighted"] += SEV_WEIGHT.get(sev, 0.3) * len(rows)

    weighted_rows = sum(a["weighted"] for a in agg.values())
    density = min(1.0, weighted_rows / total_rows)          # severity-weighted share of rows
    floor = SEV_FLOOR.get(worst, 0)
    base = floor + (100 - floor) * density                 # floor + how widespread
    bonus = GEO_BONUS.get(counsel.get("overall_regulatory_exposure", "LOW"), 0)
    score = max(0, min(100, round(base) + bonus))

    if score >= 90:
        level, color = "CRITICAL", "#DC2626"
    elif score >= 70:
        level, color = "HIGH", "#D97706"
    elif score >= 40:
        level, color = "MODERATE", "#CA8A04"
    else:
        level, color = "LOW", "#059669"

    # Drivers: each type's share of the score (sums ≈ score).
    breakdown = []
    for it, a in agg.items():
        share = round((a["weighted"] / weighted_rows) * score) if weighted_rows else 0
        breakdown.append({"issue_type": ISSUE_LABEL.get(it, it), "count": a["count"],
                          "rows": a["rows"], "total_pts": share})
    breakdown.sort(key=lambda b: b["total_pts"], reverse=True)

    pct = round(len(affected) / total_rows * 100, 1)
    d = breakdown[0]
    plural = "s" if d["count"] != 1 else ""
    verb = "is" if d["count"] == 1 else "are"
    plain = (f"Your dataset carries a {level} audit risk score of {score}/100 — integrity issues "
             f"affect about {pct}% of your {total_rows} records. The {d['count']} "
             f"{d['issue_type'].lower()}{plural} {verb} the primary driver. Resolve the critical "
             "items before your FDA audit.")

    return {"score": score, "level": level, "color": color, "breakdown": breakdown,
            "geodo_bonus": bonus, "affected_pct": pct, "plain_english": plain}


@app.get("/action-checklist")
async def get_action_checklist():
    """Pre-audit action plan (cached from the pipeline, or built on demand)."""
    if session.get("checklist"):
        return session["checklist"]
    mem = await read_all_memory()
    ck = build_action_checklist(mem)
    session["checklist"] = ck
    return ck


FLAG_COL = "audit_flag"


def _row_str(s: pd.Series) -> str:
    keys = [k for k in ["lot_number", "quantity", "unit", "temperature_c"] if k in s.index]
    return " · ".join(f"{k}={s[k]}" for k in keys)


def _norm(v) -> str:
    return ("" if v is None else str(v)).strip()


@app.get("/data-diff")
async def get_data_diff():
    """True cell-by-cell before/after diff of original vs corrected dataset."""
    orig, fixed = session.get("df_original"), session.get("df_fixed")
    if orig is None or fixed is None:
        return JSONResponse(status_code=425, content={"detail": "No audit run yet."})
    fixer = await read_memory("fixer") or {}
    row_actions = {int(k): v for k, v in (fixer.get("row_actions") or {}).items()}
    removed_rows = {int(k): v for k, v in (fixer.get("removed_rows") or {}).items()}

    changed, cells = [], 0

    # Removed rows (exact duplicates).
    for r in sorted(removed_rows):
        if r in orig.index:
            info = removed_rows[r]
            lot = str(orig.at[r, "lot_number"]) if "lot_number" in orig.columns else ""
            changed.append({"row_index": r, "lot_number": lot,
                            "finding_id": info.get("finding_id", ""), "action_type": "removed",
                            "changes": [{"column": "(entire row)", "before": _row_str(orig.loc[r]),
                                         "after": f"REMOVED — duplicate of row {info.get('kept_row_id')}"}],
                            "reason": info.get("reason", "")})
            cells += 1

    # Modified cells (rows present in both, compared column-by-column).
    for r in [i for i in orig.index if i in fixed.index]:
        diffs = []
        for col in orig.columns:
            if col == FLAG_COL:
                continue
            a = _norm(orig.at[r, col])
            b = _norm(fixed.at[r, col]) if col in fixed.columns else a
            if a != b:
                diffs.append({"column": col, "before": a or "(empty)", "after": b or "(empty)"})
        if diffs:
            act = row_actions.get(r, {})
            lot = str(fixed.at[r, "lot_number"]) if "lot_number" in fixed.columns else ""
            changed.append({"row_index": r, "lot_number": lot,
                            "finding_id": act.get("finding_id", ""),
                            "action_type": act.get("action_type", "modified"),
                            "changes": diffs, "reason": act.get("reason", "")})
            cells += len(diffs)

    rows_removed = len(removed_rows)
    rows_modified = sum(1 for c in changed if c["action_type"] != "removed")
    return {"changed_rows": changed, "rows_removed": rows_removed,
            "rows_modified": rows_modified, "rows_flagged": 0,
            "total_cells_changed": cells}


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = []


def _chat_context(mem: dict) -> dict:
    scout = mem.get("scout") or {}
    ranker = mem.get("ranker") or {}
    fixer = mem.get("fixer") or {}
    counsel = mem.get("counsel") or {}
    summary = scout.get("summary", {})
    ranked = ranker.get("ranked", [])
    top = "\n".join(
        f"- {r['finding_id']} [{r['severity']}] {r['issue_type']} "
        f"(lots {', '.join(r.get('lot_numbers', [])) or 'n/a'}): {r.get('ranking_reason', '')}"
        for r in ranked[:10]) or "None."
    fstats = fixer.get("stats", {})
    return {
        "filename": session.get("filename", "dataset.csv"),
        "rows": summary.get("rows_scanned", 0),
        "date": datetime.now().strftime("%B %d, %Y"),
        "scout_summary": (f"{summary.get('total', 0)} issues — {summary.get('HIGH', 0)} critical, "
                          f"{summary.get('MED', 0)} moderate, {summary.get('LOW', 0)} minor."),
        "top_findings": top,
        "fixed_count": fstats.get("fixed", 0),
        "fixed_details": "; ".join(e.get("action_taken", "") for e in fixer.get("auto_fixed", [])[:4]) or "none",
        "flagged_count": fstats.get("flagged", 0),
        "escalated_count": fstats.get("escalated", 0),
        "escalated_details": "; ".join(e.get("finding_id", "") for e in fixer.get("escalated", [])[:5]) or "none",
        "geodo_summary": counsel.get("geodo_summary", "No regulatory context available."),
    }


def _fallback_reply(message: str, mem: dict, ctx: dict) -> str:
    m = message.lower()
    ranker = mem.get("ranker") or {}
    fixer = mem.get("fixer") or {}
    counsel = mem.get("counsel") or {}
    ranked = ranker.get("ranked", [])
    esc = fixer.get("escalated", [])
    stats = ranker.get("stats", {})
    high = stats.get("HIGH", 0)

    # Lot-specific questions take priority over generic submit/ready intent.
    lot_q = re.search(r"lot[\s-]*([0-9]{3,})", m) or (re.search(r"\b(\d{3,})\b", m) if "lot" in m else None)
    if "lot" in m and lot_q:
        token = lot_q.group(1)
        for r in ranked:
            if any(token in str(l) for l in r.get("lot_numbers", [])):
                return (f"Lot matching '{token}' appears in finding {r['finding_id']} ({r['issue_type']}). "
                        f"{r.get('ranking_reason', '')}")
        return f"No findings are tied to a lot matching '{token}'. That lot passed all integrity checks."

    if any(k in m for k in ["submit", "ready", "tomorrow", "sign off", "sign-off", "good to go"]):
        if esc:
            ids = ", ".join(e["finding_id"] for e in esc[:5])
            return (f"Not yet. You have {len(esc)} escalated critical item(s) ({ids}) that require human "
                    "sign-off before submission. Clear every BEFORE AUDIT item in your action plan first.")
        if high:
            return (f"Almost. There are no escalated blockers, but {high} high-severity finding(s) should be "
                    "verified first. Once those are checked off, you are ready to submit.")
        return ("Yes. No critical issues or escalations remain — this dataset is in good standing and ready "
                "to hand to your auditor.")

    if any(k in m for k in ["urgent", "most important", "first", "priority", "biggest", "single"]):
        if ranked:
            r = ranked[0]
            return f"The single most urgent item is {r['finding_id']}: {r.get('ranking_reason', '')}"
        return "Nothing urgent — no issues were found in this dataset."

    if any(k in m for k in ["auto", "correct", "fixed", "verify"]):
        af = fixer.get("auto_fixed", [])
        if af:
            items = "; ".join(f"{e['finding_id']} — {e.get('action_taken', '')}" for e in af[:6])
            return (f"AssedGuard AI auto-corrected {len(af)} item(s): {items}. Confirm each in your source "
                    "system of record before submission.")
        return "No automatic corrections were applied to this dataset."

    if any(k in m for k in ["contradiction", "temperature", "pass", "over-temp", "overtemp"]):
        cons = [r for r in ranked if "contradiction" in r["issue_type"]]
        if cons:
            ids = ", ".join(r["finding_id"] for r in cons)
            return (f"There are {len(cons)} compliance contradiction(s) ({ids}): records marked PASS whose "
                    "temperature exceeded the 85°C FDA limit. A passing record cannot be out of specification, "
                    "so each must be re-tested or signed off by an engineer before the audit.")
        return "No compliance contradictions were found — no PASS records are out of temperature specification."

    if any(k in m for k in ["geodo", "regulatory", "facility", "exposure", "history"]):
        return counsel.get("geodo_summary", "No regulatory context is available for this dataset.")

    s = (mem.get("scout") or {}).get("summary", {})
    return (f"AssedGuard AI scanned {s.get('rows_scanned', 'the')} records and found {s.get('total', 0)} issues "
            f"({s.get('HIGH', 0)} critical, {s.get('MED', 0)} moderate, {s.get('LOW', 0)} minor). Regulatory "
            f"exposure is {counsel.get('overall_regulatory_exposure', 'LOW')}. Ask me about a specific lot, the "
            "most urgent fix, or whether you can submit.")


@app.post("/chat")
async def chat(req: ChatRequest):
    """Answer questions about THIS dataset using the full pipeline memory."""
    mem = await read_all_memory()
    ctx = _chat_context(mem)
    history = [{"role": h.get("role", "user"), "content": h.get("content", "")}
               for h in (req.history or [])][-8:]
    convo = "\n".join(f"{h['role'].upper()}: {h['content']}" for h in history)
    convo = (convo + "\n" if convo else "") + f"USER: {req.message}"
    reply = await ask_claude(prompt=convo, system=CHAT_SYSTEM_PROMPT.format(**ctx),
                             max_tokens=512, fallback="")
    if not reply:
        reply = _fallback_reply(req.message, mem, ctx)
    refs = sorted(set(re.findall(r"F\d{3}", reply)))
    return {"reply": reply, "finding_refs": refs}


@app.get("/regulatory")
async def get_regulatory():
    """Counsel (Geodo) regulatory context for the UI, if needed."""
    return await read_memory("counsel") or {}


@app.get("/download-narrative")
async def download_narrative():
    """Return the audit narrative as a downloadable PDF."""
    pdf = session.get("narrative_pdf")
    if not pdf:
        return JSONResponse(status_code=425,
                            content={"detail": "Narrative not ready. Run an audit first."})
    date_code = datetime.now().strftime("%Y%m%d")
    fn = f"AssedGuard_Narrative_{date_code}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fn}"'})


@app.get("/download-corrected")
async def download_corrected():
    """Download the corrected CSV the Fixer produced."""
    df = session.get("df_fixed")
    if df is None:
        return JSONResponse(status_code=425, content={"detail": "No corrected data yet."})
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    date_code = datetime.now().strftime("%Y%m%d")
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode()), media_type="text/csv",
        headers={"Content-Disposition":
                 f'attachment; filename="AssedGuard_Corrected_{date_code}.csv"'})


@app.get("/sample")
async def sample():
    """Serve the bundled demo dataset for one-click 'Run sample' in the UI."""
    path = os.path.join(os.path.dirname(__file__), "..", "data", "sample.csv")
    if not os.path.exists(path):
        return JSONResponse(status_code=404, content={"detail": "Sample not found."})
    return FileResponse(path, media_type="text/csv", filename="sample.csv")


@app.get("/status")
async def get_status():
    return session["agent_statuses"]


@app.get("/health")
async def health():
    return {"status": "ok", "service": "AssedGuard AI"}
