"""
Writes the Reminders Sent tab: one row per reminder that went out.

This is how a second run, even the same day, knows not to send again (see
reminders_sheet.py, which reads it). So a row is written the moment its email
has been accepted, not at the end of the run. A crash halfway then leaves every
email that went out already logged.

Rows are written to an explicit range, found by counting the rows already in the
tab, and never with append_row(). Google Sheets decides where an appended row
goes by guessing where "the table" is, and once put a row in the wrong columns.
tests/fakes.py has no append_row for the same reason.

Columns are found by header name, as everywhere else, so someone reordering the
tab or adding a column of their own doesn't break it. Case ID and Reminder are
required. Recipient, Sent On and Sent At are filled in if the tab has them. The
Recipient is the provider's name, never the address.

A tab that doesn't exist is created, with the headers. A tab that exists but is
empty gets the headers. A tab with headers but no Case ID or Reminder column
raises ValueError, because rows written blind would be unreadable by the next run.
"""

from gspread.exceptions import WorksheetNotFound
from gspread.utils import rowcol_to_a1

from app.reminders_sheet import (
    CASE_ID,
    SENT_HEADERS,
    SENT_REMINDER,
    SENT_TAB,
    _find_columns,  # the same header matching the reader uses
)


RECIPIENT, SENT_ON, SENT_AT = SENT_HEADERS[2:]

ROWS_AT_START = 1000
ROWS_TO_ADD = 500


class SentLog:
    """An open Reminders Sent tab, ready to take the next row."""

    def __init__(self, worksheet, columns, next_row):
        self.worksheet = worksheet
        self.columns = columns    # header -> 0-based position
        self.next_row = next_row  # the sheet row the next record goes to

    @classmethod
    def open(cls, spreadsheet):
        """Opens the tab, creating it (or its header row) if needed."""
        try:
            worksheet = spreadsheet.worksheet(SENT_TAB)
        except WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=SENT_TAB, rows=ROWS_AT_START, cols=len(SENT_HEADERS)
            )

        rows = worksheet.get_all_values()
        if not rows:
            worksheet.update(
                range_name=f"{rowcol_to_a1(1, 1)}:{rowcol_to_a1(1, len(SENT_HEADERS))}",
                values=[list(SENT_HEADERS)],
            )
            rows = [list(SENT_HEADERS)]

        columns = _find_columns(rows[0], SENT_HEADERS)
        missing = [name for name in (CASE_ID, SENT_REMINDER) if name not in columns]
        if missing:
            names = ", ".join(f"'{name}'" for name in missing)
            raise ValueError(
                f"The {SENT_TAB} tab has no {names} column, so a reminder can't be "
                "logged as sent. Row 1 of the tab needs the headers Case ID and Reminder"
            )
        return cls(worksheet, columns, len(rows) + 1)

    def record(self, case_id, reminder_key, recipient, when):
        """
        Writes one row: which case, which reminder, to whom (a name), and when
        (`when` is a datetime in Arizona time). Moves on to the next row only once
        the write has gone through.
        """
        row = [""] * (max(self.columns.values()) + 1)
        row[self.columns[CASE_ID]] = str(case_id)
        row[self.columns[SENT_REMINDER]] = reminder_key
        for name, value in (
            (RECIPIENT, recipient),
            (SENT_ON, f"{when:%Y-%m-%d}"),
            (SENT_AT, f"{when:%H:%M:%S}"),
        ):
            if name in self.columns:
                row[self.columns[name]] = value

        if self.next_row > self.worksheet.row_count:
            self.worksheet.add_rows(ROWS_TO_ADD)
        self.worksheet.update(
            range_name=f"{rowcol_to_a1(self.next_row, 1)}:{rowcol_to_a1(self.next_row, len(row))}",
            values=[row],
        )
        self.next_row += 1