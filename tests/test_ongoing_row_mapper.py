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

from gspread.utils import a1_to_rowcol

from app.ongoing_row_mapper import (
    DUPLICATE_HIGHLIGHT_COLOR,
    add_or_update_case,
    build_ongoing_row,
)


HEADERS = [
    "Case ID", "Patient Name", "Phone", "DOB", "Adult", "Child",
    "Date & Time", "Services Requested", "Allegations",
    "Psychometrist", "Provider", "Price", "Comments",
]
TEMPLATE = "Provider:\nPsychometrist:\n+Service:\nPrice:"


class FakeWorksheet:
    """
    Stands in for a gspread Worksheet. Imitates two real behaviors
    placement depends on: get_all_values() stops at the last row with
    content, and writing beyond row_count is an error.
    """

    def __init__(self, rows, row_count=1000):
        self.rows = [list(row) for row in rows]  # row 1 = headers
        self.row_count = row_count
        self.notes = {}
        self.formats = {}
        self.rows_added = 0

    # --- reads ---

    def get_all_values(self):
        rows = [list(row) for row in self.rows]
        while rows and not any(cell.strip() for cell in rows[-1]):
            rows.pop()
        width = max(len(row) for row in rows)
        return [row + [""] * (width - len(row)) for row in rows]

    def row_values(self, row_number):
        return list(self.rows[row_number - 1])

    # --- writes ---

    def _set(self, row, col, value):
        if row > self.row_count:
            raise IndexError(f"row {row} is beyond the sheet's {self.row_count} rows")
        while len(self.rows) < row:
            self.rows.append([""] * len(HEADERS))
        target = self.rows[row - 1]
        while len(target) < col:
            target.append("")
        target[col - 1] = value

    def update(self, range_name, values):
        start_row, start_col = a1_to_rowcol(range_name.split(":")[0])
        for r_offset, row_values in enumerate(values):
            for c_offset, value in enumerate(row_values):
                self._set(start_row + r_offset, start_col + c_offset, value)

    def update_cell(self, row, col, value):
        self._set(row, col, value)

    def add_rows(self, n):
        self.row_count += n
        self.rows_added += n

    def format(self, cell_range, fmt):
        self.formats[cell_range] = fmt

    def update_note(self, cell, text):
        self.notes[cell] = text

    # --- test helper ---

    def value(self, row_number, header):
        row = self.rows[row_number - 1]
        idx = HEADERS.index(header)
        return row[idx] if idx < len(row) else ""


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