"""Maintain syndicate names and aliases in Syndicate_Baseline.

Your sheet and the manager's reports rarely use the same name. A holding may be
recorded under its tenant ("AIRWAYS soe") while every report names the property
("Sir William Pickering Drive Limited Partnership").

**Fix that here, not in `Syndicate_Data`.** That tab is the system of record for
holdings and feeds the existing dashboards; renaming rows there risks breaking
them. `Syndicate_Baseline` exists to absorb naming variance: set
`canonical_name` to whatever the reports call it, and keep your own shorthand as
an alias. Both then resolve.

`syndicate_id` is never changed. It is an opaque key, and period rows in
`Syndicate_Periods` are filed against it -- changing it would orphan them.

    python scripts/manage_aliases.py --list
    python scripts/manage_aliases.py --add CENT-PENROSE "Centuria Penrose LP"
    python scripts/manage_aliases.py --rename CENT-AIRWAYSSOE "Sir William Pickering Drive Limited Partnership"

Nothing is written without --write.
"""

import argparse
import sys

sys.path.insert(0, ".")

from modules import sheets, storage  # noqa: E402


def _split(value):
    return [p.strip() for p in str(value or "").split(storage.ALIAS_SEPARATOR) if p.strip()]


def _join(values):
    return storage.ALIAS_SEPARATOR.join(sorted(set(v for v in values if v)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="show every syndicate and its aliases")
    parser.add_argument("--add", nargs=2, metavar=("SYNDICATE_ID", "ALIAS"),
                        help="add an alias to a syndicate")
    parser.add_argument("--rename", nargs=2, metavar=("SYNDICATE_ID", "NEW_CANONICAL"),
                        help="make NEW_CANONICAL the canonical name; the old one becomes an alias")
    parser.add_argument("--write", action="store_true", help="apply instead of preview")
    args = parser.parse_args()

    spreadsheet = sheets.get_client().open_by_key(sheets.PROPERTY_SHEET_ID)
    worksheet = spreadsheet.worksheet(storage.BASELINE_WORKSHEET)
    header = storage.read_header(worksheet)
    values = worksheet.get_all_values()

    def find(syndicate_id):
        for line, row in enumerate(values[1:], start=2):
            record = dict(zip(header, row))
            if str(record.get("syndicate_id", "")).strip() == syndicate_id:
                return line, record
        return None, None

    if args.list or not (args.add or args.rename):
        for row in values[1:]:
            record = dict(zip(header, row))
            if not record.get("syndicate_id"):
                continue
            print(f"  {record['syndicate_id']:<20} {str(record.get('canonical_name',''))[:44]:<46}")
            for alias in _split(record.get("aliases")):
                print(f"  {'':<20}   alias: {alias}")
        return

    target_id = (args.add or args.rename)[0]
    line, record = find(target_id)
    if record is None:
        print(f"No syndicate with id {target_id}. Run --list to see them.")
        return

    aliases = _split(record.get("aliases"))
    canonical = str(record.get("canonical_name", "")).strip()

    if args.add:
        alias = args.add[1].strip()
        if alias.lower() == canonical.lower() or alias in aliases:
            print(f"  {target_id} already answers to {alias!r}; nothing to do.")
            return
        aliases.append(alias)
        print(f"  {target_id}: add alias {alias!r}")

    if args.rename:
        new_canonical = args.rename[1].strip()
        if new_canonical == canonical:
            print(f"  {target_id} is already called {new_canonical!r}; nothing to do.")
            return
        # The old name keeps resolving -- it is still what your sheet calls it.
        aliases.append(canonical)
        aliases = [a for a in aliases if a.lower() != new_canonical.lower()]
        print(f"  {target_id}: canonical {canonical!r} -> {new_canonical!r}")
        print(f"  {target_id}: {canonical!r} kept as an alias")
        canonical = new_canonical

    print(f"  aliases now: {' | '.join(sorted(set(aliases))) or '(none)'}")

    if not args.write:
        print("\nDRY RUN. Re-run with --write to apply.")
        return

    worksheet.update_cell(line, header.index("canonical_name") + 1, canonical)
    worksheet.update_cell(line, header.index("aliases") + 1, _join(aliases))
    print("\nApplied.")


if __name__ == "__main__":
    main()
