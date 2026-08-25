"""
Single-command runner for the full SME procurement pipeline.

Runs every stage in order and stops immediately if one fails, so you never
end up analysing a dataset that a broken step upstream silently corrupted.

Usage:
    python run_all.py

Expects these files to already be in data/raw/:
    united_kingdom_contracts_finder_releases_full.jsonl.gz
    united_kingdom_fts_full.jsonl.gz
    BasicCompanyDataAsOneFile-<date>.csv   (edit CH_CSV below if the date differs)
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"

CF_GZ = RAW / "united_kingdom_contracts_finder_releases_full.jsonl.gz"
FTS_GZ = RAW / "united_kingdom_fts_full.jsonl.gz"

# Companies House filename includes a date — pick it up automatically so this
# script doesn't go stale the next time you download a fresher extract.
_ch_matches = sorted(RAW.glob("BasicCompanyDataAsOneFile-*.csv"))
CH_CSV = _ch_matches[-1] if _ch_matches else None

STEPS = [
    ("Ingest: Contracts Finder",
     [sys.executable, "-m", "src.acquire.ingest_local", str(CF_GZ), "--source", "contracts_finder"]),
    ("Ingest: Find a Tender",
     [sys.executable, "-m", "src.acquire.ingest_local", str(FTS_GZ), "--source", "find_a_tender"]),
    ("Companies House bulk matching",
     [sys.executable, "-m", "src.acquire.companies_house_bulk", str(CH_CSV)] if CH_CSV else None),
    ("Clean + flag dataset",
     [sys.executable, "-m", "src.clean.clean_dataset"]),
    ("Breakdown analysis (sector / value band / region / year)",
     [sys.executable, "-m", "src.analysis_breakdowns"]),
    ("Statistical analysis (concentration / logistic regression / chi-square)",
     [sys.executable, "-m", "src.analysis_stats"]),
]


def _check_inputs() -> None:
    missing = [p for p in (CF_GZ, FTS_GZ) if not p.exists()]
    if CH_CSV is None:
        missing.append(RAW / "BasicCompanyDataAsOneFile-*.csv (no match found)")
    if missing:
        print("ERROR: missing required input file(s) in data/raw/:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)


def main() -> None:
    _check_inputs()
    print(f"Companies House file: {CH_CSV.name}\n")

    for i, (label, cmd) in enumerate(STEPS, 1):
        print(f"{'=' * 70}\nSTEP {i}/{len(STEPS)}: {label}\n{'=' * 70}")
        t0 = time.time()
        result = subprocess.run(cmd)
        elapsed = time.time() - t0
        if result.returncode != 0:
            print(f"\nFAILED at step {i} ('{label}') after {elapsed:.0f}s — stopping.")
            print("Fix the error above, then rerun `python run_all.py`.")
            sys.exit(result.returncode)
        print(f"-- done in {elapsed:.0f}s --\n")

    print("=" * 70)
    print("ALL STEPS COMPLETE — dataset and analysis are up to date.")
    print("  Cleaned dataset : data/processed/sme_awards_clean.csv")
    print("  Breakdowns      : data/processed/breakdown_*.csv, findings.md")
    print("=" * 70)


if __name__ == "__main__":
    main()