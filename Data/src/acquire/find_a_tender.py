"""
Find a Tender Service (FTS) acquirer.

FTS publishes OCDS release packages at:
    GET /api/1.0/ocdsReleasePackages
Key query params:
    updatedFrom / updatedTo : ISO datetimes bounding the search
    stages                  : we want 'award'
    limit                   : page size
Pagination is cursor-based via the `links.next` URL in each response.

No API key is required, but be polite with the rate.
"""

from __future__ import annotations

import logging

import config
from src import storage
from src.acquire.http import get_json, RateLimiter
from src.clean.ocds_parser import parse_release_package

log = logging.getLogger("acquire.fts")
_limiter = RateLimiter(min_interval=0.4)


def _iso(date_str: str, end: bool = False) -> str:
    """'2024-01-01' -> '2024-01-01T00:00:00' (or T23:59:59 for the end)."""
    t = "T23:59:59" if end else "T00:00:00"
    return f"{date_str}{t}"


def acquire(date_from: str = config.DATE_FROM,
            date_to: str = config.DATE_TO,
            max_pages: int | None = None) -> int:
    """Pull award releases from FTS into the awards table. Returns row count."""
    params = {
        "updatedFrom": _iso(date_from),
        "updatedTo": _iso(date_to, end=True),
        "stages": "award",
        "limit": config.FTS_PAGE_LIMIT,
    }
    url = config.FTS_BASE
    total = 0
    page = 0
    with storage.connect() as conn:
        while url:
            page += 1
            page_key = f"fts|{date_from}|{date_to}|p{page}"
            _limiter.wait()
            log.info("FTS page %s", page)
            data = get_json(url, params=params)
            storage.save_raw_json(f"fts_{date_from}_{date_to}_p{page}", data)

            rows = list(parse_release_package(data, source="find_a_tender"))
            total += storage.upsert_awards(conn, rows)
            storage.mark_page(conn, page_key)

            # Cursor pagination: follow links.next (params already encoded there)
            url = (data.get("links") or {}).get("next")
            params = None  # next URL carries its own query string
            if max_pages and page >= max_pages:
                break
    log.info("FTS done: %s award rows", total)
    return total
