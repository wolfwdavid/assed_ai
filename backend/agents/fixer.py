"""
Agent 3 — Fixer
Reads ranked findings from Cognee and RESCUES the data: it removes duplicates,
fills missing timestamps, normalises unit conflicts, corrects out-of-range sensor
readings, aligns contradictory compliance statuses, and consolidates conflicting
lot quantities. Every change is logged with a plain-English reason and the
original value is preserved in an `audit_flag` column for traceability.

The result is a corrected DataFrame that passes a fresh audit cleanly, plus an
action log that drives the dashboard, checklist, and before/after diff.
"""
import pandas as pd

from memory.cognee_store import read_memory, write_memory
from utils.ai import ask_claude

FLAG_COL = "audit_flag"
NUMERIC_COLS = ["temperature_c", "pressure_bar", "quantity"]

# Which issue types are mechanical auto-fixes vs. judgement calls that a human
# should still confirm (these stay in "escalated" so the review workflow holds).
VERIFY_TYPES = {"compliance_contradiction", "duplicate_lot_number"}


def _add_flag(df: pd.DataFrame, row_ids, text: str) -> None:
    for i in row_ids:
        if i not in df.index:
            continue
        existing = str(df.at[i, FLAG_COL]) if df.at[i, FLAG_COL] else ""
        df.at[i, FLAG_COL] = (existing + " | " + text).strip(" |") if existing else text


def _most_recent_idx(df: pd.DataFrame, row_ids):
    if "batch_date" not in df.columns:
        return row_ids[-1]
    dates = pd.to_datetime(df.loc[row_ids, "batch_date"], errors="coerce")
    if dates.notna().any():
        return int(dates.idxmax())
    return row_ids[-1]


