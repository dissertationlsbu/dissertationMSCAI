"""
Contracts Finder acquirer.

Contracts Finder exposes an OCDS search endpoint:
    GET /Published/Notices/OCDS/Search
Key query params:
    publishedFrom / publishedTo : 'YYYY-MM-DD' bounds
    stages                      : 'award'
    size                        : page size (max 100)
The response carries OCDS release packages under `results`, with a
`links.next` URL for the following page.
"""

from __future__ import annotations

import logging

import config
from src import storage
from src.acquire.http import get_json, RateLimiter
from src.clean.ocds_parser import parse_release_package, parse_release

log = logging.getLogger("acquire.cf")
_limiter = RateLimiter(min_interval=0.4)


def _extract_rows(data: dict):
    """CF's payload shape has varied; handle both release-packages under
    `results` and bare release lists defensively."""
    results = data.get("results")
    if results:
        for item in results:
            if "releases" in item:                 # release package
                yield from parse_release_package(item, source="contracts_finder")
            elif "ocid" in item:                    # bare release
                yield from parse_release(item, source="contracts_finder")
    elif "releases" in data:                        # top-level package
        yield from parse_release_package(data, source="contracts_finder")


def acquire(date_from: str = config.DATE_FROM,
            date_to: str = config.DATE_TO,
            max_pages: int | None = None) -> int:
    params = {
        "publishedFrom": date_from,
        "publishedTo": date_to,
        "stages": "award",
        "size": config.CF_PAGE_SIZE,
        "order": "desc",
    }
    url = config.CF_BASE
    total = 0
    page = 0
    with storage.connect() as conn:
        while url:
            page += 1
            page_key = f"cf|{date_from}|{date_to}|p{page}"
            _limiter.wait()
            log.info("Contracts Finder page %s", page)
            data = get_json(url, params=params)
            storage.save_raw_json(f"cf_{date_from}_{date_to}_p{page}", data)

            rows = list(_extract_rows(data))
            total += storage.upsert_awards(conn, rows)
            storage.mark_page(conn, page_key)

            url = (data.get("links") or {}).get("next")
            params = None
            if max_pages and page >= max_pages:
                break
    log.info("Contracts Finder done: %s award rows", total)
    return total
