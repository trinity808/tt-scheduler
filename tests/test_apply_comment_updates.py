"""
Tests for app/apply_comment_updates.py.

Uses an in-memory FakeWorksheet instead of a real Google Sheet, so
every test builds exactly the rows it needs, runs in milliseconds,
and leaves nothing to clean up. This works because
apply_comment_updates() takes the worksheet as a parameter rather
than connecting to Google itself.

What a fake can't catch: real Sheets behavior, such as how notes or
formatting actually render. That's what the occasional live check
against the sandbox sheet is for.

Run from the project root:
    python -m pytest tests/test_apply_comment_updates.py -v
"""

import pytest

from app.apply_comment_updates import (
    CLEAR_COLOR,
    COMMENT_ISSUE_COLOR,
    apply_comment_updates,
)


class FakeWorksheet:
    """
    Stands in for a gspread Worksheet, implementing only the methods
    apply_comment_updates() and its helpers actually call.
    """

    def __init__(self, rows):
        self.rows = [list(row) for row in rows]  # row 1 = headers
        self.notes = {}     # cell (e.g. "D2") -> note text
        self.formats = {}   # cell -> last format applied
        self.writes = []    # every update_cell call, as (row, col, value)

    def row_values(self, row_number):
        return list(self.rows[row_number - 1])

    def get_all_values(self):
        # Return a copy, like the real API: the caller gets a snapshot,
        # and later writes don't change what it already read.
        return [list(row) for row in self.rows]

    def update_cell(self, row, col, value):
        self.writes.append((row, col, value))
        self.rows[row - 1][col - 1] = value

    def format(self, cell, fmt):
        self.formats[cell] = fmt

    def update_note(self, cell, text):
        self.notes[cell] = text

    def clear_note(self, cell):
        self.notes.pop(cell, None)

    def value(self, row_number, header):
        """Test helper: read a cell by row number and header name."""
        return self.rows[row_number - 1][self.rows[0].index(header)]


# Comments is column D with these headers, so row 2's Comments cell
# is "D2", row 3's is "D3", and so on.
HEADERS = ["Case ID", "Provider", "Psychometrist", "Comments"]


def make_sheet(*rows):
    return FakeWorksheet([HEADERS, *rows])


# --- Applying fields -----------------------------------------------


def test_applies_provider_and_psychometrist():
    ws = make_sheet(["15419897", "", "", "Provider: Dr. Shelton\nPsychometrist: J. Smith"])
    apply_comment_updates(ws)
    assert ws.value(2, "Provider") == "Dr. Shelton"
    assert ws.value(2, "Psychometrist") == "J. Smith"


def test_outcomes_record_old_and_new_values():
    ws = make_sheet(["15419897", "Dr. Smith", "", "Provider: Dr. Shelton\nPsychometrist: J. Smith"])
    results = apply_comment_updates(ws)
    fields = results[0]["fields"]
    assert fields["Provider"] == ("updated", "Dr. Smith", "Dr. Shelton")
    assert fields["Psychometrist"] == ("updated", "", "J. Smith")


def test_fields_only_include_what_the_comment_mentions():
    ws = make_sheet(["15419897", "", "", "Provider: Dr. Shelton"])
    results = apply_comment_updates(ws)
    assert list(results[0]["fields"]) == ["Provider"]


def test_unchanged_value_is_not_rewritten():
    ws = make_sheet(["15419897", "Dr. Shelton", "", "Provider: Dr. Shelton"])
    results = apply_comment_updates(ws)
    assert ws.writes == []
    assert results[0]["fields"]["Provider"][0] == "unchanged"


def test_rerun_with_no_changes_writes_nothing():
    ws = make_sheet(["15419897", "", "", "Provider: Dr. Shelton\nPsychometrist: J. Smith"])
    apply_comment_updates(ws)
    assert ws.writes != []

    ws.writes = []
    apply_comment_updates(ws)
    assert ws.writes == []


def test_price_and_services_are_not_applied():
    """Scope guard for Parts 1 and 2. Replace when Part 3 starts applying price and services."""
    headers = ["Case ID", "Provider", "Psychometrist", "Price", "Comments"]
    ws = FakeWorksheet([
        headers,
        ["15419897", "", "", "484.00",
         "Provider: Dr. Shelton\n+Service: 96130 - Autism testing - $92\nPrice: $576"],
    ])
    apply_comment_updates(ws)
    assert ws.value(2, "Price") == "484.00"
    price_col = headers.index("Price") + 1
    assert all(col != price_col for _, col, _ in ws.writes)


