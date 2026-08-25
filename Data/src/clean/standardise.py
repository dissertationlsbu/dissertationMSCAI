"""
Standardisation helpers used during cleaning.

The most important one is supplier-name normalisation: procurement notices
spell the same company many ways ("ACME LTD", "Acme Limited", "Acme Ltd."),
so we normalise to a canonical key before matching to Companies House and
before de-duplicating supplier counts.
"""

from __future__ import annotations

import re

import pandas as pd

# Common UK company-suffix variants -> dropped for the match key.
_SUFFIXES = [
    "limited", "ltd", "plc", "llp", "lp", "cic", "cio",
    "company", "co", "incorporated", "inc", "the",
]
_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(re.escape(s) for s in _SUFFIXES) + r")\b", re.IGNORECASE
)
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalise_name(name: str | None) -> str:
    """Canonical supplier key: lowercase, strip punctuation + legal suffixes."""
    if not name:
        return ""
    s = name.lower()
    s = s.replace("&", " and ")
    s = _NON_ALNUM.sub(" ", s)
    s = _SUFFIX_RE.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    return s


def standardise_cpv(code: str | None) -> str | None:
    """CPV codes are 8 digits + check digit (e.g. 45000000-7). Keep the
    8-digit stem; the first 2 digits give the top-level division/sector."""
    if not code:
        return None
    digits = re.sub(r"\D", "", str(code))
    return digits[:8] if digits else None


def cpv_division(code: str | None) -> str | None:
    """First two CPV digits = high-level sector division."""
    c = standardise_cpv(code)
    return c[:2] if c else None


def clean_value(amount, currency: str | None) -> float | None:
    """Return GBP value or None. (Non-GBP left as-is here; convert upstream
    if you add FX rates — most UK procurement is already GBP.)"""
    if amount is None:
        return None
    try:
        v = float(amount)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v


def value_band(amount: float | None) -> str | None:
    """Contract value bands aligned to common UK procurement thresholds."""
    if amount is None:
        return None
    if amount < 25_000:
        return "<25k"
    if amount < 100_000:
        return "25k-100k"
    if amount < 500_000:
        return "100k-500k"
    if amount < 1_000_000:
        return "500k-1m"
    if amount < 5_000_000:
        return "1m-5m"
    return "5m+"


def parse_date(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", utc=True)
