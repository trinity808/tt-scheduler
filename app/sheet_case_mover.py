"""
Google Sheets client — moving a case from Ongoing to Finished.

Ongoing and Finished don't share an identical column set, so this
isn't a literal copy: for each Finished column, the value comes from
the Ongoing row if that header name exists there too, otherwise from
extra_fields if supplied, otherwise it's left blank.

Matching by header-name overlap (rather than a hardcoded list of
"shared fields") means this keeps working correctly even if the
column layout shifts later, since both tabs are still drafts.

New rows go to the first completely empty row after the last row with
a Case ID, written to an explicit range. append_row() isn't used: it
relies on Sheets detecting "the table", and a blank row in the middle
of the data once shifted a whole row into the wrong columns. Rows that
hold something but have no Case ID (a stray note, say) are skipped
over, never overwritten.

A move can be interrupted between writing to Finished and deleting from
Ongoing, leaving the case in both tabs. Running it again must not add a
second Finished row, so the mover first checks whether the case is already
in Finished. If it is, with nothing that disagrees, it only fills the blank
cells it has a value for, leaves everything else alone, and finishes the
move by deleting from Ongoing. If the Finished row disagrees with what would
be written, or the case is in Finished more than once, it changes nothing
and raises, because guessing could lose data.
"""

import logging
from gspread.utils import rowcol_to_a1
from app.sheet_client import find_case_row

logger = logging.getLogger(__name__)

def build_finished_row(ongoing_row_dict, finished_headers, extra_fields=None):
    extra_fields = extra_fields or {}
    row = []
    for header in finished_headers:
        if header in ongoing_row_dict:
            row.append(ongoing_row_dict[header])
        elif header in extra_fields:
            row.append(extra_fields[header])
        else:
            row.append("")
    return row


def find_target_row(all_values, case_id_column="Case ID"):
    """
    The row a new case should be written to: the first completely
    empty row after the last row that has a Case ID. all_values is the
    tab's contents, header row first.
    """
    headers = all_values[0]
    if case_id_column not in headers:
        raise ValueError(f"'{case_id_column}' not found in the Finished tab's headers")
    case_id_idx = headers.index(case_id_column)

    last_case_row = 1  # the header row, if no cases yet
    for row_number, row in enumerate(all_values[1:], start=2):
        if case_id_idx < len(row) and row[case_id_idx].strip():
            last_case_row = row_number

    target_row = last_case_row + 1
    while target_row <= len(all_values) and any(
        cell.strip() for cell in all_values[target_row - 1]
    ):
        target_row += 1
    return target_row


def find_rows_in_finished(all_values, case_id, case_id_column="Case ID"):
    """
    The row numbers in the Finished tab (header row first) whose Case ID is
    case_id, ignoring surrounding spaces. Usually none; one means the case
    is already there.
    """
    headers = all_values[0]
    if case_id_column not in headers:
        raise ValueError(f"'{case_id_column}' not found in the Finished tab's headers")
    case_id_idx = headers.index(case_id_column)
    wanted = str(case_id).strip()
    return [
        row_number
        for row_number, row in enumerate(all_values[1:], start=2)
        if case_id_idx < len(row) and row[case_id_idx].strip() == wanted
    ]
 
 
