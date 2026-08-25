"""
Dissertation 1 — statistical analysis.

Two parts, both on the cleaned, windowed, analysis-ready data:

1. SUPPLIER CONCENTRATION
   How concentrated is procurement spend? Gini coefficient, the share of total
   value captured by the top 1% / 5% / 10% of suppliers, and a Lorenz curve.
   Quantifies the "money pools at the top" story behind the value-band finding.

2. STATISTICAL TESTS of the size effect
   - Logistic regression: P(winner is an SME) ~ log10(contract value).
     Reports the coefficient, odds ratio and p-value, so the size effect is
     an inferential result, not just a descriptive trend.
   - Chi-square test of independence: value band x SME/large.

Run (after the breakdowns):
    python -m src.analysis_stats
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

import config
from src.clean.standardise import value_band
from src.analysis_breakdowns import YEAR_MIN, YEAR_MAX

log = logging.getLogger("stats")
FIG_DIR = config.OUT_DIR / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)


def _load_ready() -> pd.DataFrame:
    path = config.OUT_DIR / "sme_awards_clean.parquet"
    df = (pd.read_parquet(path) if path.exists()
          else pd.read_csv(config.OUT_DIR / "sme_awards_clean.csv"))
    df = df[df["analysis_ready"]].copy()
    df["year"] = pd.to_datetime(df["award_date"], errors="coerce", utc=True).dt.year
    df = df[df["year"].between(YEAR_MIN, YEAR_MAX)]
    df = df[df["award_value"].fillna(0) > 0]
    return df


# --------------------------------------------------------------------------- #
# 1. Concentration
# --------------------------------------------------------------------------- #
def _gini(values: np.ndarray) -> float:
    v = np.sort(values[values >= 0])
    n = v.size
    if n == 0 or v.sum() == 0:
        return float("nan")
    cum = np.cumsum(v)
    return (n + 1 - 2 * (cum.sum() / cum[-1])) / n


def concentration(df: pd.DataFrame) -> dict:
    # spend per supplier (group identical supplier names)
    by_sup = df.groupby("supplier_name")["award_value"].sum().sort_values()
    vals = by_sup.values
    total = vals.sum()
    n = len(vals)

    res = {"n_suppliers": n, "total_value": total, "gini": _gini(vals)}
    for k in (0.01, 0.05, 0.10):
        top_n = max(1, int(np.ceil(n * k)))
        res[f"top_{int(k*100)}pct_share"] = vals[-top_n:].sum() / total
    log.info("CONCENTRATION  suppliers=%s  Gini=%.3f", n, res["gini"])
    for k in (1, 5, 10):
        log.info("  top %2d%% of suppliers capture %.1f%% of value",
                 k, 100 * res[f"top_{k}pct_share"])

    # Lorenz curve
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        cum_sup = np.arange(1, n + 1) / n
        cum_val = np.cumsum(vals) / total
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.plot([0, 1], [0, 1], ls="--", color="grey", label="perfect equality")
        ax.plot(cum_sup, cum_val, color="#4C72B0",
                label=f"Lorenz (Gini={res['gini']:.2f})")
        ax.fill_between(cum_sup, cum_val, cum_sup, alpha=0.1, color="#4C72B0")
        ax.set_xlabel("cumulative share of suppliers")
        ax.set_ylabel("cumulative share of award value")
        ax.set_title("Concentration of procurement value across suppliers")
        ax.legend(loc="upper left", fontsize=9)
        fig.tight_layout()
        fig.savefig(FIG_DIR / "supplier_concentration_lorenz.png", dpi=130)
        plt.close(fig)
    except Exception as exc:
        log.warning("Lorenz chart failed: %s", exc)
    return res


# --------------------------------------------------------------------------- #
# 2. Statistical tests
# --------------------------------------------------------------------------- #
def logit_size_effect(df: pd.DataFrame) -> None:
    d = df[df["sme_flag"].notna()].copy()
    d["log_value"] = np.log10(d["award_value"])
    d["sme"] = (d["sme_flag"] == 1).astype(int)

    try:
        import statsmodels.api as sm
    except ImportError:
        log.warning("statsmodels not installed - run: pip install statsmodels")
        log.warning("(skipping logistic regression; chi-square still runs)")
        return

    X = sm.add_constant(d["log_value"])
    model = sm.Logit(d["sme"], X).fit(disp=0)
    coef = model.params["log_value"]
    pval = model.pvalues["log_value"]
    or_per_10x = np.exp(coef)
    log.info("LOGISTIC REGRESSION  P(SME win) ~ log10(contract value)   n=%s", len(d))
    log.info("  coef(log10 value) = %.3f   p = %.3g", coef, pval)
    log.info("  odds ratio per 10x value = %.3f  (=> each 10x increase in "
             "contract value multiplies the odds of an SME win by %.2f)",
             or_per_10x, or_per_10x)
    log.info("  pseudo R^2 = %.3f", model.prsquared)
    with open(config.OUT_DIR / "logit_summary.txt", "w") as f:
        f.write(str(model.summary()))


def chi_square_bands(df: pd.DataFrame) -> None:
    from scipy.stats import chi2_contingency
    d = df[df["sme_flag"].notna()].copy()
    d["value_band"] = d["award_value"].map(value_band)
    ct = pd.crosstab(d["value_band"], d["sme_flag"])
    chi2, p, dof, _ = chi2_contingency(ct)
    log.info("CHI-SQUARE  value band x SME/large")
    log.info("  chi2 = %.1f   dof = %s   p = %.3g", chi2, dof, p)
    # Cramer's V (effect size)
    n = ct.values.sum()
    v = np.sqrt(chi2 / (n * (min(ct.shape) - 1)))
    log.info("  Cramer's V = %.3f (effect size)", v)
    ct.to_csv(config.OUT_DIR / "value_band_contingency.csv")


def run() -> None:
    df = _load_ready()
    log.info("rows for statistical analysis (%s-%s): %s", YEAR_MIN, YEAR_MAX, len(df))
    print("\n--- 1. SUPPLIER CONCENTRATION ---")
    concentration(df)
    print("\n--- 2a. LOGISTIC REGRESSION (size effect) ---")
    logit_size_effect(df)
    print("\n--- 2b. CHI-SQUARE (value band x SME) ---")
    chi_square_bands(df)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run()
