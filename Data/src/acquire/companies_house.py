"""
Companies House acquirer and SME classifier.

For every distinct supplier in the awards table we try to attach a Companies
House record so the company's size and financials feed the SME analysis.

Resolution order for a supplier:
  1. Use the company number already present in the OCDS award (GB-COH id).
  2. Otherwise search Companies House by normalised name and take the best
     active match.

For each resolved company we fetch the profile and derive an SME flag:
  - If filed financials (turnover / employees) are available -> apply the
    Companies Act size bands directly (most reliable).
  - Else fall back to the Companies House "accounts category" proxy
    (micro-entity / small / medium -> SME; full / group -> likely large).

Detailed turnover/employee figures live inside the iXBRL accounts documents
(Document API); extracting them is optional and stubbed below — the accounts
category proxy is enough for a first, defensible classification.
"""

from __future__ import annotations

import logging

try:
    from rapidfuzz import fuzz          # better fuzzy matching if installed
    _HAVE_FUZZ = True
except ImportError:                      # fallback to stdlib difflib
    from difflib import SequenceMatcher
    _HAVE_FUZZ = False

    class fuzz:                          # minimal shim with the same call we use
        @staticmethod
        def token_sort_ratio(a: str, b: str) -> float:
            a2 = " ".join(sorted(a.split()))
            b2 = " ".join(sorted(b.split()))
            return SequenceMatcher(None, a2, b2).ratio() * 100

import config
from src import storage
from src.acquire.http import get_json, RateLimiter
from src.clean.standardise import normalise_name

log = logging.getLogger("acquire.ch")
_limiter = RateLimiter(min_interval=config.CH_MIN_INTERVAL)
_auth = (config.CH_API_KEY, "")  # Basic auth: key as username, blank password


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def search_company(name: str) -> dict | None:
    """Best-effort name -> company match. Prefers active companies and a high
    fuzzy similarity to avoid false links."""
    if not config.CH_API_KEY:
        raise RuntimeError("COMPANIES_HOUSE_API_KEY is not set.")
    _limiter.wait()
    data = get_json(
        f"{config.CH_BASE}/search/companies",
        params={"q": name, "items_per_page": 20},
        auth=_auth,
    )
    items = data.get("items", []) or []
    if not items:
        return None

    target = normalise_name(name)
    best, best_score = None, -1.0
    for it in items:
        cand = normalise_name(it.get("title"))
        try:
            score = fuzz.token_sort_ratio(target, cand)
        except Exception:
            score = 100.0 if cand == target else 0.0
        # Reward active companies slightly so a live match beats a dissolved one.
        if (it.get("company_status") or "").lower() == "active":
            score += 5
        if score > best_score:
            best, best_score = it, score
    # Require a reasonable similarity to accept the link.
    if best is not None and best_score >= 88:
        return best
    return None


# --------------------------------------------------------------------------- #
# Profile + financials
# --------------------------------------------------------------------------- #
def fetch_profile(company_number: str) -> dict | None:
    _limiter.wait()
    return get_json(f"{config.CH_BASE}/company/{company_number}", auth=_auth)


def fetch_financials(company_number: str) -> dict:
    """Stub for detailed financial extraction.

    Returns {} by default. To enable: hit /company/{n}/filing-history, find the
    latest 'accounts' filing, download its iXBRL document via the Document API,
    and parse turnover / employee tags. Left optional because the accounts
    category proxy already supports SME classification, and iXBRL parsing is a
    heavier dependency. See README "Extending: detailed financials".
    """
    return {}


# --------------------------------------------------------------------------- #
# SME classification
# --------------------------------------------------------------------------- #
def classify_sme(profile: dict, financials: dict) -> tuple[int | None, str]:
    """Return (sme_flag, basis). sme_flag is 1 SME, 0 large, None unknown."""
    # 1) Direct: filed financials against Companies Act size bands.
    turnover = financials.get("turnover")
    employees = financials.get("employees")
    if turnover is not None or employees is not None:
        med = config.SIZE_BANDS["medium"]
        criteria = []
        if turnover is not None:
            criteria.append(turnover <= med["turnover"])
        if employees is not None:
            criteria.append(employees <= med["employees"])
        # SME if it satisfies the medium-or-smaller test on what we have.
        if criteria:
            is_sme = sum(criteria) >= max(1, len(criteria) - 1)
            return (1 if is_sme else 0), "filed_financials"

    # 2) Proxy: Companies House accounts category.
    acct = ((profile.get("accounts") or {}).get("last_accounts") or {})
    acct_type = (acct.get("type") or "").lower()
    if acct_type in config.CH_ACCOUNTS_SME_PROXY:
        return 1, f"accounts_proxy:{acct_type}"
    if acct_type in config.CH_ACCOUNTS_LARGE_PROXY:
        return 0, f"accounts_proxy:{acct_type}"

    return None, "unknown"


def _profile_to_company_row(profile: dict, matched_name: str,
                            financials: dict) -> dict:
    sme_flag, basis = classify_sme(profile, financials)
    return {
        "company_number": profile.get("company_number"),
        "matched_name": matched_name,
        "company_status": profile.get("company_status"),
        "company_type": profile.get("type"),
        "date_of_creation": profile.get("date_of_creation"),
        "sic_codes": ",".join(profile.get("sic_codes", []) or []),
        "accounts_type": (
            (profile.get("accounts") or {}).get("last_accounts") or {}
        ).get("type"),
        "turnover": financials.get("turnover"),
        "employees": financials.get("employees"),
        "sme_flag": sme_flag,
        "sme_basis": basis,
        "raw": None,
    }


# --------------------------------------------------------------------------- #
# Orchestration over all suppliers in the awards table
# --------------------------------------------------------------------------- #
def enrich_suppliers(limit: int | None = None) -> dict:
    """Resolve + enrich every distinct supplier. Resumable: already-resolved
    suppliers and already-fetched companies are skipped."""
    stats = {"suppliers": 0, "matched": 0, "no_match": 0, "cached": 0, "errors": 0}
    with storage.connect() as conn:
        rows = conn.execute(
            """
            SELECT supplier_name, MAX(supplier_id) AS supplier_id
            FROM awards
            WHERE supplier_name <> ''
            GROUP BY supplier_name
            """
        ).fetchall()
        if limit:
            rows = rows[:limit]

        for row in rows:
            stats["suppliers"] += 1
            name = row["supplier_name"]
            ocds_number = row["supplier_id"]
            key = name  # awards already store the raw name; lookup keys on it

            prior = storage.get_resolved_supplier(conn, key)
            if prior is not False:           # already attempted
                stats["cached"] += 1
                continue

            try:
                company_number = ocds_number
                matched_name = name
                if not company_number:
                    match = search_company(name)
                    if match:
                        company_number = match.get("company_number")
                        matched_name = match.get("title", name)

                if not company_number:
                    storage.set_supplier_lookup(conn, key, None)
                    stats["no_match"] += 1
                    continue

                if not storage.company_exists(conn, company_number):
                    profile = fetch_profile(company_number)
                    financials = fetch_financials(company_number)
                    storage.upsert_company(
                        conn,
                        _profile_to_company_row(profile, matched_name, financials),
                    )
                storage.set_supplier_lookup(conn, key, company_number)
                stats["matched"] += 1

            except Exception as exc:  # keep going; log and move on
                stats["errors"] += 1
                log.warning("supplier '%s' failed: %s", name, exc)

    log.info("CH enrichment: %s", stats)
    return stats
