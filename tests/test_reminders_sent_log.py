"""
Tests for app/reminders_sent_log.py: opening the Reminders Sent tab (creating it if it is
missing) and writing one row per reminder sent.

The sheet is the shared in-memory FakeWorksheet. It has no append_row(), as the real
Sheets once put an appended row in the wrong columns, so these tests also show the log
never uses it. The last tests write rows and read them back with the reader the next run
uses (app/reminders_sheet.py), so the two sides can't drift apart.

Run from the project root:
    python -m pytest tests/test_reminders_sent_log.py -v
"""

from datetime import datetime

import pytest
from gspread.exceptions import WorksheetNotFound

from app.reminder_rules import ARIZONA
from app.reminders_sent_log import ROWS_AT_START, ROWS_TO_ADD, SentLog
from app.reminders_sheet import FINISHED_TAB, ONGOING_TAB, SENT_HEADERS, SENT_TAB, read_reminder_data
from tests.fakes import FakeSpreadsheet, FakeWorksheet


WHEN = datetime(2026, 5, 11, 8, 30, 5, tzinfo=ARIZONA)


class Book:
    """A spreadsheet that can also add a tab, as a real one can."""

    def __init__(self, tabs=None):
        self.tabs = dict(tabs or {})
        self.added = []

    def worksheet(self, name):
        if name not in self.tabs:
            raise WorksheetNotFound(name)
        return self.tabs[name]

    def add_worksheet(self, title, rows, cols):
        self.added.append((title, rows, cols))
        self.tabs[title] = FakeWorksheet([[""] * cols], row_count=rows)  # a new tab is blank
        return self.tabs[title]


def log_tab(*rows, row_count=1000):
    return FakeWorksheet([list(SENT_HEADERS)] + [list(r) for r in rows], row_count=row_count)


def table(book):
    return book.tabs[SENT_TAB].get_all_values()


# --- Opening the tab --------------------------------------------------------------


def test_a_missing_tab_is_created_with_the_headers():
    book = Book()
    SentLog.open(book)
    assert book.added == [(SENT_TAB, ROWS_AT_START, len(SENT_HEADERS))]
    assert table(book) == [SENT_HEADERS]


def test_an_existing_empty_tab_gets_the_headers_and_is_not_replaced():
    book = Book({SENT_TAB: FakeWorksheet([[""] * 5])})
    SentLog.open(book)
    assert book.added == []
    assert table(book) == [SENT_HEADERS]


def test_an_existing_tab_is_used_as_it_is():
    book = Book({SENT_TAB: log_tab(["111", "DAY_5", "Dr. Doe", "2026-05-11", "08:30:05"])})
    log = SentLog.open(book)
    assert book.added == [] and book.tabs[SENT_TAB].writes == []
    assert log.next_row == 3


def test_a_tab_without_a_case_id_or_reminder_column_is_refused_before_anything_is_written():
    for headers in (["Reminder", "Sent On"], ["Case ID", "Sent On"]):
        tab = FakeWorksheet([headers, ["x", "y"]])
        with pytest.raises(ValueError, match="Row 1 of the tab needs the headers Case ID and Reminder"):
            SentLog.open(Book({SENT_TAB: tab}))
        assert tab.writes == []


def test_the_error_names_the_missing_column():
    with pytest.raises(ValueError, match="no 'Reminder' column"):
        SentLog.open(Book({SENT_TAB: FakeWorksheet([["Case ID"], ["1"]])}))


# --- Writing rows -----------------------------------------------------------------


def test_a_row_has_the_case_the_reminder_the_provider_and_the_arizona_date_and_time():
    book = Book()
    SentLog.open(book).record("15419900", "DAY_5", "Dr. Doe", WHEN)
    assert table(book)[1] == ["15419900", "DAY_5", "Dr. Doe", "2026-05-11", "08:30:05"]


def test_the_case_id_is_written_as_text():
    book = Book()
    SentLog.open(book).record(15419900, "DAY_5", "Dr. Doe", WHEN)
    assert table(book)[1][0] == "15419900"


