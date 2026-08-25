#!/usr/bin/env python3
"""
CLI for the SME procurement data build (Dissertation 1).

Examples
--------
  # small test pull (2 pages per source, 50 suppliers, no CH key needed if skipped)
  python run.py --from 2024-01-01 --to 2024-03-31 --max-pages 2 \
                --supplier-limit 50 --skip-companies-house

  # full build
  export COMPANIES_HOUSE_API_KEY=xxxx
  python run.py --from 2021-01-01 --to 2024-12-31
"""

import argparse
import logging

import config
from src import pipeline


def main():
    p = argparse.ArgumentParser(description="Build the SME procurement dataset.")
    p.add_argument("--from", dest="date_from", default=config.DATE_FROM)
    p.add_argument("--to", dest="date_to", default=config.DATE_TO)
    p.add_argument("--max-pages", type=int, default=None,
                   help="cap pages per award source (for quick tests)")
    p.add_argument("--supplier-limit", type=int, default=None,
                   help="cap suppliers enriched via Companies House")
    p.add_argument("--skip-award-apis", action="store_true",
                   help="skip FTS/CF API pull (use when you ingested a bulk file)")
    p.add_argument("--skip-companies-house", action="store_true")
    p.add_argument("--skip-cabinet-office", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    pipeline.run(
        date_from=args.date_from,
        date_to=args.date_to,
        max_pages=args.max_pages,
        supplier_limit=args.supplier_limit,
        skip_award_apis=args.skip_award_apis,
        skip_companies_house=args.skip_companies_house,
        skip_cabinet_office=args.skip_cabinet_office,
    )


if __name__ == "__main__":
    main()
