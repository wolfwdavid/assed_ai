"""
Agent 4 — Narrator
Reads ALL agent memory from Cognee (scout, ranker, fixer) and writes a
plain-English, signed-ready audit narrative as markdown. The full document is
built deterministically (so a real PDF is always produced); the Executive
Summary paragraph is optionally polished by Claude. Writes the narrative to
Cognee under "narrator" and returns the markdown string.
"""
from datetime import datetime

from memory.cognee_store import read_all_memory, write_memory
from utils.ai import ask_claude

NARRATIVE_SYSTEM_PROMPT = """
You are writing an official data integrity correction summary for a
manufacturing compliance officer to submit to a regulatory auditor.

Rules:
- Write in plain English. Zero technical jargon.
- Every claim must be traceable to a specific finding ID (F001, F002, etc.)
- Severity language: "critical" for HIGH, "moderate" for MED, "minor" for LOW
- Do not say "AI", "model", "algorithm", or "automated system" — say "AssedGuard AI"
- Every finding must have a reason a human can follow and sign off on
- The tone is formal but readable — this is a legal document
- Do not make up any findings. Only report what is in the data provided.
"""

SEV_WORD = {"HIGH": "critical", "MED": "moderate", "LOW": "minor"}
ISSUE_LABEL = {
    "compliance_contradiction": "Compliance contradiction (PASS with out-of-spec temperature)",
    "duplicate_lot_number": "Conflicting quantities for one lot number",
    "exact_duplicate": "Exact duplicate record",
    "unit_conflict": "Inconsistent unit of measure",
    "statistical_outlier": "Out-of-range sensor reading",
    "missing_timestamp": "Missing batch date",
}
ACTION_WORD = {
    "compliance_contradiction": "Escalated for sign-off",
    "duplicate_lot_number": "Escalated for sign-off",
    "exact_duplicate": "Auto-corrected",
    "missing_timestamp": "Auto-corrected",
    "unit_conflict": "Flagged for review",
    "statistical_outlier": "Flagged for review",
}


def _esc(text: str) -> str:
    return str(text).replace("|", "/")


async def run_narrator(original_filename: str) -> str:
    mem = await read_all_memory()
    scout = mem.get("scout") or {}
    ranker = mem.get("ranker") or {}
    fixer = mem.get("fixer") or {}

    ranked = ranker.get("ranked", [])
    stats = ranker.get("stats", {"HIGH": 0, "MED": 0, "LOW": 0})
    fstats = fixer.get("stats", {"fixed": 0, "flagged": 0, "escalated": 0})
    rows_scanned = scout.get("summary", {}).get("rows_scanned", 0)
    total = scout.get("summary", {}).get("total", len(ranked))

    today = datetime.now().strftime("%B %d, %Y")
    date_code = datetime.now().strftime("%Y%m%d")

    # --- Executive Summary (deterministic, optionally polished) ----------
    det_exec = (
        f"AssedGuard AI scanned {rows_scanned} records in {original_filename} and "
        f"identified {total} data integrity issues: {stats.get('HIGH',0)} critical, "
        f"{stats.get('MED',0)} moderate, and {stats.get('LOW',0)} minor. "
        f"Of these, {fstats.get('fixed',0)} were auto-corrected with a logged reason, "
        f"{fstats.get('flagged',0)} were flagged for review, and "
        f"{fstats.get('escalated',0)} were escalated for mandatory human sign-off "
        "before regulatory submission."
    )
    exec_summary = await ask_claude(
        prompt=("Rewrite this audit executive summary in exactly three formal, "
                "plain-English sentences for a regulator. Keep all numbers identical. "
                "Do not mention AI; refer to the system as 'AssedGuard AI':\n\n"
                + det_exec),
        system=NARRATIVE_SYSTEM_PROMPT,
        max_tokens=220,
        fallback=det_exec,
    )

    # --- Findings table --------------------------------------------------
    rows_md = []
    for r in ranked:
        issue = ISSUE_LABEL.get(r["issue_type"], r["issue_type"])
        action = ACTION_WORD.get(r["issue_type"], "Reviewed")
        sev = SEV_WORD.get(r["severity"], r["severity"].lower())
        reason = _esc(r.get("ranking_reason", ""))
        rows_md.append(
            f"| {r['finding_id']} | {_esc(issue)} | {sev} | "
            f"{r['rows_affected']} | {action} | {reason} |"
        )
    table = "\n".join(rows_md) if rows_md else "| — | No issues found | — | 0 | — | — |"

    # --- Open items (escalated) ------------------------------------------
    open_items = fixer.get("escalated", [])
    if open_items:
        open_md = "\n".join(
            f"- **{e['finding_id']}** — {_esc(e.get('escalation_text',''))} "
            f"_Reason: {_esc(e.get('reason',''))}_"
            for e in open_items
        )
    else:
        open_md = "- None. No findings required human escalation."

    # --- Auto-corrections -------------------------------------------------
    fixed = fixer.get("auto_fixed", [])
    if fixed:
        fixed_md = "\n".join(
            f"- **{e['finding_id']}** — {_esc(e.get('action_taken',''))} "
            f"({e.get('rows_affected',0)} row(s)). _Reason: {_esc(e.get('reason',''))}_"
            for e in fixed
        )
    else:
        fixed_md = "- None. No issues were eligible for automatic correction."

    flagged = fixer.get("flagged", [])
    if flagged:
        flagged_md = "\n".join(
            f"- {_esc(e.get('flag_text',''))} _Reason: {_esc(e.get('reason',''))}_"
            for e in flagged
        )
    else:
        flagged_md = "- None."

    narrative = f"""# Data Integrity Correction Summary
**Dataset:** {original_filename}
**Audit Date:** {today}
**Prepared by:** AssedGuard AI
**Reference ID:** AUDIT-{date_code}

## Executive Summary
{exec_summary}

## Findings & Actions Taken

| Finding ID | Issue | Severity | Rows | Action | Reason |
|------------|-------|----------|------|--------|--------|
{table}

## Open Items Requiring Human Sign-Off
{open_md}

## Items Flagged for Verification
{flagged_md}

## Auto-Corrections Applied
{fixed_md}

## Certification
I certify that this Data Integrity Correction Summary accurately reflects the
review conducted by AssedGuard AI on {today}. All auto-corrections were logged
with traceable reasons. Open items have been escalated for human verification
prior to regulatory submission.

Signed: _________________________ Title: _________________________

Date: __________________________ Facility: ______________________
"""

    await write_memory("narrator", {"narrative_md": narrative,
                                     "reference_id": f"AUDIT-{date_code}"})
    return narrative
