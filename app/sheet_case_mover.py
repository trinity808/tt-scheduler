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
"""

from gspread.utils import rowcol_to_a1

from app.sheet_client import find_case_row


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

    Returns the row number the case was written to in Finished.
    Raises ValueError if the case isn't found in Ongoing.
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

    target = move_case_to_finished(ongoing, finished, test_case_id, extra_fields=extras)
    print(f"Moved Case ID {test_case_id} to Finished (row {target}).")