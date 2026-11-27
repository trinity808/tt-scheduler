"""
Tests for app/reminders_sheet.py: reading the Ongoing, Finished and Reminders Sent
tabs. Uses the shared in-memory FakeWorksheet and FakeSpreadsheet.

Run from the project root:
    python -m pytest tests/test_reminders_sheet.py -v
"""

import pytest
from gspread.exceptions import WorksheetNotFound

from app.reminders_sheet import (
    FINISHED_TAB,
    ONGOING_TAB,
    PROVIDER_EMAIL,
    SENT_HEADERS,
    SENT_TAB,
    read_reminder_data,
)
from tests.fakes import FakeSpreadsheet, FakeWorksheet
from tests.google_like import GoogleLikeWorksheet


ADDRESS = "unique.provider.7731@example.test"

ONGOING_HEADERS = [
    "Case ID", "Patient Name", "Date & Time", "Psychometrist", "Psychometrist Email",
    "Provider", "Provider Email", "Comments",
]
FINISHED_HEADERS = ["Case ID", "Patient Name", "Date Completed"]


def ongoing_row(case_id="111", when="May 4th, 2026 08:00 AM MST", provider="Dr. Doe", email=ADDRESS):
    return [case_id, "A Patient", when, "John Doe", "john@example.test", provider, email, ""]


def sheet(ongoing=None, finished=None, sent="absent"):
    tabs = {
        ONGOING_TAB: FakeWorksheet(ongoing if ongoing is not None else [ONGOING_HEADERS, ongoing_row()]),
        FINISHED_TAB: FakeWorksheet(finished if finished is not None else [FINISHED_HEADERS]),
    }
    if sent != "absent":
        tabs[SENT_TAB] = FakeWorksheet(sent)
    return FakeSpreadsheet(tabs), tabs


def test_the_sent_log_has_the_columns_later_runs_will_write():
    assert SENT_HEADERS == ["Case ID", "Reminder", "Recipient", "Sent On", "Sent At"]


def test_the_email_header_is_the_one_the_sweep_fills_in():
    assert PROVIDER_EMAIL == "Provider Email"


def test_the_email_header_matches_the_sweeps_when_the_contacts_code_is_there():
    contacts_tab = pytest.importorskip("app.contacts_tab")
    assert PROVIDER_EMAIL == contacts_tab.PROVIDER_EMAIL_HEADER


# --- Ongoing ----------------------------------------------------------------


def test_each_open_case_is_read_with_its_row_exam_text_provider_and_address():
    book, _ = sheet()
    (case,) = read_reminder_data(book).cases
    assert (case.case_id, case.row_number) == ("111", 2)
    assert case.date_time_text == "May 4th, 2026 08:00 AM MST"
    assert (case.provider_name, case.provider_email) == ("Dr. Doe", ADDRESS)


def test_the_address_is_kept_out_of_the_cases_repr():
    book, _ = sheet()
    (case,) = read_reminder_data(book).cases
    assert ADDRESS not in repr(case)


def test_cases_come_in_sheet_order_with_their_real_row_numbers():
    book, _ = sheet([ONGOING_HEADERS, ongoing_row("333"), ongoing_row("111"), ongoing_row("222")])
    cases = read_reminder_data(book).cases
    assert [(c.case_id, c.row_number) for c in cases] == [("333", 2), ("111", 3), ("222", 4)]


def test_rows_with_no_case_id_are_skipped_like_the_sweep_does():
    spare = ["", "", "", "", "", "Dr. Spare", "spare@example.test", "Provider: Dr. Doe"]
    book, _ = sheet([ONGOING_HEADERS, ongoing_row("111"), spare, ongoing_row("222")])
    cases = read_reminder_data(book).cases
    assert [c.case_id for c in cases] == ["111", "222"] and cases[1].row_number == 4


def test_columns_are_found_by_name_whatever_their_order_capitals_or_spacing():
    headers = ["provider  EMAIL", "date & time", "case id", "PROVIDER"]
    book, _ = sheet([headers, [ADDRESS, "May 4, 2026", " 111 ", "Dr. Doe"]])
    (case,) = read_reminder_data(book).cases
    assert (case.case_id, case.date_time_text, case.provider_name) == ("111", "May 4, 2026", "Dr. Doe")
    assert case.provider_email == ADDRESS


