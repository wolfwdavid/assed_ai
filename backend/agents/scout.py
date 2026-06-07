"""
Agent 1 — Scout (schema-adaptive)
Detects data-integrity issues with deterministic Python. It profiles the
dataset's columns by name + dtype so it works on ANY manufacturing schema, and
covers the six M-AGENTS Track 01 benchmark classes plus several domain rules:

  Class 1  exact_duplicate          — identical rows
  Class 2  near_duplicate_variant   — duplicates differing only by case/whitespace
  Class 3  unit_format_drift        — same unit logged in different formats (KG/kg/Kg)
  Class 4  orphaned_customer        — customer_id absent from the lookup table
  Class 5  decimal_shift            — weight 10x/100x off the per-part baseline
  Class 6  impossible_value         — physically impossible readings (neg/zero/absurd)
  + duplicate_lot_number, unit_conflict, statistical_outlier, missing_timestamp,
    compliance_contradiction  (fire only when the relevant columns exist)

A second customer-lookup DataFrame enables the orphaned-reference check.
HIGH findings are enriched with the regulation they violate (best-effort Claude,
deterministic fallback). Writes findings to Cognee under "scout".
"""
import numpy as np
import pandas as pd

from memory.cognee_store import write_memory
from utils import bayes
from utils.ai import ask_claude

OUTLIER_SIGMA = 3.0
FDA_TEMP_THRESHOLD_C = 85.0
DECIMAL_RATIO = 8.0          # value vs per-part median ratio that signals a 10x shift
ENRICH_LIMIT = 12           # cap Claude enrichment calls (perf at benchmark scale)

WEIGHT_HINTS = ("weight", "qty", "quantity", "mass", "amount", "volume", "vol",
                "net", "gross", "load", "yield")
PART_HINTS = ("part", "product", "sku", "item", "model", "material", "component", "type")
CUSTOMER_HINTS = ("customer", "client", "cust", "account", "buyer")
UNIT_HINTS = ("unit", "uom", "measure")
TIME_HINTS = ("date", "time", "timestamp", "created", "recorded")
POSITIVE_HINTS = WEIGHT_HINTS + ("pressure", "temp", "temperature", "speed", "rpm",
                                 "count", "age", "duration", "height", "length",
                                 "width", "depth", "diameter", "force", "current", "voltage")


