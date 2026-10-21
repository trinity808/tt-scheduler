"""
Maps extracted PDF data (from pdf_extractor.py) to a row for the
Google Sheets "Ongoing" tab.

Fields not extractable from the PDF (Psychometrist, Provider) are
left blank for manual entry via OA's daily comments. Comments itself
starts blank for the same reason. Doctor/doctor_assigned is
deliberately excluded, per earlier decision.

Date & Time is joined as a single line here, unlike
schedule_generator.py's build_date_time_text(), which uses a
line-break join meant for a printed Word table cell — not
appropriate for a spreadsheet cell.

Matches the sheet's actual column headers by name (not fixed
position), same approach as sheet_case_mover.py's
build_finished_row(), so this keeps working correctly if the column
order changes later.

If the case's ID is already present in the tab, the extraction-
derived fields are updated in place rather than appending a
duplicate row. Manual-entry fields (Psychometrist, Provider,
Comments) are deliberately left untouched on update — a re-
extraction has nothing meaningful to put there, and overwriting them
would wipe out anything already entered for that case.
"""

from datetime import datetime, timezone

from gspread.utils import rowcol_to_a1

from app.case_metadata import set_case_tests
from app.pdf_extractor import extract_schedule_data
from app.sheet_client import update_case_field


# Fields OA enters manually — never overwritten by a re-extraction,
# since the newly extracted data has nothing real to put here.
MANUAL_ENTRY_FIELDS = {"Psychometrist", "Provider", "Comments"}

# Light yellow — flags a row for attention without reading as an
# error/danger state the way red would.
DUPLICATE_HIGHLIGHT_COLOR = {"red": 1.0, "green": 0.95, "blue": 0.6}


def build_date_time_text(extracted_data):
    schedule_date = extracted_data.get("schedule_date", "")
    schedule_time = extracted_data.get("schedule_time", "")
    return f"{schedule_date} {schedule_time}".strip()


def build_ongoing_row(extracted_data, ongoing_headers):
    """
    Returns a list of values in the same order as ongoing_headers,
    ready to append as a new row via worksheet.append_row().
    """
    field_map = {
        "Case ID": extracted_data.get("case_id", ""),
        "Patient Name": extracted_data.get("patient_name", ""),
        "Phone": extracted_data.get("phone", ""),
        "DOB": extracted_data.get("dob", ""),
        "Adult": extracted_data.get("adult", ""),
        "Child": extracted_data.get("child", ""),
        "Date & Time": build_date_time_text(extracted_data),
        "Services Requested": extracted_data.get("services_requested", ""),
        "Allegations": extracted_data.get("allegations", ""),
        "Psychometrist": "",
        "Provider": "",
        "Price": extracted_data.get("total", ""),
        "Comments": "",
    }

    return [field_map.get(header, "") for header in ongoing_headers]


def flag_duplicate_row(worksheet, row_number, num_columns):
    """
    Highlights the row and adds a note on its first cell, so a
    human monitoring the sheet can see a duplicate case extraction
    was received and merged, rather than it happening silently.

    Overwrites any previous flag rather than accumulating a history
    — this reflects the latest occurrence, not a full log. Nothing
    currently clears the highlight once set; that's a manual step
    for whoever reviews it, not something built here.
    """
    start = rowcol_to_a1(row_number, 1)
    end = rowcol_to_a1(row_number, num_columns)
    worksheet.format(f"{start}:{end}", {"backgroundColor": DUPLICATE_HIGHLIGHT_COLOR})

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    worksheet.update_note(
        start,
        f"Duplicate entry received — extraction re-run on {timestamp}. "
        "Extraction-derived fields were refreshed; Psychometrist/"
        "Provider/Comments were left untouched."
    )


class MetadataWriteError(Exception):
    """
    The case's row was written, but its original tests couldn't be stored.
    The row is in the sheet, so nothing is lost; a caller that doesn't mark
    the PDF as processed will retry, and a retry just updates the row again
    and stores the tests.
    """

    def __init__(self, case_id, action, row_number, cause):
        super().__init__(
            f"Case {case_id} was {action} at row {row_number}, but its "
            f"original tests couldn't be stored: {cause}"
        )
        self.case_id = case_id
        self.action = action
        self.row_number = row_number


def _store_original_tests(metadata, extracted_data, case_id, action, row_number):
    codes = [service["code"] for service in extracted_data.get("services") or []]
    try:
        set_case_tests(metadata, row_number, case_id, codes)
    except Exception as error:
        raise MetadataWriteError(case_id, action, row_number, error) from error


