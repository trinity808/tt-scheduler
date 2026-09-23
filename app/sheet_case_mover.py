"""
Google Sheets client — moving a case from Ongoing to Finished.

Ongoing and Finished don't share an identical column set, so this
isn't a literal copy: for each Finished column, the value comes from
the Ongoing row if that header name exists there too, otherwise from
extra_fields if supplied, otherwise it's left blank.

Matching by header-name overlap (rather than a hardcoded list of
"shared fields") means this keeps working correctly even if the
column layout shifts later, since both tabs are still drafts.
"""

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


def move_case_to_finished(
    ongoing_ws, finished_ws, case_id, case_id_column="Case ID", extra_fields=None
):
    """
    Moves a case's row from Ongoing to Finished:
      1. Find the row in Ongoing.
      2. Build the corresponding Finished row.
      3. Append it to Finished.
      4. Delete the original row from Ongoing.

    Append happens before delete, deliberately — if step 3 fails
    partway (network error, API issue), the row ends up duplicated
    in both tabs rather than lost from both. A duplicate is a
    visible, fixable problem; a lost row isn't. If append_row()
    raises, the exception stops execution there and delete_rows()
    never runs, so this ordering doesn't need an explicit try/except
    to enforce it.

    Raises ValueError if the case isn't found in Ongoing.
    """
    result = find_case_row(ongoing_ws, case_id, case_id_column)
    if result is None:
        raise ValueError(f"Case ID {case_id} not found in Ongoing tab")

    row_number, ongoing_row_dict = result

    finished_headers = finished_ws.row_values(1)
    new_row = build_finished_row(ongoing_row_dict, finished_headers, extra_fields)

    finished_ws.append_row(new_row)
    ongoing_ws.delete_rows(row_number)


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

    move_case_to_finished(ongoing, finished, test_case_id, extra_fields=extras)
    print(f"Moved Case ID {test_case_id} to Finished.")