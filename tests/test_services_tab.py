"""
Tests for app/services_tab.py: loading the Services tab into a lookup,
carrying on without one when the tab can't be used, and flagging problems
with a note on the tab's header cell.

Uses the shared in-memory fakes from tests/fakes.py, so no Google
Sheet is involved.

Run from the project root:
    python -m pytest tests/test_services_tab.py -v
"""

import pytest

from app.services_tab import (
    NOTE_CELL,
    NOTE_FOOTER,
    NOTE_HEADER,
    SERVICES_TAB,
    flag_services_tab,
    load_services_tab,
)
from tests.fakes import FakeSpreadsheet, FakeWorksheet


HEADERS = ["Code", "Aliases", "Name"]


def services_ws(*rows, headers=HEADERS):
    return FakeWorksheet([headers, *rows])


def spreadsheet(services=None):
    """An Ongoing tab, plus a Services tab if one is given."""
    tabs = {"Ongoing": FakeWorksheet([["Case ID"]])}
    if services is not None:
        tabs[SERVICES_TAB] = services
    return FakeSpreadsheet(tabs)


# --- A usable tab ---------------------------------------------------


def test_valid_tab_builds_a_lookup():
    ws = services_ws(
        ["96101-WISC-V", "WISC", "Wechsler Intelligence Scale for Children"],
        ["96130-CARS-2", "CARS", ""],
    )
    tab = load_services_tab(spreadsheet(ws))
    assert tab.lookup.resolve("wisc") == "96101-WISC-V"
    assert tab.lookup.resolve("CARS-2") == "96130-CARS-2"
    assert tab.problems == []


def test_worksheet_is_returned_so_problems_can_be_flagged_on_it():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    assert load_services_tab(spreadsheet(ws)).worksheet is ws


def test_columns_found_by_name_not_position():
    ws = FakeWorksheet([
        ["Name", "Aliases", "Code"],
        ["Wechsler Intelligence Scale for Children", "WISC", "96101-WISC-V"],
    ])
    assert load_services_tab(spreadsheet(ws)).lookup.resolve("WISC") == "96101-WISC-V"


def test_loading_only_reads_the_tab():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    load_services_tab(spreadsheet(ws))
    assert ws.writes == []
    assert ws.notes == {}
    assert ws.formats == {}


# --- Problems with individual rows ----------------------------------


def test_bad_rows_are_reported_without_stopping_the_lookup():
    ws = services_ws(
        ["96101-WISC-V", "WISC", ""],
        ["Beery VMI", "", ""],
        ["90791", "Psych", ""],
        ["99358-PSYCH", "", ""],
    )
    tab = load_services_tab(spreadsheet(ws))
    assert tab.lookup is not None
    assert tab.lookup.resolve("WISC") == "96101-WISC-V"
    assert len(tab.problems) == 2
    assert any("Beery VMI" in p for p in tab.problems)
    assert any("PSYCH" in p for p in tab.problems)


# --- A tab that can't be used ---------------------------------------


def test_missing_tab_gives_no_lookup_and_says_why():
    tab = load_services_tab(spreadsheet())
    assert tab.lookup is None
    assert tab.worksheet is None
    assert len(tab.problems) == 1
    assert "No tab named 'Services'" in tab.problems[0]


@pytest.mark.parametrize(
    "rows",
    [
        [],                      # completely empty
        [HEADERS],               # header row only
    ],
)
def test_empty_tab_gives_no_lookup_and_says_why(rows):
    ws = FakeWorksheet(rows)
    tab = load_services_tab(spreadsheet(ws))
    assert tab.lookup is None
    assert tab.worksheet is ws
    assert len(tab.problems) == 1
    assert "no tests listed" in tab.problems[0]


@pytest.mark.parametrize("missing", ["Code", "Aliases"])
def test_missing_required_column_gives_no_lookup_and_names_it(missing):
    headers = [h for h in HEADERS if h != missing]
    ws = FakeWorksheet([headers, ["96101-WISC-V", "WISC"][: len(headers)]])
    tab = load_services_tab(spreadsheet(ws))
    assert tab.lookup is None
    assert tab.worksheet is ws
    assert len(tab.problems) == 1
    assert f"missing the '{missing}' column" in tab.problems[0]


def test_every_unusable_tab_message_says_what_it_means_for_comments():
    for spreadsheet_ in (
        spreadsheet(),
        spreadsheet(FakeWorksheet([HEADERS])),
        spreadsheet(FakeWorksheet([["Name"], ["x"]])),
    ):
        problem = load_services_tab(spreadsheet_).problems[0]
        assert "can't be looked up" in problem