def test_a_case_row_shorter_than_the_header_row_reads_as_blanks():
    book, _ = sheet([ONGOING_HEADERS, ["111", "A Patient", "May 4, 2026"]])
    (case,) = read_reminder_data(book).cases
    assert (case.provider_name, case.provider_email) == ("", "")


def test_the_provider_column_is_optional_but_the_email_is_not():
    headers = ["Case ID", "Date & Time", "Provider Email"]
    book, _ = sheet([headers, ["111", "May 4, 2026", ADDRESS]])
    assert read_reminder_data(book).cases[0].provider_name == ""


@pytest.mark.parametrize("missing", ["Case ID", "Date & Time", "Provider Email"])
def test_a_missing_required_column_stops_the_run_and_names_it(missing):
    headers = [h for h in ONGOING_HEADERS if h != missing]
    book, _ = sheet([headers, [""] * len(headers)])
    with pytest.raises(ValueError, match=f"The Ongoing tab has no '{missing}' column"):
        read_reminder_data(book)


def test_an_ongoing_tab_with_nothing_in_it_stops_the_run():
    book, _ = sheet([[]])
    with pytest.raises(ValueError, match="Ongoing tab is empty"):
        read_reminder_data(book)


def test_a_header_only_ongoing_tab_just_has_no_cases():
    book, _ = sheet([ONGOING_HEADERS])
    assert read_reminder_data(book).cases == []


def test_a_case_listed_twice_is_read_once_from_its_first_row_and_noted():
    book, _ = sheet([ONGOING_HEADERS, ongoing_row("111", provider="Dr. First"), ongoing_row("111", provider="Dr. Second")])
    data = read_reminder_data(book)
    assert [c.provider_name for c in data.cases] == ["Dr. First"]
    assert data.notes == ["Case 111 is listed more than once in Ongoing (row 3). Only its first row is used."]


def test_a_missing_ongoing_tab_is_an_error():
    book = FakeSpreadsheet({FINISHED_TAB: FakeWorksheet([FINISHED_HEADERS])})
    with pytest.raises(WorksheetNotFound):
        read_reminder_data(book)


# --- Finished ---------------------------------------------------------------


def test_the_finished_case_ids_are_collected():
    finished = [FINISHED_HEADERS, ["555", "x", "5/8/2026"], ["", "stray note", ""], ["666", "y", "5/9/2026"]]
    book, _ = sheet(finished=finished)
    assert read_reminder_data(book).finished_ids == {"555", "666"}


def test_an_empty_finished_tab_means_nothing_is_finished_yet():
    book, _ = sheet(finished=[[]])
    assert read_reminder_data(book).finished_ids == set()


def test_a_blank_finished_tab_as_google_gives_it_back_means_nothing_is_finished_yet():
    book = FakeSpreadsheet({
        ONGOING_TAB: FakeWorksheet([ONGOING_HEADERS, ongoing_row()]),
        FINISHED_TAB: GoogleLikeWorksheet([]),
    })
    assert read_reminder_data(book).finished_ids == set()


def test_a_finished_tab_without_a_case_id_column_stops_the_run():
    book, _ = sheet(finished=[["Patient Name", "Date Completed"], ["x", "y"]])
    with pytest.raises(ValueError, match="The Finished tab has no 'Case ID' column"):
        read_reminder_data(book)


def test_a_missing_finished_tab_is_an_error_since_it_says_which_reports_are_in():
    book = FakeSpreadsheet({ONGOING_TAB: FakeWorksheet([ONGOING_HEADERS, ongoing_row()])})
    with pytest.raises(WorksheetNotFound):
        read_reminder_data(book)


# --- Reminders Sent ---------------------------------------------------------


def test_with_no_log_tab_nothing_counts_as_sent_and_that_is_said():
    book, _ = sheet()
    data = read_reminder_data(book)
    assert data.sent == set() and data.log_found is False


