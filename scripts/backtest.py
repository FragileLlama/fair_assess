"""Backtest the model against real Board of Review decisions.

Samples residential appeals the Board decided for a tax year, runs the model as
of that year (no later data), and compares our verdict to what the Board did.
Writes per-appeal rows to a CSV and win rates by verdict to app/calibration.json.

    .venv/bin/python -m scripts.backtest --year 2025 --n 300
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app import analysis as A
from app import cookdata as C
from app import service

CLASSES = sorted(A.SUPPORTED_CLASSES)


def sample_appeals(year: int, n: int, seed: int) -> list[dict]:
    where = f"tax_year={year} AND majorclass='Residential' AND class in {C._in(CLASSES)}"
    total = int(C.query(C.DS_BOR, where=where, select="count(*) as n")[0]["n"])
    rng = random.Random(seed)
    picked: dict[str, dict] = {}
    page = 10
    while len(picked) < n:
        offset = rng.randrange(0, max(1, total - page))
        rows = C.query(
            C.DS_BOR, where=where, order="rowid", offset=offset, limit=page,
            select="pin, class, township_code, result, attorneycode, assessor_totalvalue, bor_totalvalue",
        )
        for r in rows:
            picked.setdefault(r["pin"], r)
    return list(picked.values())[:n]


def run_one(appeal: dict, year: int) -> dict:
    out = {
        "pin": appeal["pin"], "class": appeal["class"], "township": appeal["township_code"],
        "result": appeal["result"], "filer": appeal.get("attorneycode", ""),
        "won": appeal["result"] == "Decrease",
    }
    before, after = A.num(appeal.get("assessor_totalvalue")), A.num(appeal.get("bor_totalvalue"))
    out["cut_pct"] = round(1 - after / before, 4) if before and after is not None else None
    try:
        fr = service.build(appeal["pin"], year=year, light=True)
        r = fr.report
        out.update(
            verdict=r.verdict, gap=r.gap, sales_gap=r.sales_gap, n_comps=len(r.comps),
            n_lower=len(r.lower_comps), caveats=len(r.caveats), own_sale=bool(r.own_sale),
        )
    except Exception as e:  # unsupported, missing data...
        out.update(verdict="error", error=f"{type(e).__name__}: {e}"[:120])
    return out


def summarize(rows: list[dict]) -> dict:
    by = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["verdict"] == "error":
            continue
        for key in (r["verdict"], f"{r['verdict']}|{'pro_se' if 'Pro Se' in r['filer'] else 'attorney'}", "all"):
            by[key][0] += r["won"]
            by[key][1] += 1
    return {k: {"won": w, "n": n, "rate": round(w / n, 3)} for k, (w, n) in sorted(by.items())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--csv", default="backtest.csv")
    ap.add_argument("--write-calibration", action="store_true")
    args = ap.parse_args()

    appeals = sample_appeals(args.year, args.n, args.seed)
    print(f"Sampled {len(appeals)} appeals", file=sys.stderr)
    rows = []
    with ThreadPoolExecutor(args.workers) as ex:
        for i, r in enumerate(ex.map(lambda a: run_one(a, args.year), appeals), 1):
            rows.append(r)
            if i % 25 == 0:
                print(f"  {i}/{len(appeals)}", file=sys.stderr)

    fields = sorted({k for r in rows for k in r})
    with open(args.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    summary = summarize(rows)
    print(json.dumps(summary, indent=2))
    if args.write_calibration:
        cal = {"year": args.year, "sample": len(rows), "by_verdict": summary}
        Path("app/calibration.json").write_text(json.dumps(cal, indent=2))
        print("wrote app/calibration.json", file=sys.stderr)


if __name__ == "__main__":
    main()
