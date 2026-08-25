"""
Follow-up check: within ocid groups that look like "separate contractors",
is each supplier genuinely listed once per lot/notice-version (real), or
does the SAME supplier+value+notice_id repeat (a duplicate our current rule
is missing because of some other differing field, e.g. award_date)?

Compact, screenshot-friendly output.

Run from project root:
    python check_twice_pattern.py
"""

import pandas as pd

df = pd.read_csv("data/processed/sme_awards_clean.csv", low_memory=False)

dup_mask = df["ocid"].duplicated(keep=False)
dup = df[dup_mask]

grp = dup.groupby("ocid").agg(
    n_rows=("ocid", "size"),
    n_suppliers=("supplier_name", "nunique"),
    n_award_values=("award_value", "nunique"),
)
separate = grp[(grp["n_suppliers"] > 1) | (grp["n_award_values"] > 1)]
sep_rows = dup[dup["ocid"].isin(separate.index)]

# For each (ocid, supplier) pair inside a "separate contractors" group,
# count how many times that exact supplier repeats.
per_supplier = sep_rows.groupby(["ocid", "supplier_name"]).agg(
    times_repeated=("supplier_name", "size"),
    distinct_notice_ids=("notice_id", "nunique"),
    distinct_dates=("award_date", "nunique"),
    distinct_values=("award_value", "nunique"),
).reset_index()

repeated = per_supplier[per_supplier["times_repeated"] > 1]

# Key question: when a supplier repeats within one ocid, does notice_id ALSO
# repeat identically? If yes -> our is_duplicate rule should have caught it
# (unless award_date differs) -> check that specifically.
same_notice = repeated[repeated["distinct_notice_ids"] == 1]
same_notice_same_date = same_notice[same_notice["distinct_dates"] == 1]
same_notice_diff_date = same_notice[same_notice["distinct_dates"] > 1]

print("=" * 60)
print(" OCID 'SEPARATE CONTRACTOR' GROUPS — SUPPLIER REPEAT CHECK")
print("=" * 60)
print(f"ocid groups classed as separate contractors : {len(separate):,}")
print(f"rows in those groups                         : {len(sep_rows):,}")
print(f"(ocid, supplier) pairs where supplier repeats : {len(repeated):,}")
print("-" * 60)
print("Of those repeats:")
print(f"  same notice_id + same date  (=> TRUE DUP, missed)  : {len(same_notice_same_date):,}")
print(f"  same notice_id + diff date  (=> re-issued notice)  : {len(same_notice_diff_date):,}")
print(f"  different notice_id         (=> genuine re-listing): {len(repeated) - len(same_notice):,}")
print("=" * 60)

if len(same_notice_same_date):
    print("\n!! FOUND rows our current is_duplicate rule MISSED !!")
    ex = same_notice_same_date.iloc[0]
    print(f"example ocid: {ex['ocid']}  supplier: {ex['supplier_name']}")
else:
    print("\nNo missed duplicates found. Every repeat has a different")
    print("notice_id or date -> these are genuine separate line items,")
    print("not the same record copied twice.")
print("=" * 60)