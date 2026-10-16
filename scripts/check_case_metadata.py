"""
Live check: runs the case-metadata logic through the real Sheets API.

Almost everything happens on a scratch tab named "MetadataCheck" in the
sandbox sheet, which is deleted at the end (taking its metadata with it).
The one exception is the last check, which is explained where it runs.

It covers what the in-memory tests can't: that the real API accepts the
requests, returns entries in the shape the client expects (including the
metadataId used for deleting), and keeps each entry with its row through
the things that happen to rows in practice.

Run from the project root:
    python -m scripts.check_case_metadata
"""

import gspread

from app.case_metadata import (
    CASE_TESTS_KEY,
    decode_case_tests,
    encode_case_tests,
    original_tests_for_row,
    read_all_case_tests,
    set_case_tests,
)
from app.sheet_client import get_client
from app.sheets_metadata_client import SheetsMetadataClient


TAB = "MetadataCheck"

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    line = f"  [{'OK' if ok else 'FAILED'}] {label}"
    if not ok and detail:
        line += f"  ->  {detail}"
    print(line)


def tests_on(client, row_number):
    entry = read_all_case_tests(client).by_row.get(row_number)
    return entry.tests if entry else None


def entries_on(client, row_number):
    return [e for e in client.search(CASE_TESTS_KEY) if e.row_number == row_number]


def all_entries_match_their_rows(client, ws):
    """Every stored entry's Case ID equals the Case ID actually in its row."""
    stored = read_all_case_tests(client)
    values = ws.get_all_values()
    for row_number in stored.by_row:
        entry, _ = original_tests_for_row(stored, row_number, values[row_number - 1][0])
        if entry is None:
            return False
    return True


def run_checks(ws, client):
    ws.update(
        range_name="A1:B4",
        values=[["Case ID", "Note"], ["CASE-A", ""], ["CASE-B", ""], ["CASE-C", ""]],
    )

    print("\n1. Attach three cases and read them all back")
    set_case_tests(client, 2, "CASE-A", ["96101-WISC-V", "90791"])
    set_case_tests(client, 3, "CASE-B", ["96130-AUTISM"])
    set_case_tests(client, 4, "CASE-C", ["96101-WAIS-IV"])
    stored = read_all_case_tests(client)
    check("entries found on rows 2, 3 and 4", sorted(stored.by_row) == [2, 3, 4],
          f"found {sorted(stored.by_row)}")
    check("row 2 holds CASE-A's tests", tests_on(client, 2) == ["96101-WISC-V", "90791"],
          f"got {tests_on(client, 2)}")
    mine = [
        m["developerMetadata"] for m in client.search_raw(CASE_TESTS_KEY)
        if m["developerMetadata"].get("location", {}).get("dimensionRange", {}).get("sheetId", 0) == ws.id
    ]
    check("every entry has a metadataId", bool(mine) and all("metadataId" in m for m in mine))
    check("every entry is DOCUMENT visibility",
          bool(mine) and all(m.get("visibility") == "DOCUMENT" for m in mine),
          f"got {[m.get('visibility') for m in mine]}")
    if mine:
        print(f"  (a raw entry, for reference: {mine[0]})")

    print("\n2. Replace one row's entry (this deletes the old one by its id)")
    set_case_tests(client, 3, "CASE-B", ["96130-AUTISM", "96101-WMS-IV"])
    check("exactly one entry on row 3", len(entries_on(client, 3)) == 1,
          f"found {len(entries_on(client, 3))}")
    check("it holds the new tests", tests_on(client, 3) == ["96130-AUTISM", "96101-WMS-IV"],
          f"got {tests_on(client, 3)}")
    check("rows 2 and 4 are untouched",
          tests_on(client, 2) == ["96101-WISC-V", "90791"] and tests_on(client, 4) == ["96101-WAIS-IV"])

    print("\n3. A row cleared by hand keeps its entry, and a new case replaces it")
    ws.update(range_name="A4:B4", values=[["", ""]])
    entry, problem = original_tests_for_row(read_all_case_tests(client), 4, "")
    check("the stale entry is still there, and the Case ID guard refuses it",
          entry is None and problem is not None, f"entry={entry}, problem={problem}")
    set_case_tests(client, 4, "CASE-NEW", ["90791"])
    ws.update(range_name="A4:B4", values=[["CASE-NEW", ""]])
    check("a new case on that row leaves exactly one entry", len(entries_on(client, 4)) == 1,
          f"found {len(entries_on(client, 4))}")
    check("and it is the new case's", tests_on(client, 4) == ["90791"], f"got {tests_on(client, 4)}")

    print("\n4. Insert a row above: entries move down with their rows")
    ws.insert_row(["CASE-INSERTED", ""], index=2)
    stored = read_all_case_tests(client)
    check("still three entries", len(stored.by_row) == 3, f"found {sorted(stored.by_row)}")
    check("none on the inserted row", 2 not in stored.by_row)
    check("every entry matches the Case ID in its row", all_entries_match_their_rows(client, ws))

    print("\n5. Sort the data by Case ID: entries follow their rows")
    ws.sort((1, "des"), range="A2:B5")
    stored = read_all_case_tests(client)
    values = ws.get_all_values()
    inserted_row = next(i for i, row in enumerate(values, start=1) if row[0] == "CASE-INSERTED")
    check("still three entries", len(stored.by_row) == 3, f"found {sorted(stored.by_row)}")
    check("none on the row that never had one", inserted_row not in stored.by_row)
    check("every entry matches the Case ID in its row", all_entries_match_their_rows(client, ws))

    print("\n6. Delete a row, as when a case moves to Finished: its entry goes with it")
    row_of_b = next(i for i, row in enumerate(ws.get_all_values(), start=1) if row[0] == "CASE-B")
    ws.delete_rows(row_of_b)
    stored = read_all_case_tests(client)
    check("two entries remain", len(stored.by_row) == 2, f"found {sorted(stored.by_row)}")
    check("none belongs to CASE-B", all(e.case_id != "CASE-B" for e in stored.by_row.values()))
    check("the rest still match their rows", all_entries_match_their_rows(client, ws))


