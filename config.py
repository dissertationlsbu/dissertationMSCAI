"""
Central configuration for the SME procurement data pipeline (Dissertation 1).

All tunable settings live here so the acquisition / cleaning / analysis code
stays free of hard-coded values. API keys are read from environment variables
so nothing secret is committed to the repo.

Set your Companies House key before running:
    export COMPANIES_HOUSE_API_KEY="your-key-here"      (mac/linux)
    setx COMPANIES_HOUSE_API_KEY "your-key-here"          (windows, new shell after)
"""

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"            # raw JSON pulled from each API (audit trail)
DB_PATH = DATA_DIR / "cache.sqlite"  # resumable cache of awards + companies
OUT_DIR = DATA_DIR / "processed"     # analytical datasets (parquet / csv)

for _d in (DATA_DIR, RAW_DIR, OUT_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- #
# Collection window
# --------------------------------------------------------------------------- #
# Default window covers enough years to study "change over time" (RQ3).
# Override per-run from the CLI if you want a smaller test pull.
DATE_FROM = os.getenv("PROC_DATE_FROM", "2021-01-01")
DATE_TO = os.getenv("PROC_DATE_TO", "2024-12-31")

# --------------------------------------------------------------------------- #
# Find a Tender Service (FTS) — OCDS API (no key required)
# --------------------------------------------------------------------------- #
FTS_BASE = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
FTS_PAGE_LIMIT = 100          # records per page (API max)

# --------------------------------------------------------------------------- #
# Contracts Finder — OCDS Search API (no key required)
# --------------------------------------------------------------------------- #
CF_BASE = "https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search"
CF_PAGE_SIZE = 100            # records per page (API max)

# --------------------------------------------------------------------------- #
# Companies House — Public Data API (Basic auth: key as username, blank pass)
# --------------------------------------------------------------------------- #
CH_BASE = "https://api.company-information.service.gov.uk"
CH_API_KEY = os.getenv("COMPANIES_HOUSE_API_KEY", "")
# CH rate limit is 600 requests / 5 min => 1 request / 0.5s is safe.
CH_MIN_INTERVAL = float(os.getenv("CH_MIN_INTERVAL", "0.55"))

# --------------------------------------------------------------------------- #
# Cabinet Office transparency data (spend-over-£25k CSVs etc.)
# --------------------------------------------------------------------------- #
# These are bulk CSV/ODS downloads rather than a single API. Put the URLs you
# want to ingest here (gov.uk publishes one file per department per month).
# Leave empty to skip; the loader also accepts local files dropped in data/raw.
CABINET_OFFICE_CSV_URLS: list[str] = []

# --------------------------------------------------------------------------- #
# HTTP behaviour
# --------------------------------------------------------------------------- #
USER_AGENT = "LSBU-MSc-procurement-research/1.0 (academic; contact via buildwithali.me)"
REQUEST_TIMEOUT = 60          # seconds
MAX_RETRIES = 5

# --------------------------------------------------------------------------- #
# SME classification thresholds
# --------------------------------------------------------------------------- #
# UK Companies Act 2006 size bands (a company qualifies if it meets >= 2 of 3).
# Turnover / balance-sheet in GBP. We use these where detailed accounts exist;
# otherwise we fall back to the Companies House "accounts category" proxy.
SIZE_BANDS = {
    "micro":  {"turnover": 632_000,    "balance_sheet": 316_000,    "employees": 10},
    "small":  {"turnover": 10_200_000, "balance_sheet": 5_100_000,  "employees": 50},
    "medium": {"turnover": 36_000_000, "balance_sheet": 18_000_000, "employees": 250},
}
# An organisation is an SME if it is medium-sized or smaller (< 250 employees).
SME_BANDS = {"micro", "small", "medium"}

# Companies House `accounts.last_accounts.type` values that imply SME size.
# Used as a fallback proxy when filed financials are unavailable.
CH_ACCOUNTS_SME_PROXY = {
    "micro-entity", "small", "medium", "total-exemption-full",
    "total-exemption-small", "dormant",
}
CH_ACCOUNTS_LARGE_PROXY = {"full", "group", "audit-exemption-subsidiary"}

# The government's stated aspiration for SME share of public procurement spend.
GOV_SME_TARGET = 0.33
