"""
Tests for app/contacts_tab.py: loading the Contacts tab, and noting its problems on
the tab itself. Uses the shared in-memory FakeWorksheet and FakeSpreadsheet.

Run from the project root:
    python -m pytest tests/test_contacts_tab.py -v
"""

from app.contacts_tab import (
    CONTACTS_TAB,
    NOTE_CELL,
    NOTE_HEADER,
    PROVIDER_EMAIL_HEADER,
    PSYCHOMETRIST_EMAIL_HEADER,
    flag_contacts_tab,
    load_contacts_tab,
)
from tests.fakes import FakeSpreadsheet, FakeWorksheet


def spreadsheet_with(rows):
    tab = FakeWorksheet(rows)
    return FakeSpreadsheet({CONTACTS_TAB: tab}), tab


GOOD = [["Name", "Email", "Role"], ["Dr. Jane Smith", "jane@example.test", "Provider"]]


def test_the_headers_the_sweep_fills_in_are_named_as_asish_will_read_them():
    assert (PROVIDER_EMAIL_HEADER, PSYCHOMETRIST_EMAIL_HEADER) == ("Provider Email", "Psychometrist Email")


# --- Loading ---------------------------------------------------------


def test_a_good_tab_gives_a_lookup_and_no_problems():
    sheet, tab = spreadsheet_with(GOOD)
    loaded = load_contacts_tab(sheet)
    assert loaded.problems == [] and loaded.worksheet is tab
    assert loaded.lookup.find("Dr Smith", "provider").email == "jane@example.test"


def test_a_missing_tab_gives_no_lookup_and_says_so():
    loaded = load_contacts_tab(FakeSpreadsheet({}))
    assert loaded.lookup is None and loaded.worksheet is None
    assert loaded.problems == [
        "No tab named 'Contacts' was found, so provider and psychometrist emails can't be filled in"
    ]


def test_a_tab_with_only_a_header_row_gives_no_lookup_but_keeps_the_tab():
    sheet, tab = spreadsheet_with([["Name", "Email"]])
    loaded = load_contacts_tab(sheet)
    assert loaded.lookup is None and loaded.worksheet is tab
    assert loaded.problems == [
        "The Contacts tab has no people listed, so provider and psychometrist emails can't be filled in"
    ]


def test_a_tab_missing_a_required_header_gives_no_lookup_but_keeps_the_tab():
    sheet, tab = spreadsheet_with([["Name", "Role"], ["Dr. Jane Smith", "Provider"]])
    loaded = load_contacts_tab(sheet)
    assert loaded.lookup is None and loaded.worksheet is tab
    assert "missing the 'Email' column" in loaded.problems[0]


def test_bad_rows_are_reported_alongside_a_lookup_that_still_works():
    sheet, _ = spreadsheet_with(GOOD + [["Dr. Raj Singh", "not an address", "Provider"]])
    loaded = load_contacts_tab(sheet)
    assert loaded.lookup is not None
    assert loaded.problems == ["Row 3: 'Dr. Raj Singh' has no valid email address, so it was skipped"]
    assert loaded.lookup.find("Dr Smith", "provider").status == "found"


# --- Noting problems on the tab ---------------------------------------


def test_with_no_tab_there_is_nowhere_to_note_anything():
    assert flag_contacts_tab(None, ["a problem"]) == "no_tab"


def test_problems_are_noted_on_the_header_cell():
    _, tab = spreadsheet_with(GOOD)
    assert flag_contacts_tab(tab, ["a problem"]) == "flagged"
    note = tab.get_note(NOTE_CELL)
    assert note.startswith(NOTE_HEADER) and "- a problem" in note and "clears itself" in note


def test_the_same_problems_again_change_nothing():
    _, tab = spreadsheet_with(GOOD)
    flag_contacts_tab(tab, ["a problem"])
    assert flag_contacts_tab(tab, ["a problem"]) == "unchanged"


def test_different_problems_replace_our_own_note():
    _, tab = spreadsheet_with(GOOD)
    flag_contacts_tab(tab, ["a problem"])
    assert flag_contacts_tab(tab, ["another problem"]) == "flagged"
    assert "another problem" in tab.get_note(NOTE_CELL) and "- a problem" not in tab.get_note(NOTE_CELL)


def test_our_note_clears_once_the_problems_are_fixed():
    _, tab = spreadsheet_with(GOOD)
    flag_contacts_tab(tab, ["a problem"])
    assert flag_contacts_tab(tab, []) == "cleared"
    assert tab.get_note(NOTE_CELL) == ""


def test_a_note_someone_else_wrote_is_never_replaced_or_cleared():
    _, tab = spreadsheet_with(GOOD)
    tab.update_note(NOTE_CELL, "Dr. wants this tab kept as is")
    assert flag_contacts_tab(tab, ["a problem"]) == "skipped"
    assert flag_contacts_tab(tab, []) == "unchanged"
    assert tab.get_note(NOTE_CELL) == "Dr. wants this tab kept as is"


def test_no_problems_and_no_note_is_nothing_to_do():
    _, tab = spreadsheet_with(GOOD)
    assert flag_contacts_tab(tab, []) == "unchanged"