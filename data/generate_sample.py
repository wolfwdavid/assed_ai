"""
Synthetic manufacturing dataset generator for AssedGuard AI.
Produces data/sample.csv (200 rows) with deliberately seeded data-quality
issues so the 4-agent pipeline has something concrete to find and fix.

Run:  python generate_sample.py
"""
import csv
import os
import random

random.seed(42)

PRODUCTS = ["PRD-100", "PRD-205", "PRD-310", "PRD-422", "PRD-540"]
FACILITIES = ["FAC-A", "FAC-B", "FAC-C"]
INSPECTORS = [f"INS-{i:02d}" for i in range(1, 13)]
NOTES_POOL = [
    "Routine batch", "Temperature nominal", "Within spec",
    "Re-tested OK", "Operator: night shift", "Line 3", "",
    "QA reviewed", "Cooling stage normal", "Standard run",
]

COLUMNS = [
    "lot_number", "product_id", "batch_date", "quantity", "unit",
    "temperature_c", "pressure_bar", "inspector_id", "facility_code",
    "compliance_status", "notes",
]


def base_row(i):
    """A clean, in-spec manufacturing record."""
    day = random.randint(1, 28)
    hour = random.randint(6, 22)
    return {
        "lot_number": f"LOT-{1000 + i}",
        "product_id": random.choice(PRODUCTS),
        "batch_date": f"2026-05-{day:02d}T{hour:02d}:00:00",
        "quantity": round(random.uniform(80, 480), 1),
        "unit": "kg",
        "temperature_c": round(random.gauss(70, 5), 1),
        "pressure_bar": round(random.gauss(2.0, 0.25), 2),
        "inspector_id": random.choice(INSPECTORS),
        "facility_code": random.choice(FACILITIES),
        "compliance_status": random.choice(["PASS", "PASS", "PASS", "FAIL", "PENDING"]),
        "notes": random.choice(NOTES_POOL),
    }


def build():
    rows = []
    # 160 clean baseline rows (indices 0..159 -> LOT-1000..LOT-1159)
    for i in range(160):
        rows.append(base_row(i))

    # --- 8 EXACT DUPLICATES (HIGH) ---------------------------------------
    # Duplicate 8 existing rows verbatim.
    for src in random.sample(range(160), 8):
        rows.append(dict(rows[src]))

    # --- 6 NEAR-DUPLICATE LOT NUMBERS (HIGH) -----------------------------
    # Same lot_number, quantity differs by 1-2 (impossible to reconcile).
    for src in random.sample(range(160), 6):
        clone = dict(rows[src])
        clone["quantity"] = round(clone["quantity"] + random.choice([1, 2, -1, -2]), 1)
        clone["notes"] = "Re-weighed"
        rows.append(clone)

    # --- 10 UNIT CONFLICTS (MED) -----------------------------------------
    # Same lot_number recorded once in kg and once in lbs. The NUMBER is left
    # unchanged on purpose — that's exactly the integrity problem: identical
    # magnitude logged under two different units, so the real weight is unknown.
    used = set()
    while len(used) < 10:
        used.add(random.randrange(160))
    for src in used:
        clone = dict(rows[src])
        clone["unit"] = "lbs"
        clone["notes"] = "Imperial units"
        rows.append(clone)

    # --- 5 STATISTICAL OUTLIERS (MED) ------------------------------------
    # temperature_c far beyond 3 sigma (sensor faults). Marked PENDING so the
    # spike is classified purely as an outlier, not also a PASS contradiction.
    for k in range(5):
        r = base_row(900 + k)
        r["temperature_c"] = round(random.uniform(210, 290), 1)
        r["compliance_status"] = "PENDING"
        r["notes"] = "Sensor spike?"
        rows.append(r)

    # --- 7 MISSING BATCH DATE (LOW) --------------------------------------
    for k in range(7):
        r = base_row(800 + k)
        r["batch_date"] = ""
        rows.append(r)

    # --- 4 COMPLIANCE CONTRADICTIONS (HIGH / CRITICAL) -------------------
    # compliance_status == PASS but temperature critically high (>95C).
    for k in range(4):
        r = base_row(700 + k)
        r["temperature_c"] = round(random.uniform(96, 99.5), 1)
        r["compliance_status"] = "PASS"
        r["notes"] = "Auto-approved"
        rows.append(r)

    random.shuffle(rows)
    return rows


def main():
    rows = build()
    out = os.path.join(os.path.dirname(__file__), "sample.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