def _complete_an_interrupted_move(
    finished_ws, finished_values, existing_rows, new_row, case_id, case_id_column
):
    """
    The case is already in Finished: an earlier move wrote it there and was
    interrupted before deleting it from Ongoing. Fills only the blank cells
    that have a value to write, and returns the existing row's number. Raises
    ValueError, having changed nothing, if the case is there more than once or
    the existing row has a different value in a column that would be written.
    """
    if len(existing_rows) > 1:
        raise ValueError(
            f"Case ID {case_id} appears {len(existing_rows)} times in Finished "
            f"(rows {', '.join(str(r) for r in existing_rows)}), so it can't be "
            "moved safely. Remove the extra rows first."
        )
 
    headers = finished_values[0]
    row_number = existing_rows[0]
    existing = finished_values[row_number - 1]
    existing = existing + [""] * (len(headers) - len(existing))
 
    blanks, conflicts = [], []
    for idx, (header, new_value) in enumerate(zip(headers, new_row)):
        new_text = str(new_value).strip()
        if header == case_id_column or not new_text:
            continue
        old_text = existing[idx].strip()
        if not old_text:
            blanks.append(idx)
        elif old_text != new_text:
            conflicts.append(header)
 
    if conflicts:
        raise ValueError(
            f"Case ID {case_id} is already in Finished (row {row_number}) with "
            f"different values for: {', '.join(conflicts)}. Nothing was changed. "
            "If the Finished row is right, delete the case's row from Ongoing by "
            "hand. Otherwise fix the Finished row first."
        )
 
    for idx in blanks:
        # update(), not update_cell(): update_cell() makes Google read the value as if it
        # were typed into the cell, so "11/12/2026 01:00 PM" becomes a date shown as
        # "11/12/2026 1:00 PM" and "484.00" becomes the number 484. update() stores the
        # text as given, as the write of a new row does.
        finished_ws.update(
            range_name=rowcol_to_a1(row_number, idx + 1),
            values=[[new_row[idx]]],
        )
 
    filled = f" Filled in: {', '.join(headers[i] for i in blanks)}." if blanks else ""
    logger.warning(
        "Case ID %s was already in Finished (row %s), so no row was added. "
        "Its move was completed by removing it from Ongoing.%s",
        case_id, row_number, filled,
    )
    return row_number
 
 
def move_case_to_finished(
    ongoing_ws, finished_ws, case_id, case_id_column="Case ID", extra_fields=None
):
    """
    Moves a case's row from Ongoing to Finished:
      1. Find the row in Ongoing.
      2. Build the corresponding Finished row.
      3. Write it into the Finished tab.
      4. Delete the original row from Ongoing.
 
    Writing happens before deleting, deliberately: if step 3 fails
    partway (network error, API issue), the row is still in Ongoing
    rather than lost from both tabs. If the write raises, the exception
    stops execution there and delete_rows() never runs, so this
    ordering doesn't need an explicit try/except to enforce it.
 
    If the case is already in Finished (an earlier move was interrupted
    after step 3), no second row is added: see the module docstring.
 
    Returns the row number the case is on in Finished.
    Raises ValueError if the case isn't found in Ongoing, or if it can't be
    moved safely because of what is already in Finished.
    """
    result = find_case_row(ongoing_ws, case_id, case_id_column)
    if result is None:
        raise ValueError(f"Case ID {case_id} not found in Ongoing tab")
 
    row_number, ongoing_row_dict = result
 
    finished_values = finished_ws.get_all_values()
    if not finished_values:
        raise ValueError("The Finished tab has no header row")
    finished_headers = finished_values[0]
    new_row = build_finished_row(ongoing_row_dict, finished_headers, extra_fields)
 
    existing_rows = find_rows_in_finished(finished_values, case_id)
    if existing_rows:
        existing_row = _complete_an_interrupted_move(
            finished_ws, finished_values, existing_rows, new_row, case_id, "Case ID"
        )
        ongoing_ws.delete_rows(row_number)
        return existing_row
 
    target_row = find_target_row(finished_values)
    if target_row > finished_ws.row_count:
        finished_ws.add_rows(target_row - finished_ws.row_count)
 
    start = rowcol_to_a1(target_row, 1)
    end = rowcol_to_a1(target_row, len(finished_headers))
    finished_ws.update(range_name=f"{start}:{end}", values=[new_row])
 
    ongoing_ws.delete_rows(row_number)
    return target_row


if __name__ == "__main__":
    from app.sheet_client import get_client

    sh = get_client()
    ongoing = sh.worksheet("Ongoing")
    finished = sh.worksheet("Finished")

    test_case_id = input("Case ID to move from Ongoing to Finished: ").strip()

    payment_status = input("Payment Status (blank to skip): ").strip()
    date_completed = input("Date Completed (blank to skip): ").strip()

    extras = {}
    if payment_status:
        extras["Payment Status"] = payment_status
    if date_completed:
        extras["Date Completed"] = date_completed

    try:
        target = move_case_to_finished(ongoing, finished, test_case_id, extra_fields=extras)
    except ValueError as error:
        raise SystemExit(f"Not moved: {error}")
    print(f"Moved Case ID {test_case_id} to Finished (row {target}).")