def test_rows_go_one_after_another_below_what_is_already_there():
    book = Book({SENT_TAB: log_tab(["1", "DAY_1", "A", "d", "t"], ["2", "DAY_1", "B", "d", "t"])})
    log = SentLog.open(book)
    log.record("111", "DAY_5", "Dr. Doe", WHEN)
    log.record("222", "DAY_8", "Dr. Smith", WHEN)
    rows = table(book)
    assert [r[0] for r in rows] == ["Case ID", "1", "2", "111", "222"]
    assert rows[1][0] == "1" and rows[2][0] == "2"  # nothing above was touched


def test_the_first_row_of_a_new_tab_is_row_two():
    book = Book()
    log = SentLog.open(book)
    assert log.next_row == 2
    log.record("111", "DAY_5", "Dr. Doe", WHEN)
    assert log.next_row == 3


def test_rows_are_written_with_update_to_an_explicit_range_never_append_row():
    book = Book({SENT_TAB: log_tab(["1", "DAY_1", "A", "d", "t"])})
    tab = book.tabs[SENT_TAB]
    assert not hasattr(tab, "append_row")
    SentLog.open(book).record("111", "DAY_5", "Dr. Doe", WHEN)
    assert [w[:2] for w in tab.writes] == [(3, 1), (3, 2), (3, 3), (3, 4), (3, 5)]


def test_the_tab_grows_when_the_rows_run_out():
    book = Book({SENT_TAB: log_tab(["1", "DAY_1", "A", "d", "t"], row_count=3)})
    tab = book.tabs[SENT_TAB]
    log = SentLog.open(book)
    log.record("111", "DAY_5", "Dr. Doe", WHEN)   # row 3: fits
    assert tab.row_count == 3
    log.record("222", "DAY_5", "Dr. Smith", WHEN)  # row 4: needs more rows first
    assert tab.row_count == 3 + ROWS_TO_ADD
    assert table(book)[-1][0] == "222"


def test_columns_are_found_by_header_so_a_reordered_tab_with_extra_columns_works():
    tab = FakeWorksheet([["Sent At", "Note", "reminder", " CASE  ID ", "Recipient", "Sent On"]])
    book = Book({SENT_TAB: tab})
    SentLog.open(book).record("111", "DAY_5", "Dr. Doe", WHEN)
    assert table(book)[1] == ["08:30:05", "", "DAY_5", "111", "Dr. Doe", "2026-05-11"]


def test_a_tab_with_only_the_two_required_columns_still_works():
    book = Book({SENT_TAB: FakeWorksheet([["Case ID", "Reminder"]])})
    SentLog.open(book).record("111", "DAY_5", "Dr. Doe", WHEN)
    assert table(book)[1] == ["111", "DAY_5"]


def test_a_failed_write_does_not_use_up_the_row():
    class Flaky(FakeWorksheet):
        fail = True

        def update(self, range_name, values):
            if self.fail:
                self.fail = False
                raise RuntimeError("the network dropped")
            super().update(range_name, values)

    book = Book({SENT_TAB: Flaky([list(SENT_HEADERS)])})
    log = SentLog.open(book)
    with pytest.raises(RuntimeError):
        log.record("111", "DAY_5", "Dr. Doe", WHEN)
    assert log.next_row == 2
    log.record("111", "DAY_5", "Dr. Doe", WHEN)
    assert table(book)[1][0] == "111"


# --- Read back by the reader the next run uses --------------------------------------


def sheet_with(book):
    ongoing = FakeWorksheet(
        [["Case ID", "Date & Time", "Provider", "Provider Email"], ["111", "May 4th, 2026 08:00 AM MST", "Dr. Doe", "a@b.test"]]
    )
    finished = FakeWorksheet([["Case ID"]])
    return FakeSpreadsheet({ONGOING_TAB: ongoing, FINISHED_TAB: finished, SENT_TAB: book.tabs[SENT_TAB]})


def test_what_is_written_is_read_back_as_sent():
    book = Book()
    log = SentLog.open(book)
    log.record("111", "DAY_5", "Dr. Doe", WHEN)
    log.record("222", "DAY_11_OVERDUE", "Dr. Smith", WHEN)
    data = read_reminder_data(sheet_with(book))
    assert data.sent == {("111", "DAY_5"), ("222", "DAY_11_OVERDUE")}
    assert data.notes == []


def test_a_tab_created_by_the_log_is_readable_when_it_has_no_rows_yet():
    book = Book()
    SentLog.open(book)
    data = read_reminder_data(sheet_with(book))
    assert data.sent == set() and data.log_found