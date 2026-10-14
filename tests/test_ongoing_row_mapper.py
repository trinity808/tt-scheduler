"""
Tests for app/ongoing_row_mapper.py: mapping extracted data to a row,
where new cases land, and how duplicates are handled.

Uses an in-memory FakeWorksheet instead of a real Google Sheet, and
plain dictionaries shaped like pdf_extractor.py's output instead of
real PDFs.

What a fake can't catch: real Sheets behavior beyond what's imitated
here. The append_row() column-shift bug, for example, came from how
Sheets detects tables, which no fake reproduces. That's what the
occasional live check against the sandbox sheet is for.

Run from the project root:
    python -m pytest tests/test_ongoing_row_mapper.py -v
"""


from app.ongoing_row_mapper import (
    DUPLICATE_HIGHLIGHT_COLOR,
    add_or_update_case,
    build_ongoing_row,
)
from tests.fakes import FakeWorksheet


HEADERS = [
    "Case ID", "Patient Name", "Phone", "DOB", "Adult", "Child",
    "Date & Time", "Services Requested", "Allegations",
    "Psychometrist", "Provider", "Price", "Comments",
]
TEMPLATE = "Provider:\nPsychometrist:\n+Service:\nPrice:"


def extracted(case_id, name="Test Patient", total="484.00"):
    """Dictionary shaped like pdf_extractor.extract_schedule_data()'s output."""
    return {
        "case_id": case_id,
        "patient_name": name,
        "phone": "(602) 555-0100",
        "dob": "January 1, 2000",
        "adult": "X",
        "child": "",
        "schedule_date": "May 4th, 2026",
        "schedule_time": "08:00 AM MST",
        "services_requested": "1. 90791 - Psychiatric Exam",
        "allegations": "test allegations",
        "total": total,
    }


def case_row(case_id, provider="", psychometrist="", comments="", price="484.00"):
    """A full sheet row for an existing case."""
    row = [""] * len(HEADERS)
    row[HEADERS.index("Case ID")] = case_id
    row[HEADERS.index("Patient Name")] = "Existing Patient"
    row[HEADERS.index("Provider")] = provider
    row[HEADERS.index("Psychometrist")] = psychometrist
    row[HEADERS.index("Comments")] = comments
    row[HEADERS.index("Price")] = price
    return row


def template_row():
    """An empty row with only a template in its Comments cell."""
    row = [""] * len(HEADERS)
    row[HEADERS.index("Comments")] = TEMPLATE
    return row


def blank_row():
    return [""] * len(HEADERS)


# --- Mapping extracted data to a row --------------------------------


def test_build_row_maps_fields_by_header():
    row = dict(zip(HEADERS, build_ongoing_row(extracted("15419897"), HEADERS)))
    assert row["Case ID"] == "15419897"
    assert row["Price"] == "484.00"
    assert row["Date & Time"] == "May 4th, 2026 08:00 AM MST"
    assert row["Provider"] == ""
    assert row["Psychometrist"] == ""
    assert row["Comments"] == ""


def test_build_row_leaves_unknown_headers_blank():
    row = build_ongoing_row(extracted("15419897"), ["Case ID", "Some New Column"])
    assert row == ["15419897", ""]


# --- Where new cases land -------------------------------------------


def test_first_case_goes_into_row_2():
    ws = FakeWorksheet([HEADERS])
    assert add_or_update_case(ws, extracted("15419897")) == ("added", 2)
    assert ws.value(2, "Case ID") == "15419897"


def test_new_case_goes_after_last_case():
    ws = FakeWorksheet([HEADERS, case_row("111"), case_row("222")])
    assert add_or_update_case(ws, extracted("333")) == ("added", 4)
    assert ws.value(4, "Case ID") == "333"


def test_templates_below_data_do_not_push_new_case_down():
    ws = FakeWorksheet([
        HEADERS, case_row("111"), case_row("222"),
        template_row(), template_row(), template_row(),
    ])
    assert add_or_update_case(ws, extracted("333")) == ("added", 4)
    assert ws.value(4, "Case ID") == "333"


def test_template_is_kept_on_the_row_the_case_lands_in():
    ws = FakeWorksheet([HEADERS, case_row("111"), template_row(), template_row()])
    add_or_update_case(ws, extracted("222"))
    assert ws.value(3, "Comments") == TEMPLATE
    assert ws.value(3, "Price") == "484.00"  # extraction data still filled in
    assert ws.value(4, "Case ID") == ""      # next template row untouched
    assert ws.value(4, "Comments") == TEMPLATE


def test_gap_from_a_cleared_row_is_not_filled():
    ws = FakeWorksheet([HEADERS, case_row("111"), blank_row(), case_row("333")])
    assert add_or_update_case(ws, extracted("444")) == ("added", 5)
    assert ws.value(3, "Case ID") == ""


def test_new_case_after_a_gap_starts_at_column_a():
    # Regression test for the append_row() bug: a blank row in the
    # data once shifted a new case so it started at the Provider column.
    ws = FakeWorksheet([HEADERS, case_row("111"), blank_row(), case_row("333")])
    add_or_update_case(ws, extracted("444", name="New Patient"))
    assert ws.rows[4][0] == "444"
    assert ws.rows[4][1] == "New Patient"


def test_sheet_grows_when_out_of_rows():
    ws = FakeWorksheet([HEADERS, case_row("111"), case_row("222")], row_count=3)
    assert add_or_update_case(ws, extracted("333")) == ("added", 4)
    assert ws.rows_added == 1
    assert ws.value(4, "Case ID") == "333"


# --- Duplicates -----------------------------------------------------


def test_duplicate_updates_in_place_instead_of_adding():
    ws = FakeWorksheet([HEADERS, case_row("111"), case_row("222", price="250.00")])
    assert add_or_update_case(ws, extracted("222", total="484.00")) == ("updated", 3)
    assert ws.value(3, "Price") == "484.00"
    assert len(ws.get_all_values()) == 3  # no new row


def test_duplicate_keeps_manual_entry_fields():
    ws = FakeWorksheet([
        HEADERS,
        case_row("222", provider="Dr. Shelton", psychometrist="J. Smith",
                 comments="Provider: Dr. Shelton"),
    ])
    add_or_update_case(ws, extracted("222"))
    assert ws.value(2, "Provider") == "Dr. Shelton"
    assert ws.value(2, "Psychometrist") == "J. Smith"
    assert ws.value(2, "Comments") == "Provider: Dr. Shelton"


def test_duplicate_row_is_highlighted_and_noted():
    ws = FakeWorksheet([HEADERS, case_row("222")])
    add_or_update_case(ws, extracted("222"))
    assert ws.formats["A2:M2"]["backgroundColor"] == DUPLICATE_HIGHLIGHT_COLOR
    assert "Duplicate entry received" in ws.notes["A2"]


def test_duplicate_match_ignores_surrounding_whitespace():
    ws = FakeWorksheet([HEADERS, case_row(" 222 ")])
    assert add_or_update_case(ws, extracted("222")) == ("updated", 2)