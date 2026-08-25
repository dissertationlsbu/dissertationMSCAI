"""
One-off diagnostic — checks whether the raw OCDS files contain the free-text
fields needed for barrier analysis (insurance requirements, certifications,
turnover thresholds), before we build a full extractor around them.

Run once, read the output, then this file can be deleted — it's not part of
the pipeline.
"""

import gzip
import io
import json
from collections import Counter
from pathlib import Path

RAW = Path(__file__).resolve().parent / "data" / "raw"
CF_GZ = RAW / "united_kingdom_contracts_finder_releases_full.jsonl.gz"


def open_gz(p):
    return io.TextIOWrapper(gzip.open(p, "rb"), encoding="utf-8", errors="ignore")


def releases(o):
    if "records" in o:
        for r in o["records"]:
            rel = r.get("compiledRelease") or (r.get("releases") or [None])[-1]
            if rel:
                yield rel
    elif "releases" in o:
        yield from o["releases"]
    elif "compiledRelease" in o:
        yield o["compiledRelease"]
    elif "ocid" in o:
        yield o


def main():
    fields = Counter()
    n = 0
    sample_desc = None
    sample_elig = None

    with open_gz(CF_GZ) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
            for rel in releases(o):
                t = rel.get("tender") or {}
                n += 1
                for f in ["description", "eligibilityCriteria", "selectionCriteria",
                          "numberOfTenderers", "documents", "submissionMethod",
                          "minValue", "awardCriteria", "otherRequirements"]:
                    if t.get(f) not in (None, "", [], {}):
                        fields[f] += 1
                if not sample_desc and t.get("description"):
                    sample_desc = t["description"]
                if not sample_elig and t.get("eligibilityCriteria"):
                    sample_elig = t["eligibilityCriteria"]
                for pt in rel.get("parties") or []:
                    if "supplier" in (pt.get("roles") or []):
                        c = (pt.get("address") or {}).get("countryName")
                        if c:
                            fields["supplier_country"] += 1
            if n >= 20000:
                break

    print(f"scanned {n:,} tenders\n")
    for f, c in fields.most_common():
        print(f"  {f:<22} present in {c:>6,}  ({c / n:.1%})")
    print("\n--- sample description ---\n", str(sample_desc)[:600])
    print("\n--- sample eligibilityCriteria ---\n", str(sample_elig)[:600])


if __name__ == "__main__":
    main()