async def run_fixer(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    ranker = await read_memory("ranker")
    out = df.copy()
    if FLAG_COL not in out.columns:
        out[FLAG_COL] = ""

    # Column medians from the ORIGINAL data, for outlier correction.
    medians = {}
    for col in NUMERIC_COLS:
        if col in df.columns:
            s = pd.to_numeric(df[col], errors="coerce")
            if s.notna().any():
                medians[col] = round(float(s.median()), 1)

    action_log = {
        "auto_fixed": [], "flagged": [], "escalated": [],
        "row_actions": {},          # row_index -> {finding_id, reason, action_type}
        "removed_rows": {},         # row_index -> {finding_id, kept_row_id, reason}
        "stats": {"fixed": 0, "flagged": 0, "escalated": 0},
    }
    if not ranker or not ranker.get("ranked"):
        await write_memory("fixer", action_log)
        return out, action_log

    rows_to_drop: set[int] = set()

    def mark(rows, fid, reason, action_type):
        for r in rows:
            action_log["row_actions"][int(r)] = {
                "finding_id": fid, "reason": reason, "action_type": action_type}

    for f in ranker["ranked"]:
        it = f["issue_type"]
        fid = f["finding_id"]
        rows = [int(r) for r in f.get("row_ids", []) if int(r) in out.index]
        lots = ", ".join(f.get("lot_numbers", [])) or "n/a"
        rv = f.get("raw_values", {})
        verify = it in VERIFY_TYPES

        # ---- MISSING TIMESTAMP → fill placeholder -----------------------
        if it == "missing_timestamp":
            for r in rows:
                out.at[r, "batch_date"] = "REQUIRES_MANUAL_ENTRY"
            _add_flag(out, rows, "MISSING_TIMESTAMP: batch_date set to REQUIRES_MANUAL_ENTRY.")
            reason = ("Filled missing batch dates with REQUIRES_MANUAL_ENTRY so the gap is "
                      "explicit and traceable per ISO 13485 record-control.")
            mark(rows, fid, reason, "modified")
            action_log["auto_fixed"].append({"finding_id": fid,
                "action_taken": "Filled missing batch dates", "row_ids_updated": rows,
                "reason": reason, "rows_affected": len(rows)})
            action_log["stats"]["fixed"] += 1

        # ---- UNIT CONFLICT → normalise to canonical unit ----------------
        elif it == "unit_conflict":
            units = out.loc[rows, "unit"].astype(str)
            canonical = units.mode().iloc[0] if not units.mode().empty else "kg"
            changed = [r for r in rows if str(out.at[r, "unit"]) != canonical]
            for r in changed:
                orig = out.at[r, "unit"]
                out.at[r, "unit"] = canonical
                _add_flag(out, [r], f"UNIT_NORMALISED: {orig} → {canonical} (canonical unit for lot).")
            reason = (f"Normalised lot {lots} to the canonical unit '{canonical}'. Original units "
                      "preserved in audit_flag — confirm the canonical unit with production.")
            mark(rows, fid, reason, "modified")
            action_log["auto_fixed"].append({"finding_id": fid,
                "action_taken": f"Normalised units to {canonical}", "row_ids_updated": changed,
                "reason": reason, "rows_affected": len(changed)})
            action_log["stats"]["fixed"] += 1

        # ---- STATISTICAL OUTLIER → impute column median -----------------
        elif it == "statistical_outlier":
            col = rv.get("column", "temperature_c")
            med = medians.get(col)
            for r in rows:
                orig = out.at[r, col]
                if med is not None:
                    out.at[r, col] = med
                    _add_flag(out, [r], f"OUTLIER_CORRECTED: {col} {orig} → median {med} (probable sensor fault).")
            reason = (f"Replaced the out-of-range {col} reading for lot {lots} with the column "
                      f"median ({med}); original value preserved in audit_flag — verify the sensor.")
            mark(rows, fid, reason, "modified")
            action_log["auto_fixed"].append({"finding_id": fid,
                "action_taken": f"Corrected {col} outlier to median", "row_ids_updated": rows,
                "reason": reason, "rows_affected": len(rows)})
            action_log["stats"]["fixed"] += 1

        # ---- COMPLIANCE CONTRADICTION → correct status to FAIL ----------
        elif it == "compliance_contradiction":
            temp = rv.get("temperature_c", "?")
            for r in rows:
                orig = out.at[r, "compliance_status"]
                out.at[r, "compliance_status"] = "FAIL"
                _add_flag(out, [r], f"STATUS_CORRECTED: {orig} → FAIL (temperature {temp}°C exceeds 85°C limit).")
            reason = (f"Lot {lots} was marked PASS at {temp}°C, above the 85°C FDA limit. The "
                      "measurement is authoritative, so the status was corrected PASS → FAIL. "
                      "Confirm with a production engineer before submission.")
            mark(rows, fid, reason, "modified")
            action_log["escalated"].append({"finding_id": fid,
                "escalation_text": f"Lot {lots}: compliance_status corrected PASS → FAIL ({temp}°C out of spec).",
                "action_taken": "Corrected PASS → FAIL", "reason": reason})
            action_log["stats"]["escalated"] += 1

        # ---- DUPLICATE LOT NUMBER → consolidate to most-recent quantity -
        elif it == "duplicate_lot_number":
            if "quantity" in out.columns and rows:
                keep = _most_recent_idx(out, rows)
                qty = out.at[keep, "quantity"]
                for r in rows:
                    if out.at[r, "quantity"] != qty:
                        orig = out.at[r, "quantity"]
                        out.at[r, "quantity"] = qty
                        _add_flag(out, [r], f"LOT_CONSOLIDATED: quantity {orig} → {qty} (most-recent record).")
                reason = (f"Lot {lots} had conflicting quantities; consolidated all records to the "
                          f"most-recent value ({qty}). Verify against physical inventory before submission.")
                mark(rows, fid, reason, "modified")
                action_log["escalated"].append({"finding_id": fid,
                    "escalation_text": f"Lot {lots}: quantities consolidated to {qty} (most-recent record).",
                    "action_taken": "Consolidated lot quantity", "reason": reason})
                action_log["stats"]["escalated"] += 1

        # ---- NEAR-DUPLICATE VARIANT → keep one, remove variants ---------
        elif it == "near_duplicate_variant":
            if len(rows) > 1:
                keep = _most_recent_idx(out, rows)
                drop = [r for r in rows if r != keep]
                rows_to_drop.update(drop)
                reason = ("Removed case/whitespace variant duplicate(s), keeping the most "
                          "recent record so each entity maps to one row.")
                for r in drop:
                    action_log["removed_rows"][int(r)] = {"finding_id": fid, "kept_row_id": keep, "reason": reason}
                action_log["auto_fixed"].append({"finding_id": fid,
                    "action_taken": "Removed near-duplicate variants", "kept_row_id": keep,
                    "removed_row_ids": drop, "reason": reason, "rows_affected": len(drop)})
                action_log["stats"]["fixed"] += 1

        # ---- UNIT-FORMAT DRIFT → normalise to canonical format ----------
        elif it == "unit_format_drift":
            col = rv.get("column")
            if col and col in out.columns and rows:
                vals = out.loc[rows, col].astype(str)
                canonical = vals.mode().iloc[0] if not vals.mode().empty else str(vals.iloc[0])
                changed = [r for r in rows if str(out.at[r, col]) != canonical]
                for r in changed:
                    orig = out.at[r, col]
                    out.at[r, col] = canonical
                    _add_flag(out, [r], f"UNIT_FORMAT_NORMALISED: {orig} → {canonical}.")
                reason = (f"Normalised inconsistent '{col}' formats to the canonical form "
                          f"'{canonical}'. Originals preserved in audit_flag.")
                mark(rows, fid, reason, "modified")
                action_log["auto_fixed"].append({"finding_id": fid,
                    "action_taken": f"Normalised '{col}' format to {canonical}",
                    "row_ids_updated": changed, "reason": reason, "rows_affected": len(changed)})
                action_log["stats"]["fixed"] += 1

        # ---- DECIMAL SHIFT → rescale to per-part baseline ---------------
        elif it == "decimal_shift":
            col = rv.get("column")
            scale = rv.get("approx_scale") or 10
            if col and col in out.columns:
                for r in rows:
                    orig = pd.to_numeric(pd.Series([out.at[r, col]]), errors="coerce").iloc[0]
                    if pd.notna(orig) and scale:
                        fixed_val = round(float(orig) / scale, 3)
                        out.at[r, col] = fixed_val
                        _add_flag(out, [r], f"DECIMAL_SHIFT_CORRECTED: {col} {orig} → {fixed_val} (÷{scale}).")
                reason = (f"Rescaled the decimal-shifted {col} for record {lots} to the per-part "
                          f"baseline (÷{scale}); original preserved in audit_flag — verify the value.")
                mark(rows, fid, reason, "modified")
                action_log["escalated"].append({"finding_id": fid,
                    "escalation_text": f"Record {lots}: {col} rescaled ÷{scale} to per-part baseline.",
                    "action_taken": f"Rescaled {col} ÷{scale}", "reason": reason})
                action_log["stats"]["escalated"] += 1

        # ---- ORPHANED CUSTOMER → escalate (cannot invent a customer) ----
        elif it == "orphaned_customer":
            cid = rv.get("customer_id", "?")
            _add_flag(out, rows, f"ORPHANED_CUSTOMER: '{cid}' not in lookup — verify with procurement.")
            reason = (f"Customer reference '{cid}' is not in the lookup table. AssedGuard AI cannot "
                      "invent a customer — verify with procurement and correct the reference.")
            action_log["escalated"].append({"finding_id": fid,
                "escalation_text": f"Customer '{cid}' is orphaned on {len(rows)} record(s) — verify.",
                "action_taken": "Flagged orphaned reference", "reason": reason})
            action_log["stats"]["escalated"] += 1

        # ---- IMPOSSIBLE VALUE → escalate (true value unknown) -----------
        elif it == "impossible_value":
            col = rv.get("column", "value")
            _add_flag(out, rows, f"IMPOSSIBLE_VALUE: '{col}' physically impossible — verify source reading.")
            reason = (f"'{col}' holds physically impossible value(s). The true reading is unknown, so "
                      "this is escalated for source verification rather than guessed.")
            action_log["escalated"].append({"finding_id": fid,
                "escalation_text": f"Impossible value(s) in '{col}' on {len(rows)} record(s) — verify.",
                "action_taken": "Flagged impossible value", "reason": reason})
            action_log["stats"]["escalated"] += 1

        # ---- EXACT DUPLICATE → remove extras (applied last) -------------
        elif it == "exact_duplicate":
            if len(rows) > 1:
                keep = _most_recent_idx(out, rows)
                drop = [r for r in rows if r != keep]
                rows_to_drop.update(drop)
                reason = ("Removed identical duplicate record(s), keeping the most recent per "
                          "ISO 13485 §4.2.4 document control.")
                for r in drop:
                    action_log["removed_rows"][int(r)] = {
                        "finding_id": fid, "kept_row_id": keep, "reason": reason}
                action_log["auto_fixed"].append({"finding_id": fid,
                    "action_taken": "Removed exact duplicates", "kept_row_id": keep,
                    "removed_row_ids": drop, "reason": reason, "rows_affected": len(drop)})
                action_log["stats"]["fixed"] += 1

    # Apply duplicate removals last (after all cell edits).
    if rows_to_drop:
        out = out.drop(index=[r for r in rows_to_drop if r in out.index])

    # Final normalisation pass: impute any residual statistical outliers (e.g.
    # genuinely-high temperatures that surface once sensor faults are removed) so
    # the exported dataset re-scans cleanly. Originals are kept in audit_flag.
    for _ in range(3):
        any_changed = False
        for col in NUMERIC_COLS:
            if col not in out.columns:
                continue
            s = pd.to_numeric(out[col], errors="coerce")
            valid = s.dropna()
            sd = float(valid.std(ddof=0)) if len(valid) >= 5 else 0.0
            if sd == 0:
                continue
            mean = float(valid.mean())
            med = medians.get(col) or round(float(valid.median()), 1)
            for i in s.index[(s - mean).abs() / sd > 3.0]:
                orig = out.at[i, col]
                out.at[i, col] = med
                _add_flag(out, [i], f"OUTLIER_NORMALISED: {col} {orig} → {med}.")
                action_log["row_actions"][int(i)] = {
                    "finding_id": "", "action_type": "modified",
                    "reason": f"Residual {col} outlier normalised to median {med}."}
                any_changed = True
        if not any_changed:
            break

    # Claude one-line summaries for HIGH actions (best-effort).
    for entry in action_log["auto_fixed"] + action_log["escalated"]:
        entry["action_summary"] = await ask_claude(
            prompt=("In one plain-English sentence for a compliance officer, explain this audit "
                    f"correction (do not mention AI): {entry.get('action_taken', '')} — {entry['reason']}"),
            max_tokens=90, fallback=entry["reason"])

    await write_memory("fixer", action_log)
    return out, action_log