def test_the_log_gives_the_case_and_reminder_pairs_already_sent():
    log = [SENT_HEADERS, ["111", "DAY_5", "Provider", "2026-05-11", "2026-05-11 08:01"],
           ["111", "DAY_1", "Provider", "2026-05-05", ""], ["222", "DAY_5", "Provider", "", ""]]
    book, _ = sheet(sent=log)
    data = read_reminder_data(book)
    assert data.sent == {("111", "DAY_5"), ("111", "DAY_1"), ("222", "DAY_5")} and data.log_found


def test_the_log_needs_only_the_case_and_reminder_columns_in_any_order():
    book, _ = sheet(sent=[["reminder", "CASE ID"], ["DAY_5", "111"]])
    assert read_reminder_data(book).sent == {("111", "DAY_5")}


def test_log_rows_missing_a_case_or_a_reminder_are_ignored():
    book, _ = sheet(sent=[SENT_HEADERS, ["", "DAY_5", "", "", ""], ["111", "", "", "", ""]])
    assert read_reminder_data(book).sent == set()


@pytest.mark.parametrize("written", ["DAY_5", "Day_5", "day_5", "  day_5  "])
def test_a_reminder_name_in_the_log_matches_whatever_its_capitals(written):
    book, _ = sheet(sent=[SENT_HEADERS, ["111", written, "", "", ""]])
    data = read_reminder_data(book)
    assert data.sent == {("111", "DAY_5")} and data.notes == []


@pytest.mark.parametrize("written", ["Day5", "DAY 5", "Day_55", "check-in"])
def test_a_name_that_is_not_a_reminder_is_noted_with_its_row_and_not_counted(written):
    book, _ = sheet(sent=[SENT_HEADERS, ["111", "DAY_1", "", "", ""], ["111", written, "", "", ""]])
    data = read_reminder_data(book)
    assert data.sent == {("111", "DAY_1")}
    assert data.notes == [
        f"Reminders Sent, row 3: '{written}' isn't a reminder name, so it is ignored. "
        "Use one of DAY_1, DAY_3, DAY_5, DAY_8, DAY_10, DAY_11_OVERDUE."
    ]


def test_a_header_only_log_has_nothing_sent():
    book, _ = sheet(sent=[SENT_HEADERS])
    data = read_reminder_data(book)
    assert data.sent == set() and data.log_found


@pytest.mark.parametrize("missing", ["Case ID", "Reminder"])
def test_a_log_missing_a_needed_column_stops_the_run_rather_than_risk_sending_twice(missing):
    headers = [h for h in SENT_HEADERS if h != missing]
    book, _ = sheet(sent=[headers, ["x"] * len(headers)])
    with pytest.raises(ValueError, match=f"The Reminders Sent tab has no '{missing}' column"):
        read_reminder_data(book)


# --- Reading writes nothing -------------------------------------------------


def test_reading_writes_nothing_anywhere():
    book, tabs = sheet(sent=[SENT_HEADERS])
    read_reminder_data(book)
    for tab in tabs.values():
        assert tab.writes == [] and tab.notes == {}


# --- A tab with nothing in it, as Google gives it back ([[]], not []) --------------


def test_a_blank_ongoing_tab_is_called_empty_not_missing_a_column():
    book = FakeSpreadsheet({
        ONGOING_TAB: GoogleLikeWorksheet([]),
        FINISHED_TAB: FakeWorksheet([FINISHED_HEADERS]),
    })
    with pytest.raises(ValueError, match="The Ongoing tab is empty: it has no header row"):
        read_reminder_data(book)


def book_with_log_tab(log_tab):
    return FakeSpreadsheet({
        ONGOING_TAB: FakeWorksheet([ONGOING_HEADERS, ongoing_row()]),
        FINISHED_TAB: FakeWorksheet([FINISHED_HEADERS]),
        SENT_TAB: log_tab,
    })


def test_a_blank_log_tab_means_nothing_is_sent_yet_and_the_tab_is_found():
    book = book_with_log_tab(GoogleLikeWorksheet([]))
    data = read_reminder_data(book)
    assert data.sent == set() and data.log_found is True and data.notes == []


def test_a_log_tab_with_rows_but_a_blank_header_is_still_a_damaged_header():
    book = book_with_log_tab(GoogleLikeWorksheet([[""] * 5, ["111", "DAY_5", "", "", ""]]))
    with pytest.raises(ValueError, match="The Reminders Sent tab has no"):
        read_reminder_data(book)