def test_columns_found_by_name_not_position():
    headers = ["Comments", "Psychometrist", "Case ID", "Provider"]
    ws = FakeWorksheet([
        headers,
        ["Provider: Dr. Shelton", "", "15419897", ""],
    ])
    apply_comment_updates(ws)
    assert ws.value(2, "Provider") == "Dr. Shelton"
    assert "A2" in ws.formats  # Comments is column A here


def test_missing_required_column_raises():
    ws = FakeWorksheet([
        ["Case ID", "Provider", "Psychometrist"],
        ["15419897", "", ""],
    ])
    with pytest.raises(ValueError):
        apply_comment_updates(ws)


# --- Rows that are skipped -----------------------------------------


@pytest.mark.parametrize("case_id", ["", "   "])
def test_row_without_case_id_is_skipped_entirely(case_id):
    ws = make_sheet([case_id, "", "", "Provider: Dr. Doe"])
    results = apply_comment_updates(ws)
    assert results == []
    assert ws.writes == []
    assert "D2" not in ws.formats
    assert "D2" not in ws.notes


def test_template_in_caseless_row_is_left_alone():
    ws = make_sheet(
        ["15419897", "", "", "Provider: Dr. Shelton"],
        ["", "", "", "Provider:\nPsychometrist:\n+Service:\nPrice:"],
    )
    results = apply_comment_updates(ws)
    assert [r["case_id"] for r in results] == ["15419897"]
    assert "D3" not in ws.formats


def test_row_with_empty_comment_is_skipped():
    ws = make_sheet(["15419897", "Dr. Shelton", "", ""])
    results = apply_comment_updates(ws)
    assert results == []
    assert ws.writes == []


def test_blank_template_on_a_case_changes_nothing():
    ws = make_sheet(["15419897", "Dr. Shelton", "", "Provider:\nPsychometrist:\n+Service:\nPrice:"])
    results = apply_comment_updates(ws)
    assert results[0]["fields"] == {}
    assert ws.writes == []
    assert "D2" not in ws.notes


# --- Flagging ------------------------------------------------------


def test_problem_comment_gets_highlight_and_note():
    ws = make_sheet(["15419897", "", "", "Provder: Dr. Smith"])
    apply_comment_updates(ws)
    assert ws.formats["D2"]["backgroundColor"] == COMMENT_ISSUE_COLOR
    assert "Comment needs attention" in ws.notes["D2"]
    assert "did you mean Provider?" in ws.notes["D2"]


def test_flag_clears_once_comment_is_fixed():
    ws = make_sheet(["15419897", "", "", "Provder: Dr. Smith"])
    apply_comment_updates(ws)
    assert "D2" in ws.notes

    ws.rows[1][3] = "Provider: Dr. Smith"  # OA fixes the typo
    apply_comment_updates(ws)
    assert "D2" not in ws.notes
    assert ws.formats["D2"]["backgroundColor"] == CLEAR_COLOR
    assert ws.value(2, "Provider") == "Dr. Smith"


def test_clean_comment_is_never_left_flagged():
    ws = make_sheet(["15419897", "", "", "Provider: Dr. Shelton"])
    apply_comment_updates(ws)
    assert "D2" not in ws.notes
    assert ws.formats["D2"]["backgroundColor"] == CLEAR_COLOR


def test_only_rows_with_problems_are_flagged():
    ws = make_sheet(
        ["15419897", "", "", "Provider: Dr. Shelton"],
        ["15419898", "", "", "Provder: Dr. Smith"],
    )
    results = apply_comment_updates(ws)
    assert [r["case_id"] for r in results] == ["15419897", "15419898"]
    assert "D2" not in ws.notes
    assert "D3" in ws.notes


def test_crammed_line_does_not_overwrite_provider():
    ws = make_sheet(
        ["15419897", "Dr. Shelton", "", "Provider: Dr. Doe +Service: 96130 - Autism testing - $92"],
    )
    apply_comment_updates(ws)
    assert ws.value(2, "Provider") == "Dr. Shelton"
    assert ws.writes == []
    assert "skipped: more than one field" in ws.notes["D2"]


def test_correct_line_applies_alongside_skipped_crammed_line():
    ws = make_sheet(
        ["15419897", "Dr. Smith", "",
         "Provider: Dr. Doe +Service: 96130 - Autism testing - $92\nProvider: Dr. Shelton"],
    )
    apply_comment_updates(ws)
    assert ws.value(2, "Provider") == "Dr. Shelton"
    assert "skipped: more than one field" in ws.notes["D2"]