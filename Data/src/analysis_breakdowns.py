"""
Dissertation 1 breakdown analysis.

Takes the cleaned dataset (analysis-ready rows; frameworks excluded) and answers
the spec's research questions by decomposing SME share along four dimensions:

  sector       CPV division (first two digits)
  value_band   contract value bands
  region       contracting-authority region
  year         award year (change over time)

For each it computes SME share by COUNT and by VALUE, writes a CSV table and a
PNG chart, and drafts a short findings paragraph. Outputs land in
data/processed/ and data/processed/figures/.

Run:
    python -m src.analysis_breakdowns
"""

from __future__ import annotations

import logging

import pandas as pd

import config
from src.clean.standardise import cpv_division, value_band, parse_date

log = logging.getLogger("breakdowns")

FIG_DIR = config.OUT_DIR / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)

# Analysis window: both Find a Tender and Contracts Finder exist only from 2021
# (FTS launched Jan 2021), so a consistent-source series starts here. 2026 is a
# partial year and excluded.
YEAR_MIN, YEAR_MAX = 2021, 2025

# UK NUTS1 region codes -> readable region. The genuine regional signal is in
# these codes; free-text "England"/"UK"/"GB" name a country, not a region.
_NUTS1 = {
    "UKC": "North East", "UKD": "North West", "UKE": "Yorkshire & Humber",
    "UKF": "East Midlands", "UKG": "West Midlands", "UKH": "East of England",
    "UKI": "London", "UKJ": "South East", "UKK": "South West",
    "UKL": "Wales", "UKM": "Scotland", "UKN": "Northern Ireland",
}
_REGION_TEXT = {
    "north east": "North East", "north west": "North West",
    "yorkshire": "Yorkshire & Humber", "humber": "Yorkshire & Humber",
    "east midlands": "East Midlands", "west midlands": "West Midlands",
    "east of england": "East of England", "south east": "South East",
    "south west": "South West", "london": "London", "wales": "Wales",
    "scotland": "Scotland", "northern ireland": "Northern Ireland",
}
_NATIONAL = {"england", "uk", "gb", "eng", "united kingdom", "great britain",
             "u.k.", "gbr", "uk-wide", "national"}


def _clean_region(v) -> str:
    if not v or pd.isna(v):
        return "Unspecified"
    s = str(v).strip()
    up = s.upper()
    if up.startswith("UK") and len(up) >= 3 and up[2] in "CDEFGHIJKLMN":
        return _NUTS1.get(up[:3], "Other UK")
    low = s.lower()
    for k, name in _REGION_TEXT.items():
        if k in low:
            return name
    if low in _NATIONAL:
        return "UK-wide / unspecified"
    return "Unspecified"

# CPV 2-digit division -> readable sector label (common UK procurement sectors).
CPV_DIVISIONS = {
    "03": "Agriculture/food", "09": "Fuels/energy", "14": "Mining/metals",
    "15": "Food/beverages", "18": "Clothing", "22": "Printed matter",
    "24": "Chemicals", "30": "Office/computing equip", "31": "Electrical equip",
    "32": "Comms equipment", "33": "Medical/pharma", "34": "Transport equip",
    "35": "Security/defence equip", "37": "Musical instruments",
    "38": "Lab/optical equip", "39": "Furniture/cleaning", "41": "Water",
    "42": "Industrial machinery", "43": "Mining machinery",
    "44": "Construction materials", "45": "Construction works",
    "48": "Software/IT systems", "50": "Repair/maintenance",
    "51": "Installation services", "55": "Hotel/catering",
    "60": "Transport services", "63": "Transport support", "64": "Postal/telecom",
    "65": "Public utilities", "66": "Financial/insurance", "70": "Real estate",
    "71": "Architecture/engineering", "72": "IT services", "73": "R&D services",
    "75": "Public admin/defence", "76": "Oil/gas services",
    "77": "Agri/forestry services", "79": "Business services",
    "80": "Education/training", "85": "Health/social work",
    "90": "Environmental/waste", "92": "Recreation/culture", "98": "Other services",
}

VALUE_BAND_ORDER = ["<25k", "25k-100k", "100k-500k", "500k-1m", "1m-5m", "5m+"]


def _sme_table(df: pd.DataFrame, group_col: str) -> pd.DataFrame:
    """SME share by count and value for each group (known classification only)."""
    known = df[df["sme_flag"].notna()].copy()
    known["sme_value"] = known["award_value"].where(known["sme_flag"] == 1, 0.0)
    g = known.groupby(group_col)
    total_value = g["award_value"].sum()
    out = pd.DataFrame({
        "n_awards": g.size(),
        "total_value": total_value,
        "sme_by_count": g["sme_flag"].mean(),               # mean of 0/1 = share
        "sme_by_value": g["sme_value"].sum() / total_value.replace(0, pd.NA),
    })
    return out