def check_first_tab(sh):
    """
    A tab's sheetId is its identity, not its position. The tab with
    sheetId 0 is whichever one the spreadsheet was created with, and a
    scratch tab never gets that id, so the checks above can't cover it.
    This one tests the tab that has it, if there is one.

    It attaches hidden metadata to row 1 (a start index of 0) and to the
    last row of the tab, reads both back, and deletes them. No cell is
    touched, and only entries made here (recognized by their Case ID) are
    deleted, never anything that belongs to a real case.
    """
    print("\n7. The tab with sheetId 0, if this spreadsheet has one")
    zero_tab = next((w for w in sh.worksheets() if w.id == 0), None)
    if zero_tab is None:
        print("  No tab here has sheetId 0, so there is nothing to check.")
        return

    print(f"  (the tab with sheetId 0 is '{zero_tab.title}')")
    client = SheetsMetadataClient(zero_tab)
    last_row = zero_tab.row_count
    marker = "ZERO-CHECK"
    try:
        for row_number in (1, last_row):
            client.create(row_number, CASE_TESTS_KEY, encode_case_tests(marker, ["90791"]))

        found = {}
        for entry in client.search(CASE_TESTS_KEY):
            try:
                if decode_case_tests(entry.value).case_id == marker:
                    found[entry.row_number] = entry
            except ValueError:
                pass
        check("the entry on row 1 is found (start index 0)", 1 in found, f"found rows {sorted(found)}")
        check(f"the entry on the last row ({last_row}) is found", last_row in found,
              f"found rows {sorted(found)}")

        for match in client.search_raw(CASE_TESTS_KEY):
            metadata = match["developerMetadata"]
            if marker in metadata.get("metadataValue", ""):
                print(f"  (raw location, for reference: {metadata.get('location')})")
    finally:
        for entry in client.search(CASE_TESTS_KEY):
            try:
                if decode_case_tests(entry.value).case_id == marker:
                    client.delete(entry.metadata_id)
            except ValueError:
                pass
    leftover = [
        e for e in client.search(CASE_TESTS_KEY)
        if marker in e.value
    ]
    check("both check entries were cleaned up", leftover == [], f"left {leftover}")


def main():
    sh = get_client()
    try:
        sh.del_worksheet(sh.worksheet(TAB))  # left over from an earlier failed run
    except gspread.WorksheetNotFound:
        pass

    ws = sh.add_worksheet(title=TAB, rows=20, cols=3)
    try:
        run_checks(ws, SheetsMetadataClient(ws))
        check_first_tab(sh)
    finally:
        sh.del_worksheet(ws)
        print("\nScratch tab deleted.")

    print(f"{sum(results)} of {len(results)} checks passed.")


if __name__ == "__main__":
    main()