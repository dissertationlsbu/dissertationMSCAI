"""
Cabinet Office transparency data loader.

Departments publish 'spend over £25k' transparency files (CSV/ODS). These are
not a single API, so this loader takes either:
  - URLs listed in config.CABINET_OFFICE_CSV_URLS, or
  - any *.csv files you drop into data/raw/cabinet_office/

These files use the actual *payments* a department made to suppliers, which is
a useful cross-check on the award-notice picture (notices can over- or under-
state realised spend). We don't force them into the awards schema; we keep a
tidy supplier-level spend table for triangulation in the analysis stage.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import requests

import config
from src.clean.standardise import normalise_name

log = logging.getLogger("acquire.cabinet_office")

LOCAL_DIR = config.RAW_DIR / "cabinet_office"
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

# Column names vary across departments; map common variants.
_SUPPLIER_COLS = ["supplier", "supplier name", "merchant", "payee"]
_AMOUNT_COLS = ["amount", "gross", "net amount", "value", "spend", "amount (gbp)"]
_DATE_COLS = ["date", "payment date", "transaction date", "date paid"]
_DEPT_COLS = ["entity", "department", "organisation", "body"]


def _pick(cols: list[str], candidates: list[str]) -> str | None:
    lower = {c.lower().strip(): c for c in cols}
    for cand in candidates:
        if cand in lower:
            return lower[cand]
    return None


def _read_one(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, on_bad_lines="skip", encoding_errors="ignore")
    cols = list(df.columns)
    sup = _pick(cols, _SUPPLIER_COLS)
    amt = _pick(cols, _AMOUNT_COLS)
    dt = _pick(cols, _DATE_COLS)
    dept = _pick(cols, _DEPT_COLS)
    if not sup or not amt:
        log.warning("skipping %s (no supplier/amount columns)", path.name)
        return pd.DataFrame()
    out = pd.DataFrame({
        "supplier_name": df[sup].fillna("").str.strip(),
        "amount": pd.to_numeric(
            df[amt].str.replace(r"[£,]", "", regex=True), errors="coerce"
        ),
        "payment_date": pd.to_datetime(df[dt], errors="coerce") if dt else pd.NaT,
        "department": df[dept] if dept else path.stem,
        "source_file": path.name,
    })
    out["supplier_key"] = out["supplier_name"].map(normalise_name)
    return out.dropna(subset=["amount"])


def _download(url: str) -> Path:
    dest = LOCAL_DIR / Path(url).name
    if dest.exists():
        return dest
    log.info("downloading %s", url)
    r = requests.get(url, timeout=config.REQUEST_TIMEOUT,
                     headers={"User-Agent": config.USER_AGENT})
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def acquire() -> pd.DataFrame:
    """Load and combine all configured/local transparency files."""
    paths: list[Path] = []
    for url in config.CABINET_OFFICE_CSV_URLS:
        try:
            paths.append(_download(url))
        except Exception as exc:
            log.warning("download failed %s: %s", url, exc)
    paths.extend(sorted(LOCAL_DIR.glob("*.csv")))

    frames = [_read_one(p) for p in dict.fromkeys(paths)]  # de-dup paths
    frames = [f for f in frames if not f.empty]
    if not frames:
        log.info("no Cabinet Office files found")
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    out_path = config.OUT_DIR / "cabinet_office_spend.parquet"
    try:
        combined.to_parquet(out_path, index=False)
    except Exception:
        combined.to_csv(config.OUT_DIR / "cabinet_office_spend.csv", index=False)
    log.info("Cabinet Office: %s payment rows from %s files",
             len(combined), len(frames))
    return combined
