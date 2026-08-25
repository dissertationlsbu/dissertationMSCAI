"""
Headline SME-share summary using the CLEANED dataset.

Reports SME share by VALUE and by COUNT under three lenses:
  A. all classified awards with positive value   (raw headline)
  B. analysis-ready only (frameworks/placeholders/outliers/dups removed)
  C. lower bound (unmatched + unknown counted as non-SME)

Run after src.clean.clean_dataset:
    python -m src.analysis_summary
"""

import logging

import pandas as pd

import config

log = logging.getLogger("analysis")
TARGET, GOV_ACTUAL = 0.33, 0.20


def _pct(x):
    return f"{x:.1%}" if pd.notna(x) else "n/a"


def _share(df, by_value=True):
    """SME share over rows where sme_flag is known."""
    known = df[df["sme_flag"].notna()]
    if known.empty:
        return None
    if by_value:
        sme = known.loc[known["sme_flag"] == 1, "award_value"].sum()
        tot = known["award_value"].sum()
    else:
        sme = (known["sme_flag"] == 1).sum()
        tot = len(known)
    return sme / tot if tot else None


def main():
    path = config.OUT_DIR / "sme_awards_clean.parquet"
    if path.exists():
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(config.OUT_DIR / "sme_awards_clean.csv")

    pos = df[df["award_value"].fillna(0) > 0].copy()
    ready = pos[pos["analysis_ready"]]

    print("=" * 66)
    print("SME SHARE  (target 33% | gov-reported actual 20%)")
    print("=" * 66)
    print(f"{'lens':<34}{'by VALUE':>15}{'by COUNT':>15}")
    print("-" * 66)
    print(f"{'A. all classified awards':<34}"
          f"{_pct(_share(pos, True)):>15}{_pct(_share(pos, False)):>15}")
    print(f"{'B. frameworks/junk removed':<34}"
          f"{_pct(_share(ready, True)):>15}{_pct(_share(ready, False)):>15}")

    # C. lower bound on the ready set: unmatched/unknown treated as non-SME
    sme_val = ready.loc[ready["sme_flag"] == 1, "award_value"].sum()
    lb = sme_val / ready["award_value"].sum() if len(ready) else None
    print(f"{'C. lower bound (ready set)':<34}{_pct(lb):>15}{'':>15}")

    print("\n" + "=" * 66)
    print("VALUE TOTALS  (sanity vs ~£300-400bn/yr national spend)")
    print("=" * 66)
    print(f"  all positive-value awards : £{pos['award_value'].sum():,.0f}")
    print(f"  analysis-ready awards     : £{ready['award_value'].sum():,.0f}")
    print(f"  rows: total {len(df):,} | positive-value {len(pos):,} | "
          f"analysis-ready {len(ready):,}")

    # framework contrast — the likely headline finding
    fw = pos[pos["is_framework"]]
    print("\n" + "=" * 66)
    print("FRAMEWORK CONTRAST")
    print("=" * 66)
    print(f"  framework awards          : {len(fw):,} "
          f"({len(fw)/len(pos):.1%} of awards)")
    print(f"  framework value           : £{fw['award_value'].sum():,.0f} "
          f"({fw['award_value'].sum()/pos['award_value'].sum():.1%} of value)")
    sme_fw = _share(fw, True)
    print(f"  SME share WITHIN frameworks (by value): {_pct(sme_fw)}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()