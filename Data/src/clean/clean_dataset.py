"""Cleaning + flagging for Dissertation 1 — frameworks flagged via method, structure, and a stated value ceiling."""

from __future__ import annotations

import logging
import re

import pandas as pd

from src import storage
import config

log = logging.getLogger("clean")

# Stated single-award ceiling: genuine single public contracts above this are
# rare and almost always frameworks/capital ceilings. Reported as a sensitivity.
SINGLE_AWARD_CEILING = 100_000_000  # £100m

_PLACEHOLDER = re.compile(
    r"attachment|successful supplier|withheld|redact|refer to|list of|"
    r"awarded to \d+|various supplier|see notice|see webpage|not applicable|"
    r"^n/?a$|multiple suppl|tbc|to be confirmed|see contract|please see",
    re.IGNORECASE,
)


def _flag(df: pd.DataFrame) -> pd.DataFrame:
    name = df["supplier_name"].fillna("")
    method = df["procurement_method_details"].fillna("")
    title = df["title"].fillna("")

    df["is_placeholder"] = name.str.contains(_PLACEHOLDER, na=False)

    # (a) method states it explicitly
    fw_method = method.str.contains(
        r"framework|call-?off|call off|dynamic purchasing|\bdps\b", case=False, na=False)
    fw_title = title.str.contains(
        r"framework|dynamic purchasing|\bdps\b|call-?off", case=False, na=False)
    fw_multilot = df["lot_count"].fillna(0) > 1

    # (b) structural: many suppliers share the SAME (buyer, title, value) — the
    #     framework ceiling stamped onto every supplier row (e.g. "3S_22
    #     Strategic HR Services"). Count distinct suppliers per group.
    grp = (df["buyer_name"].fillna("") + "|" + title + "|"
           + df["award_value"].fillna(-1).astype(str))
    suppliers_per_group = df.groupby(grp)["supplier_name"].transform("nunique")
    fw_multisupplier = (suppliers_per_group >= 4) & (df["award_value"].fillna(0) > 0)

    # (c) supplier_count on the award itself indicates a multi-award framework
    fw_supcount = df["supplier_count"].fillna(0) >= 4

    df["is_framework"] = (
        fw_method | fw_title | fw_multilot | fw_multisupplier | fw_supcount)

    # stated value ceiling — backstop for residual ceiling figures
    df["is_outlier"] = df["award_value"].fillna(0) > SINGLE_AWARD_CEILING

# Genuine duplicate award records: the same notice repeats an identical
    # award block. Confirmed by diagnostics — every duplicate group shares an
    # identical notice_id. Flag every occurrence after the first (kept), which
    # then drops out of analysis_ready. Not a hard delete.
    df["is_duplicate"] = df.duplicated(
        subset=["notice_id", "supplier_name", "award_value", "award_date"],
        keep="first",
    ) & (df["supplier_name"].fillna("").str.strip() != "")

    df["analysis_ready"] = (
        ~df["is_placeholder"] & ~df["is_framework"]
        & ~df["is_outlier"] & ~df["is_duplicate"]
        & (df["award_value"].fillna(0) > 0)
    )
    return df


def run() -> pd.DataFrame:
    with storage.connect() as conn:
        df = pd.read_sql_query(
            """
            SELECT a.*, c.company_number AS ch_number, c.accounts_type,
                   c.company_status, c.sme_flag, c.sme_basis
            FROM awards a
            LEFT JOIN supplier_lookup s ON s.supplier_key = a.supplier_name
            LEFT JOIN companies c ON c.company_number = s.company_number
            """,
            conn,
        )
    log.info("loaded %s award rows", len(df))
    df = _flag(df)

    for col in ["is_placeholder", "is_framework", "is_outlier",
                "is_duplicate", "analysis_ready"]:
        log.info("  %-15s : %s (%.1f%%)", col, int(df[col].sum()),
                 100 * df[col].mean())

    out_parquet = config.OUT_DIR / "sme_awards_clean.parquet"
    out_csv = config.OUT_DIR / "sme_awards_clean.csv"
    try:
        df.to_parquet(out_parquet, index=False)
    except Exception:
        pass
    df.to_csv(out_csv, index=False)
    log.info("wrote cleaned dataset -> %s", out_csv)
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run()