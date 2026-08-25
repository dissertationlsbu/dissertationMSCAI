# SME Public Procurement — Dissertation 1 data pipeline

**Data Integration and Statistical Analysis of SME Participation in UK Public Procurement**

This is the shared data foundation for the project: it acquires UK public
procurement award data, cleans and standardises it, links suppliers to
Companies House to classify them as SME or large, and produces one tidy
analytical dataset. The research angle for Dissertation 1 is *why SME direct
procurement spend sits around 20% against the government's 33% aspiration* —
this pipeline builds the evidence base to answer that descriptively.

## What it produces

`data/processed/sme_awards.parquet` (and `.csv`) — one row per
(notice, supplier) award, with:

| field | meaning |
|---|---|
| source | `find_a_tender` or `contracts_finder` |
| buyer_name / buyer_region | contracting authority + region |
| cpv_code / cpv_description | sector classification |
| award_date / award_value / currency | the award |
| supplier_name | who won |
| ch_number / accounts_type / company_type / sic_codes | Companies House match |
| sme_flag | 1 = SME, 0 = large, null = unknown |
| sme_basis | how the flag was decided |

Plus `data/processed/cabinet_office_spend.parquet` — realised payments for
cross-checking notice data against actual spend.

## Data sources (all public)

1. **Find a Tender Service** — OCDS release-package API (high-value / OJEU-scale notices)
2. **Contracts Finder** — OCDS search API (lower-value + below-threshold notices)
3. **Companies House** — Public Data API (company size + financials → SME flag)
4. **Cabinet Office transparency** — spend-over-£25k CSVs (cross-check)

## Setup

```bash
pip install -r requirements.txt

# Companies House key (free): https://developer.company-information.service.gov.uk/
export COMPANIES_HOUSE_API_KEY="your-key-here"
```

## Run

```bash
# quick test — 2 pages/source, 50 suppliers, no CH calls
python run.py --from 2024-01-01 --to 2024-03-31 --max-pages 2 \
              --supplier-limit 50 --skip-companies-house

# full build
python run.py --from 2021-01-01 --to 2024-12-31
```

The run is **resumable**: award pages, supplier lookups, and company records
are cached in `data/cache.sqlite`, so re-running after an interruption skips
work already done.

## How SME classification works

A UK SME is an organisation with fewer than 250 employees (Companies Act size
bands also cap turnover ≤ £36m and balance sheet ≤ £18m for "medium").

The classifier decides in this order:

1. **Filed financials** — if turnover / employee figures are available, apply
   the size bands directly (most reliable). *Optional, see below.*
2. **Accounts-category proxy** — otherwise use the Companies House "last
   accounts" type: `micro-entity` / `small` / `medium` → SME;
   `full` / `group` → large. This is a defensible, widely-used proxy when
   detailed accounts aren't parsed.
3. **Unknown** — left null and reported separately, never silently counted.

Supplier → company linkage uses the `GB-COH` company number embedded in the
OCDS notice where present; otherwise a fuzzy name search against Companies
House (accepts a match only above a similarity threshold to avoid false links).

### Extending: detailed financials

`src/acquire/companies_house.py::fetch_financials` is a stub returning `{}`.
To pull real turnover/employee numbers: call
`/company/{n}/filing-history`, find the latest `accounts` filing, download its
iXBRL document via the Companies House Document API, and parse the
`Turnover` / `AverageNumberEmployeesDuringPeriod` tags. The classifier already
uses these automatically once populated.

## Layout

```
config.py                 settings, thresholds, date window, API endpoints
run.py                    CLI entry point
src/storage.py            SQLite cache + parquet/csv export
src/acquire/http.py       retrying HTTP session + rate limiter
src/acquire/find_a_tender.py
src/acquire/contracts_finder.py
src/acquire/companies_house.py   resolve + classify SME
src/acquire/cabinet_office.py
src/clean/ocds_parser.py  OCDS releases -> flat award rows
src/clean/standardise.py  name/CPV/value normalisation
src/pipeline.py           orchestrator: acquire -> enrich -> export
```

## Next steps (analysis stage)

Once the dataset is built, Dissertation 1's statistical analysis answers:
SME award **rate by sector** (CPV division), **by value band**, **by region**,
and **over time** — and quantifies the gap to the 33% target by spend vs by
count of awards (the two often diverge, which is itself a finding).
