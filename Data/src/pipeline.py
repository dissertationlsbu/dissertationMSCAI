"""
Pipeline orchestrator.

Runs the Dissertation-1 data build end to end:
  1. acquire award notices  (Find a Tender + Contracts Finder)
  2. enrich suppliers        (Companies House: link + classify SME)
  3. load Cabinet Office     (transparency spend, optional cross-check)
  4. export analytical dataset (parquet + csv)

Each step is independent and resumable, so you can re-run after an interruption
without re-downloading what you already have.
"""

from __future__ import annotations

import logging

import config
from src import storage
from src.acquire import find_a_tender, contracts_finder, companies_house, cabinet_office

log = logging.getLogger("pipeline")


def run(date_from: str = config.DATE_FROM,
        date_to: str = config.DATE_TO,
        max_pages: int | None = None,
        supplier_limit: int | None = None,
        skip_award_apis: bool = False,
        skip_companies_house: bool = False,
        skip_cabinet_office: bool = False):

    if not skip_award_apis:
        log.info("STEP 1/4  acquiring award notices  %s -> %s", date_from, date_to)
        fts_n = find_a_tender.acquire(date_from, date_to, max_pages=max_pages)
        cf_n = contracts_finder.acquire(date_from, date_to, max_pages=max_pages)
        log.info("acquired %s FTS + %s CF award rows", fts_n, cf_n)
    else:
        log.info("STEP 1/4  skipped (award APIs) - using already-ingested data")

    if not skip_companies_house:
        log.info("STEP 2/4  enriching suppliers via Companies House")
        companies_house.enrich_suppliers(limit=supplier_limit)
    else:
        log.info("STEP 2/4  skipped (Companies House)")

    if not skip_cabinet_office:
        log.info("STEP 3/4  loading Cabinet Office transparency spend")
        cabinet_office.acquire()
    else:
        log.info("STEP 3/4  skipped (Cabinet Office)")

    log.info("STEP 4/4  exporting analytical dataset")
    with storage.connect() as conn:
        df = storage.export_dataset(conn)
    log.info("exported %s award rows to %s", len(df), config.OUT_DIR)
    return df