def add_or_update_case(worksheet, extracted_data, metadata=None):
    """
    Adds extracted_data as a new row if its Case ID isn't already in
    the tab, or updates the extraction-derived fields of the existing
    row in place if it is, flagging the row so the duplicate is
    visible. Manual-entry fields are left untouched either way.

    When a metadata client is given, the case's original tests (the codes
    from the PDF) are also stored on its row as hidden metadata, for later
    steps that rebuild a case's tests from that original. This happens for
    a new case and again for a repeated one, since a repeat refreshes the
    Services Requested cell from the PDF and the stored original must match.

    New cases go into the first row after the last row that has a
    Case ID, not appended after the last non-empty row. That way a
    template pre-filled in the Comments column of empty rows doesn't
    push new cases further down, and the template is kept on the row
    the case lands in. A gap left by a hand-cleared row stays a gap
    rather than pulling new cases out of order.

    If `metadata` (a client for the hidden per-row metadata, see
    case_metadata.py) is given, the case's original tests are stored on
    its row. A blank Case ID raises ValueError before anything is
    written, since the stored entry couldn't be checked against its row.
    If storing fails after the row is written, MetadataWriteError is
    raised; the row stays.

    Returns a tuple: ("added" | "updated", row_number).
    """
    case_id = str(extracted_data.get("case_id", "")).strip()
    if metadata is not None and not case_id:
        raise ValueError("A Case ID is needed to store a case's original tests")

    all_values = worksheet.get_all_values()
    headers = all_values[0]
    case_id_idx = headers.index("Case ID")

    existing_row_number = None
    last_case_row = 1  # header row, if no cases exist yet

    for row_number, row in enumerate(all_values[1:], start=2):
        row_case_id = row[case_id_idx].strip() if case_id_idx < len(row) else ""
        if not row_case_id:
            continue
        last_case_row = row_number
        if row_case_id == case_id:
            existing_row_number = row_number

    new_values = build_ongoing_row(extracted_data, headers)

    if existing_row_number is not None:
        for header, value in zip(headers, new_values):
            if header in MANUAL_ENTRY_FIELDS:
                continue
            update_case_field(worksheet, existing_row_number, header, value)
        flag_duplicate_row(worksheet, existing_row_number, len(headers))
        if metadata is not None:
            _store_original_tests(
                metadata, extracted_data, case_id, "updated", existing_row_number
            )
        return "updated", existing_row_number

    target_row = last_case_row + 1

    # Always write to an explicit range. append_row() relies on Sheets
    # detecting "the table", which gets confused by gaps and can shift
    # the whole row into the wrong columns.
    if target_row > worksheet.row_count:
        worksheet.add_rows(target_row - worksheet.row_count)

    # The target row may already hold something (e.g. a pre-filled
    # template). Keep any existing manual-entry values; fill the rest.
    if target_row <= len(all_values):
        existing = all_values[target_row - 1]
    else:
        existing = []
    existing = existing + [""] * (len(headers) - len(existing))

    merged = []
    for header, new_value, old_value in zip(headers, new_values, existing):
        if header in MANUAL_ENTRY_FIELDS and old_value.strip():
            merged.append(old_value)
        else:
            merged.append(new_value)

    start = rowcol_to_a1(target_row, 1)
    end = rowcol_to_a1(target_row, len(headers))
    worksheet.update(range_name=f"{start}:{end}", values=[merged])
    if metadata is not None:
        _store_original_tests(metadata, extracted_data, case_id, "added", target_row)
    return "added", target_row


if __name__ == "__main__":
    from app.sheet_client import get_client
    from app.sheets_metadata_client import SheetsMetadataClient

    pdf_path = input("Path to a test PDF (e.g. testing pdfs/Test1-may4_2026.pdf): ").strip()
    extracted = extract_schedule_data(pdf_path)

    sh = get_client()
    ongoing = sh.worksheet("Ongoing")
    headers = ongoing.row_values(1)

    row = build_ongoing_row(extracted, headers)

    print("\n--- Mapped row (in sheet column order) ---")
    for header, value in zip(headers, row):
        print(f"{header}: {value}")

    confirm = input("\nAdd or update this case in the Ongoing tab? (y/n): ").strip().lower()
    if confirm == "y":
        try:
            action, row_number = add_or_update_case(
                ongoing, extracted, metadata=SheetsMetadataClient(ongoing)
            )
        except MetadataWriteError as error:
            print(f"\nThe row was written, but: {error}")
            print("Running it again is safe: it will update the row and try again.")
            raise SystemExit(1)
        if action == "added":
            print(f"Added at row {row_number}.")
        else:
            print(
                f"Case already existed at row {row_number} — extraction-derived "
                "fields updated, Psychometrist/Provider/Comments left untouched."
            )
    else:
        print("No changes made.")