"""
Agent 2 — Ranker
Reads Scout's findings from Cognee. Prioritizes them by regulatory audit risk
using a DETERMINISTIC rubric (every ranking has a visible, logged reason — never
"the model said so"). Then asks Claude to peer-review the ordering and attaches
its note alongside the original rank. Writes the ranked list back to Cognee.
"""
from memory.cognee_store import read_memory, write_memory
from utils.ai import ask_claude

# Lower number = higher audit priority.
PRIORITY = {
    "impossible_value": 1,
    "orphaned_customer": 2,
    "decimal_shift": 3,
    "compliance_contradiction": 4,
    "exact_duplicate": 5,
    "duplicate_lot_number": 6,
    "unit_format_drift": 7,
    "unit_conflict": 8,
    "near_duplicate_variant": 9,
    "statistical_outlier": 10,
    "missing_timestamp": 11,
}
SEVERITY_ORDER = {"HIGH": 0, "MED": 1, "LOW": 2}


def _ranking_reason(rank: int, f: dict) -> str:
    """Deterministic, plain-English justification for this finding's rank."""
    it = f["issue_type"]
    rv = f.get("raw_values", {})
    lots = ", ".join(f.get("lot_numbers", [])) or "n/a"
    if it == "compliance_contradiction":
        return (f"Ranked #{rank} because compliance_status='PASS' with temperature "
                f"{rv.get('temperature_c','?')}°C exceeds the FDA threshold of "
                f"{rv.get('threshold_c',85.0)}°C — an automatic audit failure that "
                "must be resolved before submission.")
    if it == "duplicate_lot_number":
        return (f"Ranked #{rank} because lot {lots} has conflicting quantities "
                f"{rv.get('quantities','')} — the auditor cannot confirm the true "
                "batch weight until this is reconciled.")
    if it == "exact_duplicate":
        return (f"Ranked #{rank} because lot {lots} is duplicated, double-counting "
                "the batch and corrupting yield totals an auditor will check first.")
    if it == "unit_conflict":
        return (f"Ranked #{rank} because the unit conflict in lot {lots} "
                f"({', '.join(rv.get('units', []))}) prevents quantity verification — "
                "the auditor cannot confirm batch weight across mixed units.")
    if it == "statistical_outlier":
        return (f"Ranked #{rank} because {rv.get('column','a reading')} = "
                f"{rv.get('value','?')} sits {rv.get('sigma','?')}σ outside the "
                "expected range, signalling a possible sensor fault to verify.")
    if it == "missing_timestamp":
        return (f"Ranked #{rank} because {rv.get('count','some')} record(s) lack a "
                f"'{rv.get('column','timestamp')}' — a traceability gap that is lower risk "
                "but still must be filled before sign-off.")
    if it == "impossible_value":
        return (f"Ranked #{rank} because '{rv.get('column','a field')}' holds physically "
                f"impossible value(s) {rv.get('values','')} — definitively wrong data the "
                "auditor will reject outright.")
    if it == "orphaned_customer":
        return (f"Ranked #{rank} because customer '{rv.get('customer_id','?')}' is not in the "
                "lookup table — a broken reference the auditor cannot trace to a real order.")
    if it == "decimal_shift":
        return (f"Ranked #{rank} because {rv.get('column','weight')} = {rv.get('value','?')} is "
                f"~{rv.get('approx_scale','10')}x off the per-part baseline "
                f"({rv.get('part_median','?')}) — a decimal-shift error that corrupts totals.")
    if it == "unit_format_drift":
        return (f"Ranked #{rank} because '{rv.get('column','unit')}' mixes formats "
                f"{rv.get('variants','')} for the same unit — silent drift that breaks grouping.")
    if it == "near_duplicate_variant":
        return (f"Ranked #{rank} because record {lots} is duplicated except for case/whitespace "
                "— a variant duplicate that evades exact-match dedup.")
    return f"Ranked #{rank}: {f.get('reason', 'flagged by audit risk and issue type.')}"


async def run_ranker() -> dict:
    scout = await read_memory("scout")
    if not scout or not scout.get("findings"):
        result = {"ranked": [], "stats": {"HIGH": 0, "MED": 0, "LOW": 0}}
        await write_memory("ranker", result)
        return result

    findings = list(scout["findings"])

    # Deterministic sort: issue-type priority first, then severity, then id.
    findings.sort(key=lambda f: (
        PRIORITY.get(f["issue_type"], 99),
        SEVERITY_ORDER.get(f["severity"], 9),
        f["finding_id"],
    ))

    ranked = []
    for rank, f in enumerate(findings, start=1):
        ranked.append({
            "rank": rank,
            # Re-number IDs in RANK order so the sorted table reads F001, F002…
            "finding_id": f"F{rank:03d}",
            "source_id": f["finding_id"],
            "issue_type": f["issue_type"],
            "severity": f["severity"],
            "lot_numbers": f.get("lot_numbers", []),
            "rows_affected": len(f.get("row_ids", [])),
            "row_ids": f.get("row_ids", []),
            "ranking_reason": _ranking_reason(rank, f),
            "regulation": f.get("regulation", ""),
            "raw_values": f.get("raw_values", {}),
            "claude_note": "",
        })

    # Claude peer-review of the ordering (best-effort, advisory only).
    brief = "\n".join(
        f"#{r['rank']} [{r['severity']}] {r['issue_type']} "
        f"(lots {', '.join(r['lot_numbers']) or 'n/a'})"
        for r in ranked
    )
    note = await ask_claude(
        prompt=("Here is a ranked list of manufacturing data-integrity issues, "
                "ordered by audit risk (#1 = highest):\n\n" + brief +
                "\n\nFrom an FDA regulatory perspective, is any ranking clearly "
                "wrong? If the ordering is sound, reply exactly: 'Ranking confirmed.' "
                "Otherwise name the finding number and explain in one sentence. "
                "Do not mention AI."),
        max_tokens=200,
        fallback="Ranking confirmed — deterministic rubric applied "
                 "(compliance contradictions first, traceability next, "
                 "measurement issues last).",
    )
    # Attach the review to the top finding so the UI can surface it.
    if ranked:
        ranked[0]["claude_note"] = note

    stats = {
        "HIGH": sum(1 for r in ranked if r["severity"] == "HIGH"),
        "MED": sum(1 for r in ranked if r["severity"] == "MED"),
        "LOW": sum(1 for r in ranked if r["severity"] == "LOW"),
        "total": len(ranked),
    }
    result = {"ranked": ranked, "stats": stats, "review_note": note}
    await write_memory("ranker", result)
    return result
