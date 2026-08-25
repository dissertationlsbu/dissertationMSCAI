"""
Local ingest: load data you have ALREADY pulled into the cache, without
re-downloading. Use this to bring your existing Contracts Finder extraction
into the modular pipeline.

Auto-detects the file shape:
  1. OCDS release package      -> {"releases": [...]}        (Find a Tender shape)
  2. OCDS release list         -> [{"ocid": ...}, ...]
  3. Legacy Contracts Finder   -> CF's native notice JSON (POST Search API)
  4. Flat CSV                  -> one award per row, columns auto-mapped

Run:
    python -m src.acquire.ingest_local data/raw/contracts_finder
    python -m src.acquire.ingest_local path/to/your_old_pull.json --source contracts_finder
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import logging
import sys
from pathlib import Path
from typing import Iterator

from src import storage
from src.clean.ocds_parser import parse_release, parse_release_package
from src.clean.standardise import standardise_cpv

log = logging.getLogger("acquire.ingest_local")


# --------------------------------------------------------------------------- #
# OCDS bulk JSON Lines (.jsonl / .jsonl.gz) — streamed, memory-safe
# --------------------------------------------------------------------------- #
# The OCDS data-registry "full" download is one JSON object per line, usually a
# *record package* (records carry a `compiledRelease` = merged final state).
# These files can be multi-GB, so we stream line by line and commit in batches
# rather than loading everything into memory.
def _rows_from_jsonl_obj(obj: dict, default_source: str) -> Iterator[dict]:
    # Record package: {"records": [{"compiledRelease": {...}}, ...]}
    if "records" in obj:
        for rec in obj.get("records") or []:
            rel = rec.get("compiledRelease")
            if not rel:                          # fall back to latest release
                rels = rec.get("releases") or []
                rel = rels[-1] if rels else None
            if rel:
                yield from parse_release(rel, source=default_source)
        return
    # Release package: {"releases": [...]}
    if "releases" in obj:
        yield from parse_release_package(obj, source=default_source)
        return
    # Bare record with compiledRelease
    if "compiledRelease" in obj:
        yield from parse_release(obj["compiledRelease"], source=default_source)
        return
    # Bare release
    if "ocid" in obj:
        yield from parse_release(obj, source=default_source)


def _open_maybe_gz(path: Path):
    if path.suffix.lower() == ".gz":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="ignore")
    return open(path, "r", encoding="utf-8", errors="ignore")


def ingest_jsonl(path: Path, source: str = "find_a_tender",
                 batch: int = 5000) -> int:
    """Stream an OCDS .jsonl / .jsonl.gz file into the awards cache."""
    total = 0
    buffer: list[dict] = []
    with storage.connect() as conn, _open_maybe_gz(path) as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                log.warning("skipping malformed line %s", line_no)
                continue
            buffer.extend(_rows_from_jsonl_obj(obj, source))
            if len(buffer) >= batch:
                total += storage.upsert_awards(conn, buffer)
                buffer.clear()
                log.info("  ... %s award rows ingested", total)
        if buffer:
            total += storage.upsert_awards(conn, buffer)
    log.info("JSONL ingest complete: %s award rows from %s", total, path.name)
    return total


# --------------------------------------------------------------------------- #
# Legacy Contracts Finder native-JSON adapter
# --------------------------------------------------------------------------- #
# CF's native notice JSON uses different field names from OCDS. We map the most
# common variants; unknown layouts fall through to None and are logged.
# >>> If your file doesn't map cleanly, send me one sample record and I'll lock
#     these field names exactly. <<<
def _cf_native_to_rows(notice: dict) -> Iterator[dict]:
    def g(*keys):
        for k in keys:
            if k in notice and notice[k] not in (None, "", []):
                return notice[k]
        return None

    notice_id = g("id", "noticeIdentifier", "noticeId")
    title = g("title", "noticeTitle")
    buyer = g("organisationName", "buyerName", "contactDetailsOrganisationName")
    region = g("region", "regionText", "deliveryLocationText")
    cpv = g("cpvCodes", "cpvCode", "cpvCodesExtended")
    if isinstance(cpv, list):
        cpv = cpv[0] if cpv else None
    if isinstance(cpv, dict):
        cpv = cpv.get("code") or cpv.get("cpvCode")
    cpv_desc = g("cpvDescription")

    # Award block may be nested or flat depending on export.
    awards = g("awards", "awardedContracts", "awardDetail")
    if isinstance(awards, dict):
        awards = [awards]
    if not awards:
        # Some flat exports put a single supplier directly on the notice.
        supplier = g("awardedSupplier", "supplierName", "awardedToName")
        if supplier:
            awards = [{
                "supplierName": supplier,
                "awardedValue": g("awardedValue", "awardedContractValue", "valueHigh"),
                "awardedDate": g("awardedDate", "awardDate", "awardedOn"),
                "supplierCompaniesHouseNumber": g(
                    "awardedSupplierCompaniesHouseNumber",
                    "supplierCompaniesHouseNumber", "companiesHouseNumber"),
            }]
        else:
            awards = []

    for idx, aw in enumerate(awards or []):
        def ga(*keys):
            for k in keys:
                if k in aw and aw[k] not in (None, "", []):
                    return aw[k]
            return None
        supplier = ga("supplierName", "awardedSupplier", "name", "awardedToName")
        if not supplier:
            continue
        amount = ga("awardedValue", "value", "awardedContractValue", "valueHigh")
        try:
            amount = float(str(amount).replace(",", "").replace("£", "")) if amount else None
        except ValueError:
            amount = None
        company_number = ga(
            "supplierCompaniesHouseNumber", "companiesHouseNumber",
            "awardedSupplierCompaniesHouseNumber")
        uid_seed = f"contracts_finder|{notice_id}|{idx}|{supplier}"
        yield {
            "award_uid": hashlib.sha1(uid_seed.encode()).hexdigest(),
            "source": "contracts_finder",
            "ocid": None,
            "notice_id": notice_id,
            "title": title,
            "buyer_name": buyer,
            "buyer_region": region,
            "cpv_code": standardise_cpv(cpv),
            "cpv_description": cpv_desc,
            "award_date": ga("awardedDate", "awardDate", "awardedOn"),
            "award_value": amount,
            "currency": "GBP",
            "supplier_name": (supplier or "").strip(),
            "supplier_scheme": "GB-COH" if company_number else None,
            "supplier_id": str(company_number).strip() if company_number else None,
            "raw": json.dumps(aw, ensure_ascii=False),
        }


# --------------------------------------------------------------------------- #
# CSV adapter
# --------------------------------------------------------------------------- #
_CSV_MAP = {
    "supplier_name": ["supplier", "supplier_name", "awardedsupplier", "winner"],
    "buyer_name": ["buyer", "buyer_name", "organisationname", "authority"],
    "title": ["title", "notice_title"],
    "award_value": ["value", "award_value", "awardedvalue", "amount"],
    "award_date": ["date", "award_date", "awardeddate"],
    "cpv_code": ["cpv", "cpv_code", "cpvcodes"],
    "buyer_region": ["region"],
    "supplier_id": ["companies_house_number", "company_number", "chnumber"],
    "notice_id": ["id", "notice_id"],
}


def _csv_to_rows(path: Path) -> Iterator[dict]:
    with open(path, newline="", encoding="utf-8-sig", errors="ignore") as f:
        reader = csv.DictReader(f)
        header_lower = {h.lower().strip(): h for h in (reader.fieldnames or [])}

        def col(field):
            for cand in _CSV_MAP[field]:
                if cand in header_lower:
                    return header_lower[cand]
            return None

        cols = {f: col(f) for f in _CSV_MAP}
        for i, row in enumerate(reader):
            supplier = (row.get(cols["supplier_name"]) or "").strip() if cols["supplier_name"] else ""
            if not supplier:
                continue
            raw_val = row.get(cols["award_value"]) if cols["award_value"] else None
            try:
                amount = float(str(raw_val).replace(",", "").replace("£", "")) if raw_val else None
            except ValueError:
                amount = None
            chn = row.get(cols["supplier_id"]) if cols["supplier_id"] else None
            uid_seed = f"contracts_finder|{path.name}|{i}|{supplier}"
            yield {
                "award_uid": hashlib.sha1(uid_seed.encode()).hexdigest(),
                "source": "contracts_finder",
                "ocid": None,
                "notice_id": row.get(cols["notice_id"]) if cols["notice_id"] else None,
                "title": row.get(cols["title"]) if cols["title"] else None,
                "buyer_name": row.get(cols["buyer_name"]) if cols["buyer_name"] else None,
                "buyer_region": row.get(cols["buyer_region"]) if cols["buyer_region"] else None,
                "cpv_code": standardise_cpv(row.get(cols["cpv_code"])) if cols["cpv_code"] else None,
                "cpv_description": None,
                "award_date": row.get(cols["award_date"]) if cols["award_date"] else None,
                "award_value": amount,
                "currency": "GBP",
                "supplier_name": supplier,
                "supplier_scheme": "GB-COH" if chn else None,
                "supplier_id": str(chn).strip() if chn else None,
                "raw": json.dumps(row, ensure_ascii=False),
            }


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #
def _rows_from_file(path: Path, source: str | None) -> Iterator[dict]:
    if path.suffix.lower() == ".csv":
        yield from _csv_to_rows(path)
        return

    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))

    # OCDS release package
    if isinstance(data, dict) and "releases" in data:
        yield from parse_release_package(data, source=source or "find_a_tender")
        return
    # OCDS releases list
    if isinstance(data, list) and data and isinstance(data[0], dict) and "ocid" in data[0]:
        for rel in data:
            yield from parse_release(rel, source=source or "contracts_finder")
        return
    # Single OCDS release
    if isinstance(data, dict) and "ocid" in data and "releases" not in data:
        yield from parse_release(data, source=source or "contracts_finder")
        return

    # Legacy Contracts Finder native — could be {"results":[...]} or a bare list
    notices = data.get("results") if isinstance(data, dict) else data
    if isinstance(notices, dict):
        notices = notices.get("noticeList") or notices.get("notices") or [notices]
    for notice in notices or []:
        yield from _cf_native_to_rows(notice)


def ingest_path(target: str, source: str | None = None) -> int:
    p = Path(target)
    if p.is_dir():
        files = (sorted(p.glob("*.jsonl.gz")) + sorted(p.glob("*.jsonl"))
                 + sorted(p.glob("*.json")) + sorted(p.glob("*.csv")))
    else:
        files = [p]

    total = 0
    for f in files:
        name = f.name.lower()
        # Stream large OCDS line-delimited files; load others in one go.
        if name.endswith(".jsonl") or name.endswith(".jsonl.gz"):
            total += ingest_jsonl(f, source=source or "find_a_tender")
            continue
        with storage.connect() as conn:
            rows = list(_rows_from_file(f, source))
            n = storage.upsert_awards(conn, rows)
            total += n
            log.info("ingested %s rows from %s", n, f.name)
    log.info("local ingest complete: %s award rows", total)
    return total


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2:
        print("usage: python -m src.acquire.ingest_local <file-or-dir> [--source NAME]")
        sys.exit(1)
    src = None
    if "--source" in sys.argv:
        src = sys.argv[sys.argv.index("--source") + 1]
    ingest_path(sys.argv[1], source=src)
