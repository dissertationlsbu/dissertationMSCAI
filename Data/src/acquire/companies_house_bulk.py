"""
Companies House SME classification via the BULK company data product.

The procurement OCDS files carry supplier *names* only (no company numbers),
and name-searching ~900k awards through the CH API would take days. Instead we
match locally against the free Companies House "Company Data Product":

    http://download.companieshouse.gov.uk/en_output.html
    -> "Company Data as one file": BasicCompanyDataAsOneFile-YYYY-MM-DD.zip

Unzip it; it contains one big CSV (~5M rows). Point this module at that CSV.
Relevant columns (header has stray spaces, so we match names case/space-
insensitively):
    CompanyName, CompanyNumber, CompanyStatus, CompanyCategory,
    Accounts.AccountCategory, SICCode.SicText_1, IncorporationDate

SME proxy from Accounts.AccountCategory:
    micro / small / medium / abridged / total-exemption / dormant -> SME
    full / group                                                  -> large
    no-accounts / not-applicable / blank                          -> unknown
This is the standard, citable proxy when filed turnover/employee figures aren't
available; document its limitations in the methodology.

Usage:
    python -m src.acquire.companies_house_bulk \
        "D:/.../BasicCompanyDataAsOneFile-2026-06-01.csv"
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

import config
from src import storage
from src.clean.standardise import normalise_name

log = logging.getLogger("acquire.ch_bulk")

# --------------------------------------------------------------------------- #
# Accounts category -> SME proxy   (values uppercased + stripped before lookup)
# --------------------------------------------------------------------------- #
_SME_CATEGORIES = {
    "MICRO ENTITY", "SMALL", "MEDIUM",
    "TOTAL EXEMPTION FULL", "TOTAL EXEMPTION SMALL",
    "UNAUDITED ABRIDGED", "AUDITED ABRIDGED",
    "DORMANT",
}
_LARGE_CATEGORIES = {"FULL", "GROUP"}
# Everything else (NO ACCOUNTS FILED, ACCOUNTS TYPE NOT APPLICABLE, INITIAL,
# subsidiary exemptions, blank) -> unknown, reported separately.


def classify_account_category(cat: str | None) -> tuple[int | None, str]:
    if not cat:
        return None, "unknown:no_category"
    c = str(cat).strip().upper()
    if c in _SME_CATEGORIES:
        return 1, f"bulk_accounts:{c.lower()}"
    if c in _LARGE_CATEGORIES:
        return 0, f"bulk_accounts:{c.lower()}"
    return None, f"unknown:{c.lower()}"


# --------------------------------------------------------------------------- #
# Build a normalised-name -> best record index from the bulk CSV
# --------------------------------------------------------------------------- #
def _resolve_columns(cols: list[str]) -> dict:
    lut = {c.strip().lower(): c for c in cols}
    want = {
        "name": "companyname",
        "number": "companynumber",
        "status": "companystatus",
        "category": "companycategory",
        "accounts": "accounts.accountcategory",
        "sic": "siccode.sictext_1",
        "incorp": "incorporationdate",
    }
    resolved = {}
    for key, target in want.items():
        if target in lut:
            resolved[key] = lut[target]
    return resolved


def build_index(csv_path: Path, chunksize: int = 200_000) -> dict:
    """Stream the CH bulk CSV and build {normalised_name: best_record}.

    Collisions (several companies share a normalised name) are resolved by
    preferring 'Active' status, then the most recent incorporation date.
    Returns a dict; also tells you how many names were ambiguous.
    """
    index: dict[str, dict] = {}
    ambiguous = 0
    rows_seen = 0

    # Peek header to resolve actual column names.
    head = pd.read_csv(csv_path, nrows=0)
    cols = _resolve_columns(list(head.columns))
    if "name" not in cols or "number" not in cols:
        raise RuntimeError(f"Could not find CompanyName/Number in {csv_path.name}; "
                           f"columns seen: {list(head.columns)[:8]}")

    usecols = [c for c in cols.values()]
    reader = pd.read_csv(
        csv_path, usecols=usecols, dtype=str, chunksize=chunksize,
        encoding_errors="ignore", on_bad_lines="skip",
    )
    for chunk in reader:
        chunk = chunk.rename(columns={v: k for k, v in cols.items()})
        chunk["key"] = chunk["name"].map(normalise_name)
        for rec in chunk.to_dict("records"):
            key = rec["key"]
            if not key:
                continue
            rows_seen += 1
            existing = index.get(key)
            if existing is None:
                index[key] = rec
            else:
                ambiguous += 1
                # Prefer active; then later incorporation date.
                e_active = (existing.get("status") or "").lower() == "active"
                r_active = (rec.get("status") or "").lower() == "active"
                if r_active and not e_active:
                    index[key] = rec
                elif r_active == e_active:
                    if (rec.get("incorp") or "") > (existing.get("incorp") or ""):
                        index[key] = rec
        log.info("  indexed %s companies (%s names so far)", rows_seen, len(index))

    log.info("bulk index built: %s unique names, %s collisions resolved",
             len(index), ambiguous)
    return index


# --------------------------------------------------------------------------- #
# Match distinct suppliers and write companies + supplier_lookup
# --------------------------------------------------------------------------- #
def match_suppliers(csv_path: str) -> dict:
    index = build_index(Path(csv_path))
    stats = {"suppliers": 0, "matched": 0, "no_match": 0,
             "sme": 0, "large": 0, "unknown": 0}

    # 1) Pull distinct suppliers.
    with storage.connect() as conn:
        suppliers = [r["supplier_name"] for r in conn.execute(
            "SELECT DISTINCT supplier_name FROM awards WHERE supplier_name <> ''"
        ).fetchall()]
    log.info("matching %s distinct suppliers against the index...", len(suppliers))

    # 2) Resolve in memory (fast) — collect rows, write once at the end.
    lookups: list[tuple] = []          # (supplier_key, company_number)
    companies: dict[str, dict] = {}    # company_number -> row
    for i, name in enumerate(suppliers, 1):
        stats["suppliers"] += 1
        rec = index.get(normalise_name(name))
        if not rec:
            lookups.append((name, None))
            stats["no_match"] += 1
        else:
            number = rec.get("number")
            sme_flag, basis = classify_account_category(rec.get("accounts"))
            companies[number] = {
                "company_number": number,
                "matched_name": rec.get("name"),
                "company_status": rec.get("status"),
                "company_type": rec.get("category"),
                "date_of_creation": rec.get("incorp"),
                "sic_codes": rec.get("sic"),
                "accounts_type": rec.get("accounts"),
                "turnover": None, "employees": None,
                "sme_flag": sme_flag, "sme_basis": basis, "raw": None,
            }
            lookups.append((name, number))
            stats["matched"] += 1
            stats["sme" if sme_flag == 1 else "large" if sme_flag == 0 else "unknown"] += 1
        if i % 50_000 == 0:
            log.info("  resolved %s/%s suppliers", i, len(suppliers))

    # 3) Bulk write in a SINGLE transaction (this is the speed fix).
    log.info("writing %s supplier links and %s companies...",
             len(lookups), len(companies))
    ccols = ["company_number", "matched_name", "company_status", "company_type",
             "date_of_creation", "sic_codes", "accounts_type", "turnover",
             "employees", "sme_flag", "sme_basis", "raw"]
    with storage.connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO supplier_lookup "
            "(supplier_key, company_number, resolved) VALUES (?, ?, 1)",
            lookups,
        )
        conn.executemany(
            f"INSERT OR REPLACE INTO companies ({','.join(ccols)}) "
            f"VALUES ({','.join('?' for _ in ccols)})",
            [[c.get(col) for col in ccols] for c in companies.values()],
        )
        conn.commit()

    log.info("supplier matching complete: %s", stats)
    return stats


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2:
        print("usage: python -m src.acquire.companies_house_bulk <BasicCompanyData.csv>")
        sys.exit(1)
    match_suppliers(sys.argv[1])