def _id(n: int) -> str:
    return f"F{n:03d}"


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _profile(df: pd.DataFrame) -> dict:
    """Classify columns into roles by name + dtype (schema-adaptive)."""
    cols = [c for c in df.columns if c != "audit_flag"]
    low = {c: str(c).lower() for c in cols}
    has = lambda c, hints: any(h in low[c] for h in hints)  # noqa: E731

    numeric = [c for c in cols if _num(df, c).notna().mean() > 0.6]
    weight = [c for c in numeric if has(c, WEIGHT_HINTS)] or numeric
    units = [c for c in cols if has(c, UNIT_HINTS)]
    times = [c for c in cols if has(c, TIME_HINTS)]
    customer = next((c for c in cols if has(c, CUSTOMER_HINTS)), None)
    part = next((c for c in cols if has(c, PART_HINTS) and 0 < df[c].nunique() <= max(2, len(df) // 2)), None)
    ident = (next((c for c in cols if has(c, ("lot", "serial", "record"))), None)
             or next((c for c in cols if has(c, ("id", "number", "code", "ref"))), None)
             or (cols[0] if cols else None))
    return {"data": cols, "numeric": numeric, "weight": weight, "units": units,
            "times": times, "customer": customer, "part": part, "ident": ident}


def _entity(df: pd.DataFrame, idx, ident) -> list[str]:
    if not ident or ident not in df.columns:
        return [str(i) for i in idx]
    return [str(df.at[i, ident]) for i in idx if i in df.index]


def detect(df: pd.DataFrame, customers_df: pd.DataFrame | None = None) -> list[dict]:
    findings: list[dict] = []
    n = 0
    p = _profile(df)
    data_cols = p["data"]
    ident = p["ident"]
    flagged_cells: set[tuple] = set()   # (row, col) already explained, to avoid double-flag

    def add(issue_type, severity, row_ids, raw_values, reason, audit_risk):
        nonlocal n
        n += 1
        findings.append({
            "finding_id": _id(n), "issue_type": issue_type, "severity": severity,
            "row_ids": [int(i) for i in row_ids],
            "lot_numbers": _entity(df, row_ids, ident),
            "raw_values": raw_values, "reason": reason, "audit_risk": audit_risk,
        })

    # ── CLASS 1: EXACT DUPLICATES ────────────────────────────────────────
    dup_all = df[df.duplicated(subset=data_cols, keep=False)]
    if not dup_all.empty:
        for _sig, grp in dup_all.groupby(data_cols, dropna=False):
            if len(grp) < 2:
                continue
            ent = _entity(df, grp.index, ident)[0] if ident else ""
            add("exact_duplicate", "HIGH", list(grp.index), {"count": int(len(grp))},
                f"Record {ent} appears as {len(grp)} identical rows ({len(grp) - 1} duplicate "
                "copies). Duplicates inflate counts and break one-to-one traceability.",
                "An auditor cannot trust totals when a record is counted more than once.")

    # ── CLASS 2: NEAR-DUPLICATE VARIANTS (case / whitespace) ─────────────
    normdf = df[data_cols].copy()
    for c in data_cols:
        if normdf[c].dtype == object:
            normdf[c] = normdf[c].astype(str).str.strip().str.lower()
        else:
            normdf[c] = normdf[c].astype(str)
    raw_str = df[data_cols].astype(str)
    variant_mask = normdf.duplicated(keep=False) & ~raw_str.duplicated(keep=False)
    if variant_mask.any():
        sig = normdf[variant_mask].astype(str).agg("|".join, axis=1)
        for _s, idx in sig.groupby(sig).groups.items():
            idx = list(idx)
            if len(idx) < 2:
                continue
            ent = _entity(df, idx, ident)[0] if ident else ""
            add("near_duplicate_variant", "MED", idx, {"count": len(idx)},
                f"Record {ent} has {len(idx)} rows that are identical except for "
                "letter-case or whitespace differences — a near-duplicate variant.",
                "Variant duplicates evade exact-match dedup and double-count records.")

    # ── CLASS 3: UNIT-FORMAT DRIFT (same unit, different format) ─────────
    for ucol in p["units"]:
        raw = df[ucol].dropna().astype(str)
        norm = raw.str.strip().str.lower()
        for _base, grp in raw.groupby(norm):
            variants = sorted(grp.unique().tolist())
            if len(variants) > 1:
                add("unit_format_drift", "MED", list(grp.index),
                    {"column": ucol, "variants": variants},
                    f"Column '{ucol}' records the same unit in inconsistent formats "
                    f"{variants} — a silent format drift (e.g. firmware change).",
                    "Inconsistent unit formatting corrupts grouping and downstream parsing.")

    # ── CLASS 4: ORPHANED CUSTOMER REFERENCES ────────────────────────────
    if customers_df is not None and p["customer"]:
        ccol = p["customer"]
        ref_candidates = [c for c in customers_df.columns
                          if any(h in str(c).lower() for h in CUSTOMER_HINTS + ("id",))]
        ref_col = ref_candidates[0] if ref_candidates else customers_df.columns[0]
        ref_ids = set(customers_df[ref_col].dropna().astype(str).str.strip())
        main = df[ccol].astype(str).str.strip()
        orphan_mask = main.notna() & ~main.isin(ref_ids) & (main != "") & (main.str.lower() != "nan")
        if orphan_mask.any():
            for cid, idx in df[orphan_mask].groupby(main[orphan_mask]).groups.items():
                idx = list(idx)
                add("orphaned_customer", "HIGH", idx,
                    {"customer_id": str(cid), "lookup_rows": len(ref_ids)},
                    f"Customer reference '{cid}' on {len(idx)} record(s) is not present in the "
                    "customer lookup table — an orphaned (dangling) reference.",
                    "Orphaned references break referential integrity; the auditor cannot "
                    "trace the order to a real customer.")

    # ── CLASS 5: DECIMAL-SHIFT WEIGHTS (per-part baseline) ───────────────
    part = p["part"]
    n_parts = df[part].nunique() if part else 0
    use_part = bool(part) and 0 < n_parts <= 500
    for wcol in p["weight"]:
        groups = df.groupby(part) if use_part else [(None, df)]
        for pid, grp in groups:
            vals = _num(grp, wcol).dropna()
            vals = vals[vals > 0]
            if len(vals) < 3:
                continue
            med = float(vals.median())
            if med <= 0:
                continue
            for i, v in vals.items():
                ratio = v / med
                if ratio >= DECIMAL_RATIO or ratio <= 1 / DECIMAL_RATIO:
                    flagged_cells.add((int(i), wcol))
                    scale = 10 ** round(np.log10(ratio)) if ratio > 0 else 1
                    add("decimal_shift", "HIGH", [i],
                        {"column": wcol, "value": round(float(v), 3),
                         "part_median": round(med, 3), "approx_scale": scale,
                         "part": str(pid) if pid is not None else "all"},
                        f"{wcol} = {round(float(v), 3)} is ~{scale}x off the per-part baseline "
                        f"({round(med, 3)}) — a decimal-shift error.",
                        "A 10x/100x weight error corrupts yield, billing, and shipment totals.")

    # ── CLASS 6: IMPOSSIBLE VALUES ───────────────────────────────────────
    for col in p["numeric"]:
        s = _num(df, col)
        low = str(col).lower()
        bad_idx = pd.Index([])
        if any(h in low for h in POSITIVE_HINTS):
            bad_idx = bad_idx.union(s.index[s < 0])
        if any(h in low for h in WEIGHT_HINTS):
            bad_idx = bad_idx.union(s.index[s == 0])
        bad_idx = bad_idx.union(s.index[s.abs() > 1e9])
        bad_idx = [i for i in bad_idx if (int(i), col) not in flagged_cells]
        if bad_idx:
            for i in bad_idx:
                flagged_cells.add((int(i), col))
            add("impossible_value", "HIGH", bad_idx,
                {"column": col, "values": [round(float(s.loc[i]), 3) for i in bad_idx[:5]]},
                f"Column '{col}' contains {len(bad_idx)} physically impossible value(s) "
                "(negative, zero, or absurd magnitude).",
                "Impossible readings are definitively wrong and fail any data-integrity check.")

    # ── DOMAIN RULE: duplicate_lot_number (same id, differing weight) ────
    if ident and p["weight"] and ident in df.columns:
        wcol = p["weight"][0]
        q = _num(df, wcol)
        nun = q.groupby(df[ident]).nunique()
        exact_keys = set(dup_all[ident].astype(str)) if (not dup_all.empty and ident in dup_all) else set()
        for ent in nun[nun > 1].index:
            idx = list(df.index[df[ident] == ent])
            if str(ent) in exact_keys and df.loc[idx].duplicated(keep=False).all():
                continue
            qvals = sorted(q.loc[idx].dropna().unique().tolist())
            if len(qvals) > 1:
                add("duplicate_lot_number", "HIGH", idx, {"quantities": [round(v, 2) for v in qvals]},
                    f"Record {ent} has {len(idx)} rows with differing {wcol} {qvals} — "
                    "the true value is ambiguous.",
                    "Conflicting values for one record prevent the auditor from confirming output.")

    # ── DOMAIN RULE: unit_conflict (genuinely different units per record) ─
    if p["units"] and ident and ident in df.columns:
        ucol = p["units"][0]
        norm_u = df[ucol].astype(str).str.strip().str.lower()
        nun = norm_u.groupby(df[ident]).nunique()
        for ent in nun[nun > 1].index:
            idx = list(df.index[df[ident] == ent])
            uvals = sorted(set(norm_u.loc[idx]))
            add("unit_conflict", "MED", idx, {"units": uvals},
                f"Record {ent} is recorded in conflicting units ({', '.join(uvals)}) — the "
                "quantity is meaningless without conversion.",
                "Quantity cannot be verified when the unit of measure is inconsistent.")

    # ── DOMAIN RULE: statistical outliers (any numeric col) ──────────────
    # Detection is deterministic (3-sigma) so it never depends on PyMC. When PyMC
    # is available, a ROBUST Student-T model upgrades each finding with a
    # calibrated anomaly probability and the sensor's 95% credible normal range —
    # best-effort, falling back to the plain 3-sigma reason if anything fails.
    use_bayes = bayes.available()
    for col in p["numeric"]:
        s = _num(df, col).dropna()
        if len(s) < 8 or s.std(ddof=0) == 0:
            continue
        mean, std = float(s.mean()), float(s.std(ddof=0))
        z = (s - mean).abs() / std
        candidates = [(int(i), float(zi), float(s.loc[i]))
                      for i, zi in z[z > OUTLIER_SIGMA].items()
                      if (int(i), col) not in flagged_cells]
        if not candidates:
            continue

        bayes_info = bayes.analyze_column(s.values, [c[2] for c in candidates]) if use_bayes else None

        for k, (i, zi, val) in enumerate(candidates):
            rv = {"column": col, "value": round(val, 2), "sigma": round(zi, 1),
                  "expected_range": [round(mean - OUTLIER_SIGMA * std, 1),
                                     round(mean + OUTLIER_SIGMA * std, 1)]}
            reason = (f"{col} value {round(val, 1)} is {round(zi, 1)} standard deviations from "
                      "the mean — likely a sensor fault or transcription error.")
            if bayes_info and k < len(bayes_info["anomaly_probs"]):
                prob = bayes_info["anomaly_probs"][k]
                rv.update(bayesian_probability=prob, method="bayesian",
                          bayesian_model=bayes_info["model"],
                          credible_range=[bayes_info["credible_low"], bayes_info["credible_high"]],
                          robust_sigma=bayes_info["robust_sigma"],
                          naive_sigma=bayes_info["naive_sigma"])
                reason = (f"{col} reading {round(val, 1)} is rated {round(prob * 100)}% likely "
                          f"anomalous by a robust Bayesian sensor model ({bayes_info['model']}). "
                          f"The model places this sensor's true in-spec range at "
                          f"{bayes_info['credible_low']}–{bayes_info['credible_high']} "
                          f"(robust σ {bayes_info['robust_sigma']} vs naive {bayes_info['naive_sigma']}, "
                          "which the faults themselves inflate).")
            add("statistical_outlier", "MED", [i], rv, reason,
                "Implausible readings suggest an uncalibrated sensor.")

    # ── DOMAIN RULE: missing timestamps ──────────────────────────────────
    for tcol in p["times"]:
        bd = df[tcol]
        missing = df[bd.isna() | (bd.astype(str).str.strip() == "")
                     | (bd.astype(str).str.lower().isin(["nat", "nan"]))]
        if not missing.empty:
            add("missing_timestamp", "LOW", list(missing.index),
                {"column": tcol, "count": int(len(missing))},
                f"{len(missing)} record(s) are missing '{tcol}'. Records without a timestamp "
                "cannot be placed on the production timeline.",
                "Missing dates break audit traceability.")

    # ── DOMAIN RULE: compliance contradiction (PASS + over-temp) ─────────
    if "compliance_status" in df.columns and "temperature_c" in df.columns:
        temp = _num(df, "temperature_c")
        status = df["compliance_status"].astype(str).str.upper().str.strip()
        bad = df[(status == "PASS") & (temp > FDA_TEMP_THRESHOLD_C)]
        for i, row in bad.iterrows():
            tval = float(temp.loc[i])
            add("compliance_contradiction", "HIGH", [i],
                {"temperature_c": round(tval, 1), "compliance_status": "PASS",
                 "threshold_c": FDA_TEMP_THRESHOLD_C},
                f"Record {row.get(ident, '')} is marked PASS but its temperature was "
                f"{round(tval, 1)}°C — above the {FDA_TEMP_THRESHOLD_C}°C limit.",
                "A passing record that contradicts its own data is an automatic audit failure.")

    return findings


async def run_scout(df: pd.DataFrame, customers_df: pd.DataFrame | None = None) -> dict:
    findings = detect(df, customers_df)

    enriched = 0
    for f in findings:
        if f["severity"] != "HIGH":
            continue
        if enriched < ENRICH_LIMIT:
            f["regulation"] = await ask_claude(
                prompt=(f"Given this manufacturing data issue: {f['reason']} What specific FDA "
                        "21 CFR Part 11 or ISO 13485 requirement does it violate? One sentence, "
                        "plain English, no mention of AI."),
                max_tokens=120, fallback=_default_regulation(f["issue_type"]))
            enriched += 1
        else:
            f["regulation"] = _default_regulation(f["issue_type"])

    summary = {
        "total": len(findings),
        "HIGH": sum(1 for f in findings if f["severity"] == "HIGH"),
        "MED": sum(1 for f in findings if f["severity"] == "MED"),
        "LOW": sum(1 for f in findings if f["severity"] == "LOW"),
        "rows_scanned": int(len(df)),
    }
    result = {"findings": findings, "summary": summary}
    await write_memory("scout", result)
    return result


def _default_regulation(issue_type: str) -> str:
    return {
        "compliance_contradiction":
            "Violates FDA 21 CFR Part 11 §11.10(a) — records must be accurate and reliable.",
        "duplicate_lot_number":
            "Violates ISO 13485 §7.5.8 identification and traceability.",
        "exact_duplicate":
            "Violates ISO 13485 §4.2.4 control of records — records must be unique and controlled.",
        "near_duplicate_variant":
            "Violates ISO 13485 §4.2.4 — case/whitespace variants undermine record uniqueness.",
        "unit_format_drift":
            "Violates FDA 21 CFR Part 11 data-integrity (consistent, attributable records).",
        "orphaned_customer":
            "Violates ISO 13485 §7.2.1 / §4.2.4 — customer references must be valid and traceable.",
        "decimal_shift":
            "Violates FDA 21 CFR Part 11 §11.10(a) — a 10x/100x weight error is an inaccurate record.",
        "impossible_value":
            "Violates FDA 21 CFR Part 11 §11.10(a) — physically impossible readings are not reliable.",
    }.get(issue_type, "Relevant to FDA 21 CFR Part 11 data-integrity expectations (ALCOA+).")
