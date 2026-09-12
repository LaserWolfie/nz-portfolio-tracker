"""One-off: merge Syndicate_Baseline rows that name the same property.

The same property can appear under several names because it is held by
different family companies, or was bought on the secondary market at a
different time and cost. Those are facts about the HOLDING, not the property.
Property facts -- LVR, WALE, cap rate, valuation, occupancy, ICR -- belong to
one syndicate row, so the rows are merged and every name becomes an alias.

Where the two rows disagreed on a property metric, the disagreement was stale
data on one of them, not two different buildings.

The surviving row keeps its syndicate_id, gains the other's names as aliases,
and takes the union of owner entities. The surplus row is deleted.

Run with no arguments to preview; pass --write to apply.

    .venvapp/Scripts/python.exe scripts/merge_baseline.py
    .venvapp/Scripts/python.exe scripts/merge_baseline.py --write
"""

import argparse
import sys

sys.path.insert(0, ".")

from modules import sheets, storage  # noqa: E402

#: (surviving syndicate_id, absorbed syndicate_id, why)
MERGES = [
    ("CENT-BUILDINGB", "CENT-GRAHAMB",
     'Graham St "B" is Building B Graham Street held by Group Reality'),
    ("CENT-WILLIAMSSTRE", "CENT-WILLIAMST",
     "Cedenco is the Williams Street Nominees property; LVR 0.31 and 30.69 are "
     "the same figure in different units"),
    ("ESKI-DAYCARE", "ESKI-DAYCARE2",
     "Same childcare fund, two holdings at different cost bases"),
    ("SGB", "CENT-STGEORGE",
     "St George Group is Augusta St Georges Bay Road; SGB survives because the "
     "extracted period row is already filed against it"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    spreadsheet = sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
    worksheet = spreadsheet.worksheet(storage.BASELINE_WORKSHEET)
    header = storage.read_header(worksheet)
    rows = worksheet.get_all_values()

    def find(syndicate_id):
        for index, row in enumerate(rows[1:], start=2):
            record = dict(zip(header, row))
            if str(record.get("syndicate_id", "")).strip() == syndicate_id:
                return index, record
        return None, None

    planned = []
    for keep_id, drop_id, reason in MERGES:
        keep_line, keep = find(keep_id)
        drop_line, drop = find(drop_id)
        if keep is None or drop is None:
            missing = keep_id if keep is None else drop_id
            print(f"  SKIP    {keep_id} <- {drop_id}: {missing} not found "
                  "(already merged?)")
            continue

        def split(value):
            return [p.strip() for p in str(value or "").split(storage.ALIAS_SEPARATOR)
                    if p.strip()]

        aliases = set(split(keep.get("aliases")))
        aliases |= set(split(drop.get("aliases")))
        aliases.add(str(drop.get("canonical_name", "")).strip())
        aliases.discard(str(keep.get("canonical_name", "")).strip())
        aliases.discard("")

        owners = sorted(set(split(keep.get("owner_entity")))
                        | set(split(drop.get("owner_entity"))))

        note = (str(keep.get("notes", "")).strip() + " " if keep.get("notes") else "")
        note += f"Merged {drop_id}: {reason}."

        planned.append({
            "keep_line": keep_line,
            "drop_line": drop_line,
            "keep_id": keep_id,
            "drop_id": drop_id,
            "canonical": keep.get("canonical_name"),
            "absorbed": drop.get("canonical_name"),
            "aliases": storage.ALIAS_SEPARATOR.join(sorted(aliases)),
            "owner_entity": storage.ALIAS_SEPARATOR.join(owners),
            "notes": note.strip(),
        })

    for p in planned:
        print(f"  MERGE   {p['keep_id']:<18} <- {p['drop_id']}")
        print(f"            keep:    {p['canonical']}")
        print(f"            absorb:  {p['absorbed']}  (row {p['drop_line']} deleted)")
        print(f"            owners:  {p['owner_entity'].replace(storage.ALIAS_SEPARATOR, ', ')}")
        print(f"            aliases: {p['aliases'].replace(storage.ALIAS_SEPARATOR, ' | ')}")

    if not args.write:
        print(f"\nDRY RUN. {len(planned)} merge(s) would be applied. Re-run with --write.")
        return

    # Update survivors first, then delete absorbed rows from the bottom up so
    # earlier deletions cannot shift the line numbers of later ones.
    for p in planned:
        for column in ("aliases", "owner_entity", "notes"):
            worksheet.update_cell(p["keep_line"], header.index(column) + 1, p[column])

    for p in sorted(planned, key=lambda x: -x["drop_line"]):
        worksheet.delete_rows(p["drop_line"])

    print(f"\nApplied {len(planned)} merge(s).")


if __name__ == "__main__":
    main()
