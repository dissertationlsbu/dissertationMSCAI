"""
Storage layer.

A small SQLite cache makes the whole pipeline resumable: if a long Companies
House run is interrupted, re-running skips suppliers already resolved. Three
tables:

  awards      one row per (notice, supplier) award line from FTS / CF
  companies   one row per Companies House company we have resolved
  raw_pages   bookkeeping so re-runs don't re-download the same API pages

The analytical export (parquet + csv) is built by joining awards to companies.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import pandas as pd

import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS awards (
    award_uid        TEXT PRIMARY KEY,   -- ocid + supplier index, de-duplicated
    source           TEXT,               -- 'find_a_tender' | 'contracts_finder'
    ocid             TEXT,
    notice_id        TEXT,
    title            TEXT,
    buyer_name       TEXT,
    buyer_region     TEXT,
    cpv_code         TEXT,
    cpv_description  TEXT,
    award_date       TEXT,
    award_value      REAL,
    currency         TEXT,
    supplier_name    TEXT,
    supplier_scheme  TEXT,               -- e.g. GB-COH
    supplier_id      TEXT,               -- company number if given in OCDS
    tender_value     REAL,               -- procurement-level estimated value
    procurement_method         TEXT,     -- open / selective / limited
    procurement_method_details TEXT,     -- e.g. "Call-off from a framework agreement"
    main_category    TEXT,               -- goods / services / works
    lot_count        INTEGER,            -- number of lots in the procurement
    supplier_count   INTEGER,            -- suppliers on this award (consortia/frameworks)
    raw              TEXT                -- original award JSON (audit)
);

CREATE TABLE IF NOT EXISTS companies (
    company_number      TEXT PRIMARY KEY,
    matched_name        TEXT,
    company_status      TEXT,
    company_type        TEXT,
    date_of_creation    TEXT,
    sic_codes           TEXT,            -- comma separated
    accounts_type       TEXT,           -- last accounts category
    turnover            REAL,           -- if extracted from filings (often NULL)
    employees           INTEGER,        -- if extracted (often NULL)
    sme_flag            INTEGER,        -- 1 SME, 0 large, NULL unknown
    sme_basis           TEXT,           -- how sme_flag was decided
    raw                 TEXT
);

CREATE TABLE IF NOT EXISTS supplier_lookup (
    supplier_key     TEXT PRIMARY KEY,   -- normalised supplier name
    company_number   TEXT,               -- resolved CH number (NULL if no match)
    resolved         INTEGER DEFAULT 0   -- 1 once we've attempted resolution
);

CREATE TABLE IF NOT EXISTS raw_pages (
    page_key   TEXT PRIMARY KEY,         -- source + cursor/date window
    fetched_at TEXT
);
"""


# Columns added after the first release; auto-applied to existing caches so
# you don't have to delete and rebuild (preserves companies + supplier_lookup).
_AWARDS_MIGRATIONS = {
    "tender_value": "REAL",
    "procurement_method": "TEXT",
    "procurement_method_details": "TEXT",
    "main_category": "TEXT",
    "lot_count": "INTEGER",
    "supplier_count": "INTEGER",
}


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(awards)")}
    for col, typ in _AWARDS_MIGRATIONS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE awards ADD COLUMN {col} {typ}")


@contextmanager
def connect(db_path: Path = config.DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_awards(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    cols = [
        "award_uid", "source", "ocid", "notice_id", "title", "buyer_name",
        "buyer_region", "cpv_code", "cpv_description", "award_date",
        "award_value", "currency", "supplier_name", "supplier_scheme",
        "supplier_id", "tender_value", "procurement_method",
        "procurement_method_details", "main_category", "lot_count",
        "supplier_count", "raw",
    ]
    placeholders = ",".join("?" for _ in cols)
    sql = (
        f"INSERT OR REPLACE INTO awards ({','.join(cols)}) "
        f"VALUES ({placeholders})"
    )
    n = 0
    for r in rows:
        conn.execute(sql, [r.get(c) for c in cols])
        n += 1
    conn.commit()
    return n


def upsert_company(conn: sqlite3.Connection, company: dict) -> None:
    cols = [
        "company_number", "matched_name", "company_status", "company_type",
        "date_of_creation", "sic_codes", "accounts_type", "turnover",
        "employees", "sme_flag", "sme_basis", "raw",
    ]
    placeholders = ",".join("?" for _ in cols)
    conn.execute(
        f"INSERT OR REPLACE INTO companies ({','.join(cols)}) "
        f"VALUES ({placeholders})",
        [company.get(c) for c in cols],
    )
    conn.commit()


def set_supplier_lookup(conn, supplier_key: str, company_number: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO supplier_lookup "
        "(supplier_key, company_number, resolved) VALUES (?, ?, 1)",
        (supplier_key, company_number),
    )
    conn.commit()


def get_resolved_supplier(conn, supplier_key: str) -> str | None | bool:
    """Return company_number if resolved, None if resolved-but-no-match,
    or False if not yet attempted."""
    row = conn.execute(
        "SELECT company_number, resolved FROM supplier_lookup WHERE supplier_key=?",
        (supplier_key,),
    ).fetchone()
    if row is None:
        return False
    return row["company_number"]


def company_exists(conn, company_number: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM companies WHERE company_number=?", (company_number,)
    ).fetchone() is not None


def page_seen(conn, page_key: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM raw_pages WHERE page_key=?", (page_key,)
    ).fetchone() is not None


def mark_page(conn, page_key: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO raw_pages (page_key, fetched_at) "
        "VALUES (?, datetime('now'))",
        (page_key,),
    )
    conn.commit()


def save_raw_json(name: str, payload: dict | list) -> None:
    """Persist a raw API payload for the audit trail / reproducibility."""
    path = config.RAW_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=0))


def export_dataset(conn: sqlite3.Connection) -> pd.DataFrame:
    """Join awards to companies and write the analytical dataset."""
    df = pd.read_sql_query(
        """
        SELECT a.*, c.company_number AS ch_number, c.accounts_type,
               c.company_type, c.company_status, c.date_of_creation,
               c.sic_codes, c.turnover, c.employees, c.sme_flag, c.sme_basis
        FROM awards a
        LEFT JOIN supplier_lookup s
               ON s.supplier_key = a.supplier_name
        LEFT JOIN companies c
               ON c.company_number = COALESCE(a.supplier_id, s.company_number)
        """,
        conn,
    )
    parquet_path = config.OUT_DIR / "sme_awards.parquet"
    csv_path = config.OUT_DIR / "sme_awards.csv"
    try:
        df.to_parquet(parquet_path, index=False)
    except Exception:  # pyarrow missing -> csv only
        pass
    df.to_csv(csv_path, index=False)
    return df