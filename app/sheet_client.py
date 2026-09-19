"""
Google Sheets client — column mapping and case lookup.

Design notes:
- Column lookups go by header name, not hardcoded position, since the
  column layout is still a draft and expected to shift.
- find_case_row() loads the whole tab in one API call and searches in
  memory, rather than one API call per case, to avoid hitting Sheets
  API rate limits when processing a batch of cases in a single run.
"""

import os
from dotenv import load_dotenv
import gspread

load_dotenv()

SHEET_ID = os.getenv("GOOGLE_SHEETS_ID")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")


def get_client():
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
    return gc.open_by_key(SHEET_ID)


def get_header_map(worksheet):
    """
    Maps each header name (row 1) to its 1-indexed column number,
    matching gspread's own indexing convention for update calls.
    """
    headers = worksheet.row_values(1)
    return {name: idx + 1 for idx, name in enumerate(headers)}


def find_case_row(worksheet, case_id, case_id_column="Case ID"):
    """
    Searches the tab for a row matching case_id.

    Returns (row_number, row_dict) if found, else None.
    row_number is the real sheet row (header row already accounted
    for), so it can be passed straight into update_case_field().
    """
    header_map = get_header_map(worksheet)
    if case_id_column not in header_map:
        raise ValueError(
            f"'{case_id_column}' not found in headers: {list(header_map.keys())}"
        )

    case_id_col_idx = header_map[case_id_column] - 1  # 0-indexed for list access

    all_values = worksheet.get_all_values()
    headers = all_values[0]

    for row_number, row in enumerate(all_values[1:], start=2):
        if row[case_id_col_idx] == str(case_id):
            return row_number, dict(zip(headers, row))

    return None


def update_case_field(worksheet, row_number, field_name, value):
    """
    Updates a single field on a known row without touching the rest
    of the row.
    """
    header_map = get_header_map(worksheet)
    if field_name not in header_map:
        raise ValueError(
            f"'{field_name}' not found in headers: {list(header_map.keys())}"
        )

    worksheet.update_cell(row_number, header_map[field_name], value)


if __name__ == "__main__":
    sh = get_client()
    ongoing = sh.worksheet("Ongoing")

    # Quick manual check — swap in a real Case ID from your sandbox sheet
    test_case_id = input("Case ID to look up: ").strip()

    result = find_case_row(ongoing, test_case_id)
    if result is None:
        print(f"No row found for Case ID {test_case_id}")
    else:
        row_number, row_data = result
        print(f"Found at row {row_number}: {row_data}")

        field = input("Field to update (blank to skip): ").strip()
        if field:
            new_value = input(f"New value for '{field}': ").strip()
            update_case_field(ongoing, row_number, field, new_value)
            print("Updated.")