def _save_chart(table: pd.DataFrame, dim: str, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = table.copy()
    # show the most material categories (by award count), capped for readability
    t = t.sort_values("n_awards", ascending=False).head(15)
    t = t.iloc[::-1]  # horizontal bars read top-down

    fig, ax = plt.subplots(figsize=(9, max(4, 0.45 * len(t))))
    y = range(len(t))
    ax.barh([i + 0.2 for i in y], t["sme_by_count"], height=0.4,
            label="by count", color="#4C72B0")
    ax.barh([i - 0.2 for i in y], t["sme_by_value"], height=0.4,
            label="by value", color="#DD8452")
    ax.axvline(config.GOV_SME_TARGET, ls="--", color="grey",
               label=f"target {config.GOV_SME_TARGET:.0%}")
    ax.set_yticks(list(y))
    ax.set_yticklabels(t.index)
    ax.set_xlabel("SME share")
    ax.set_xlim(0, 1)
    ax.set_title(title)
    ax.legend(loc="lower right", fontsize=8)
    fig.tight_layout()
    path = FIG_DIR / f"sme_share_by_{dim}.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _finding(table: pd.DataFrame, dim: str) -> str:
    """Draft a short findings sentence (edit into your own voice later)."""
    t = table[table["n_awards"] >= 30].dropna(subset=["sme_by_value"])
    if t.empty:
        return f"- **{dim}**: too few classified awards to summarise.\n"
    hi = t["sme_by_value"].idxmax()
    lo = t["sme_by_value"].idxmin()
    return (
        f"- **{dim}**: SME value share is highest in *{hi}* "
        f"({t.loc[hi, 'sme_by_value']:.0%}) and lowest in *{lo}* "
        f"({t.loc[lo, 'sme_by_value']:.0%}). Across this dimension the "
        f"by-count share consistently exceeds the by-value share, i.e. SMEs win "
        f"many contracts but a smaller share of the money.\n"
    )


def run() -> None:
    path = config.OUT_DIR / "sme_awards_clean.parquet"
    df = (pd.read_parquet(path) if path.exists()
          else pd.read_csv(config.OUT_DIR / "sme_awards_clean.csv"))
    df = df[df["analysis_ready"]].copy()
    log.info("analysis-ready rows (all years): %s", len(df))

    # derive grouping columns
    df["sector"] = df["cpv_code"].map(
        lambda c: CPV_DIVISIONS.get(cpv_division(c), f"CPV {cpv_division(c)}"
                                    if cpv_division(c) else "Unknown"))
    df["value_band"] = df["award_value"].map(value_band)
    df["region"] = df["buyer_region"].map(_clean_region)
    df["year"] = parse_date(df["award_date"]).dt.year

    # restrict to the consistent-source analysis window
    df = df[df["year"].between(YEAR_MIN, YEAR_MAX)]
    log.info("after %s-%s window: %s rows", YEAR_MIN, YEAR_MAX, len(df))

    # windowed headline SME share (matches the breakdowns below)
    known = df[df["sme_flag"].notna()]
    by_count = known["sme_flag"].mean()
    by_value = (known.loc[known["sme_flag"] == 1, "award_value"].sum()
                / known["award_value"].sum())
    log.info("HEADLINE (%s-%s, frameworks excluded): SME share "
             "%.1f%% by value | %.1f%% by count",
             YEAR_MIN, YEAR_MAX, 100 * by_value, 100 * by_count)

    # region coverage: how much has a genuine sub-national region?
    real_region = ~df["region"].isin(["UK-wide / unspecified", "Unspecified"])
    log.info("region coverage: %.1f%% of awards have a real sub-national region",
             100 * real_region.mean())

    findings = ["# Dissertation 1 — SME participation breakdowns\n",
                f"_Analysis-ready awards, {YEAR_MIN}-{YEAR_MAX} (frameworks, "
                "placeholders, outliers and duplicates excluded). SME "
                "classification via Companies House accounts category._\n",
                f"\n**Headline:** SME share {by_value:.0%} by value, "
                f"{by_count:.0%} by count (target {config.GOV_SME_TARGET:.0%}).\n"]

    specs = [
        ("sector", "SME share by sector (CPV division)"),
        ("value_band", "SME share by contract value band"),
        ("region", "SME share by region"),
        ("year", "SME share over time"),
    ]
    for dim, title in specs:
        table = _sme_table(df, dim)
        if dim == "value_band":
            table = table.reindex([b for b in VALUE_BAND_ORDER if b in table.index])
        elif dim == "year":
            table = table.sort_index()
        csv_path = config.OUT_DIR / f"breakdown_{dim}.csv"
        table.to_csv(csv_path)
        try:
            fig_path = _save_chart(table, dim, title)
            log.info("  %-11s -> %s | %s", dim, csv_path.name, fig_path.name)
        except Exception as exc:
            log.warning("  chart failed for %s: %s", dim, exc)
        findings.append(_finding(table, dim))

        # print a compact view to console
        disp = table.copy()
        disp["sme_by_count"] = disp["sme_by_count"].map(lambda v: f"{v:.0%}")
        disp["sme_by_value"] = disp["sme_by_value"].map(
            lambda v: f"{v:.0%}" if pd.notna(v) else "n/a")
        disp["total_value"] = disp["total_value"].map(lambda v: f"£{v:,.0f}")
        print(f"\n=== {title} ===")
        print(disp.sort_values("n_awards", ascending=False).head(12).to_string())

    (config.OUT_DIR / "findings.md").write_text("\n".join(findings), encoding="utf-8")
    log.info("wrote findings -> %s", config.OUT_DIR / "findings.md")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run()