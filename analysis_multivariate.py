"""
Multivariate logistic regression extending the single-variable model in
analysis_stats.py. Tests whether contract size still predicts SME success
once sector and year are controlled for.

Model: SME_win ~ log10(award_value) + C(sector) + C(year)

Does NOT touch analysis_stats.py or the cleaned dataset — reads the same
validated sme_awards_clean.csv, standalone script.

Run from the project root:
    python analysis_multivariate.py
"""

import logging

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("multivariate")

YEAR_MIN, YEAR_MAX = 2021, 2025


def main() -> None:
    df = pd.read_csv("data/processed/sme_awards_clean.csv", low_memory=False)

    df["year"] = pd.to_datetime(df["award_date"], errors="coerce", utc=True).dt.year
    ready = df[
        df["analysis_ready"]
        & df["year"].between(YEAR_MIN, YEAR_MAX)
        & df["sme_flag"].notna()
        & (df["award_value"] > 0)
    ].copy()
    log.info("rows for multivariate model: %s", f"{len(ready):,}")

    ready["log_value"] = np.log10(ready["award_value"])
    ready["sme_win"] = ready["sme_flag"].astype(int)
    ready["year"] = ready["year"].astype(int)

    # Collapse sector to the top N categories (statsmodels needs a
    # manageable number of dummy variables; rare sectors get grouped as
    # "Other" so the model doesn't choke on 1-row categories).
    top_sectors = ready["cpv_description"].value_counts().head(10).index
    ready["sector_grp"] = np.where(
        ready["cpv_description"].isin(top_sectors), ready["cpv_description"], "Other"
    )

    log.info("--- MULTIVARIATE LOGISTIC REGRESSION ---")
    log.info("model: sme_win ~ log_value + C(sector_grp) + C(year)")

    model = smf.logit(
        "sme_win ~ log_value + C(sector_grp) + C(year)", data=ready
    ).fit(disp=0)

    # Focus the printed output on the two things that matter for RQ1/RQ3:
    # 1. Does the size effect survive controlling for sector and year?
    coef = model.params["log_value"]
    pval = model.pvalues["log_value"]
    odds_ratio = np.exp(coef)
    log.info("")
    log.info("SIZE EFFECT (controlling for sector + year):")
    log.info("  coef(log_value) = %.3f   p = %.4g", coef, pval)
    log.info("  odds ratio per 10x value = %.3f", odds_ratio)
    log.info("  (single-variable model without controls was 0.573 for comparison)")

    # 2. Which sectors differ significantly from the reference sector, holding
    #    value and year constant?
    log.info("")
    log.info("SECTOR EFFECTS (relative to reference sector, holding value + year constant):")
    for name, c, p in zip(model.params.index, model.params, model.pvalues):
        if name.startswith("C(sector_grp)"):
            sector = name.split("[T.")[1].rstrip("]")
            sig = "***" if p < 0.001 else ("**" if p < 0.01 else ("*" if p < 0.05 else ""))
            log.info("  %-30s odds ratio=%.2f  p=%.4g  %s", sector, np.exp(c), p, sig)

    log.info("")
    log.info("pseudo R^2 = %.3f  (single-variable model was 0.034)", model.prsquared)
    log.info("n = %s", f"{int(model.nobs):,}")

    with open("data/processed/multivariate_summary.txt", "w") as f:
        f.write(str(model.summary()))
    log.info("full statsmodels summary written -> data/processed/multivariate_summary.txt")


if __name__ == "__main__":
    main()