# --- Errors that should not be swallowed ----------------------------


def test_other_errors_are_not_mistaken_for_a_missing_tab():
    class Unreachable(FakeSpreadsheet):
        def worksheet(self, name):
            raise RuntimeError("network down")

    with pytest.raises(RuntimeError):
        load_services_tab(Unreachable({}))


# --- Flagging problems on the tab -----------------------------------

PROBLEM_A = "Row 5: 'Beery VMI' isn't a valid service code, so it was skipped"
PROBLEM_B = "'PSYCH' matches more than one code, so it won't be accepted"


def test_problems_are_written_as_a_note_on_the_header_cell():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    assert flag_services_tab(ws, [PROBLEM_A]) == "flagged"
    assert ws.notes[NOTE_CELL].startswith(NOTE_HEADER)
    assert NOTE_CELL == "A1"


def test_note_lists_every_problem_and_ends_with_the_clearing_hint():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    flag_services_tab(ws, [PROBLEM_A, PROBLEM_B])
    lines = ws.notes[NOTE_CELL].split("\n")
    assert lines[0] == NOTE_HEADER
    assert f"- {PROBLEM_A}" in lines
    assert f"- {PROBLEM_B}" in lines
    assert lines[-1] == NOTE_FOOTER


def test_formatting_is_never_touched():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    flag_services_tab(ws, [PROBLEM_A])
    flag_services_tab(ws, [])
    assert ws.formats == {}
    assert ws.writes == []


def test_our_note_is_cleared_once_the_table_is_clean():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    flag_services_tab(ws, [PROBLEM_A])
    assert flag_services_tab(ws, []) == "cleared"
    assert NOTE_CELL not in ws.notes


def test_clean_table_with_no_note_does_nothing():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    assert flag_services_tab(ws, []) == "unchanged"
    assert ws.notes == {}


class NoteCountingWorksheet(FakeWorksheet):
    note_writes = 0

    def update_note(self, cell, text):
        self.note_writes += 1
        super().update_note(cell, text)


def test_identical_note_is_not_rewritten():
    ws = NoteCountingWorksheet([HEADERS, ["96101-WISC-V", "WISC", ""]])
    flag_services_tab(ws, [PROBLEM_A])
    assert flag_services_tab(ws, [PROBLEM_A]) == "unchanged"
    assert ws.note_writes == 1


def test_note_is_updated_when_the_problems_change():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    flag_services_tab(ws, [PROBLEM_A])
    assert flag_services_tab(ws, [PROBLEM_A, PROBLEM_B]) == "flagged"
    assert PROBLEM_B in ws.notes[NOTE_CELL]


def test_someone_elses_note_is_left_alone_when_the_table_is_clean():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    ws.notes[NOTE_CELL] = "Ask OA before changing the Code column"
    assert flag_services_tab(ws, []) == "unchanged"
    assert ws.notes[NOTE_CELL] == "Ask OA before changing the Code column"


def test_someone_elses_note_is_not_overwritten_when_there_are_problems():
    ws = services_ws(["96101-WISC-V", "WISC", ""])
    ws.notes[NOTE_CELL] = "Ask OA before changing the Code column"
    assert flag_services_tab(ws, [PROBLEM_A]) == "skipped"
    assert ws.notes[NOTE_CELL] == "Ask OA before changing the Code column"


def test_no_tab_means_nothing_to_flag():
    assert flag_services_tab(None, [PROBLEM_A]) == "no_tab"


def test_an_unusable_tab_is_flagged_with_why():
    ws = FakeWorksheet([["Name", "Aliases"], ["Wechsler", "WISC"]])
    tab = load_services_tab(spreadsheet(ws))
    assert flag_services_tab(tab.worksheet, tab.problems) == "flagged"
    assert "missing the 'Code' column" in ws.notes[NOTE_CELL]


def test_load_then_flag_then_fix_then_flag_again():
    ws = services_ws(
        ["96101-WISC-V", "WISC", ""],
        ["Beery VMI", "", ""],
    )
    first = load_services_tab(spreadsheet(ws))
    assert flag_services_tab(first.worksheet, first.problems) == "flagged"
    assert "Beery VMI" in ws.notes[NOTE_CELL]

    ws.rows.pop()  # someone deletes the bad row
    second = load_services_tab(spreadsheet(ws))
    assert flag_services_tab(second.worksheet, second.problems) == "cleared"
    assert NOTE_CELL not in ws.notes