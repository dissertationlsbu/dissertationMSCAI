"""
Parse OCDS (Open Contracting Data Standard) releases into flat award rows.

Both Find a Tender and Contracts Finder publish OCDS. A "release" describes a
notice; the parts we care about for SME analysis live in:

    release.tender.classification        -> CPV code + description
    release.buyer.name                   -> contracting authority
    release.awards[].suppliers[]         -> who won (name + identifier)
    release.awards[].value               -> award value + currency
    release.awards[].date                -> award date

One award can have several suppliers (consortium); we emit one row per
supplier so the supplier->Companies House link is clean.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterator


def _first(*vals):
    for v in vals:
        if v not in (None, "", []):
            return v
    return None


def _supplier_company_number(supplier: dict) -> tuple[str | None, str | None]:
    """Pull a Companies House number straight from OCDS if present.

    OCDS suppliers often carry identifier.scheme == 'GB-COH' with the company
    number in identifier.id. Using it avoids a name search later.
    """
    ident = supplier.get("identifier") or {}
    scheme = ident.get("scheme")
    cid = ident.get("id")
    if scheme and "COH" in scheme.upper() and cid:
        return scheme, str(cid).strip()
    # Some publishers nest it under additionalIdentifiers
    for ai in supplier.get("additionalIdentifiers", []) or []:
        if ai.get("scheme") and "COH" in ai["scheme"].upper() and ai.get("id"):
            return ai["scheme"], str(ai["id"]).strip()
    return scheme, None


def parse_release(release: dict, source: str) -> Iterator[dict]:
    ocid = release.get("ocid")
    notice_id = release.get("id")
    tender = release.get("tender") or {}
    buyer = release.get("buyer") or {}
    parties = {p.get("id"): p for p in release.get("parties", []) or []}

    # CPV classification (main + any additional)
    cls = tender.get("classification") or {}
    cpv_code = cls.get("id")
    cpv_desc = cls.get("description")

    # Buyer region: try the buyer party's address region/country
    buyer_party = parties.get(buyer.get("id")) if buyer.get("id") else None
    buyer_region = None
    if buyer_party:
        addr = (buyer_party.get("address") or {})
        buyer_region = _first(addr.get("region"), addr.get("countryName"))

    title = _first(tender.get("title"), release.get("title"))

    # Procurement-level fields used to detect frameworks during cleaning.
    tender_value = (tender.get("value") or {}).get("amount")
    proc_method = tender.get("procurementMethod")
    proc_method_details = tender.get("procurementMethodDetails")
    main_category = tender.get("mainProcurementCategory")
    lot_count = len(tender.get("lots") or [])

    for award in release.get("awards", []) or []:
        value = award.get("value") or {}
        amount = value.get("amount")
        currency = value.get("currency")
        award_date = _first(award.get("date"), award.get("contractPeriod", {}).get("startDate"))

        suppliers = award.get("suppliers") or []
        if not suppliers:
            continue
        supplier_count = len(suppliers)
        for idx, sup in enumerate(suppliers):
            scheme, company_number = _supplier_company_number(sup)
            name = (sup.get("name") or "").strip()
            uid_seed = f"{source}|{ocid}|{award.get('id')}|{idx}|{name}"
            award_uid = hashlib.sha1(uid_seed.encode("utf-8")).hexdigest()
            yield {
                "award_uid": award_uid,
                "source": source,
                "ocid": ocid,
                "notice_id": notice_id,
                "title": title,
                "buyer_name": buyer.get("name"),
                "buyer_region": buyer_region,
                "cpv_code": cpv_code,
                "cpv_description": cpv_desc,
                "award_date": award_date,
                "award_value": float(amount) if amount is not None else None,
                "currency": currency,
                "supplier_name": name,
                "supplier_scheme": scheme,
                "supplier_id": company_number,
                "tender_value": float(tender_value) if tender_value is not None else None,
                "procurement_method": proc_method,
                "procurement_method_details": proc_method_details,
                "main_category": main_category,
                "lot_count": lot_count,
                "supplier_count": supplier_count,
                "raw": json.dumps(award, ensure_ascii=False),
            }


def parse_release_package(package: dict, source: str) -> Iterator[dict]:
    """A release package wraps a list of releases under `releases`."""
    for release in package.get("releases", []) or []:
        yield from parse_release(release, source)