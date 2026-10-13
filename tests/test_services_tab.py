"""
Tests for app/services_tab.py: loading the Services tab into a lookup,
and carrying on without one when the tab can't be used.

Uses the shared in-memory fakes from tests/fakes.py, so no Google
Sheet is involved.

Run from the project root:
    python -m pytest tests/test_services_tab.py -v
"""

import pytest

from app.services_tab import SERVICES_TAB, load_services_tab
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