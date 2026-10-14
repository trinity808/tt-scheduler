"""
Tests for run_sweep() in app/apply_comment_updates.py: loading the
Services tab, flagging it, and sweeping the Ongoing tab with the lookup,
all in one call.

Uses the shared in-memory fakes from tests/fakes.py, so no Google Sheet
is involved.

Run from the project root:
    python -m pytest tests/test_run_sweep.py -v
"""

import pytest
from gspread.exceptions import WorksheetNotFound

from app.apply_comment_updates import ONGOING_TAB, run_sweep
from app.comment_parser import ServiceChange
from app.services_tab import NOTE_CELL, SERVICES_TAB
from tests.fakes import FakeSpreadsheet, FakeWorksheet


ONGOING_HEADERS = ["Case ID", "Provider", "Psychometrist", "Comments"]
SERVICES_HEADERS = ["Code", "Aliases", "Name"]

GOOD_ROWS = [
    ["96101-WISC-V", "WISC", ""],
    ["96130-CARS-2", "CARS", ""],
]
BAD_ROW = ["Beery VMI", "", ""]  # not a valid service code
CARS = "96130-CARS-2"


def ongoing(*rows):
    return FakeWorksheet([ONGOING_HEADERS, *rows])


def services(*rows):
    return FakeWorksheet([SERVICES_HEADERS, *rows])


def spreadsheet(ongoing_ws, services_ws=None):
    tabs = {ONGOING_TAB: ongoing_ws}
    if services_ws is not None:
        tabs[SERVICES_TAB] = services_ws
    return FakeSpreadsheet(tabs)


# --- A clean run ----------------------------------------------------


def test_clean_run_resolves_test_changes_and_leaves_the_services_tab_alone():
    ws = ongoing(["15419897", "", "", "Add: cars"])
    svc = services(*GOOD_ROWS)
    report = run_sweep(spreadsheet(ws, svc))

    assert report.results[0]["parsed"].service_changes == [ServiceChange("add", CARS)]
    assert report.services.lookup is not None
    assert report.services.problems == []
    assert report.services_flag == "unchanged"
    assert "D2" not in ws.notes
    assert svc.notes == {}


def test_services_tab_only_ever_gets_a_note_never_cells_or_formatting():
    svc = services(*GOOD_ROWS, BAD_ROW)
    run_sweep(spreadsheet(ongoing(["15419897", "", "", "Add: cars"]), svc))
    assert svc.writes == []
    assert svc.formats == {}


# --- Problems with the Services tab, sweep carries on ----------------


def test_problem_rows_are_flagged_on_the_tab_and_the_sweep_still_runs():
    ws = ongoing(["15419897", "", "", "Provider: Dr. Shelton\nAdd: cars"])
    svc = services(*GOOD_ROWS, BAD_ROW)
    report = run_sweep(spreadsheet(ws, svc))

    assert report.services_flag == "flagged"
    assert "Beery VMI" in svc.notes[NOTE_CELL]
    # The lookup is still usable, so the sweep resolves test changes too.
    assert ws.value(2, "Provider") == "Dr. Shelton"
    assert report.results[0]["parsed"].service_changes == [ServiceChange("add", CARS)]


def test_missing_services_tab_degrades_without_stopping_the_sweep():
    ws = ongoing(["15419897", "", "", "Provider: Dr. Shelton\nAdd: cars"])
    report = run_sweep(spreadsheet(ws))

    assert report.services.lookup is None
    assert report.services_flag == "no_tab"
    assert ws.value(2, "Provider") == "Dr. Shelton"
    assert "services list not loaded" in ws.notes["D2"]


def test_unusable_services_tab_is_flagged_and_the_sweep_degrades():
    ws = ongoing(["15419897", "", "", "Provider: Dr. Shelton\nAdd: cars"])
    svc = FakeWorksheet([["Name", "Aliases"], ["Wechsler", "WISC"]])  # no Code column
    report = run_sweep(spreadsheet(ws, svc))

    assert report.services.lookup is None
    assert report.services_flag == "flagged"
    assert "missing the 'Code' column" in svc.notes[NOTE_CELL]
    assert ws.value(2, "Provider") == "Dr. Shelton"
    assert "services list not loaded" in ws.notes["D2"]


def test_someone_elses_note_on_the_services_tab_is_reported_as_skipped():
    ws = ongoing(["15419897", "", "", "Add: cars"])
    svc = services(*GOOD_ROWS, BAD_ROW)
    svc.notes[NOTE_CELL] = "my own note"
    report = run_sweep(spreadsheet(ws, svc))

    assert report.services_flag == "skipped"
    assert svc.notes[NOTE_CELL] == "my own note"
    assert report.results[0]["parsed"].service_changes == [ServiceChange("add", CARS)]


# --- Across several runs --------------------------------------------


def test_services_flag_is_set_once_then_clears_when_the_tab_is_fixed():
    svc = services(*GOOD_ROWS, BAD_ROW)
    sheet = spreadsheet(ongoing(["15419897", "", "", "Add: cars"]), svc)

    assert run_sweep(sheet).services_flag == "flagged"
    assert run_sweep(sheet).services_flag == "unchanged"  # not rewritten

    svc.rows.pop()  # someone deletes the bad row
    assert run_sweep(sheet).services_flag == "cleared"
    assert NOTE_CELL not in svc.notes


def test_repeat_runs_make_no_redundant_writes_to_ongoing():
    ws = ongoing(["15419897", "", "", "Provider: Dr. Shelton"])
    sheet = spreadsheet(ws, services(*GOOD_ROWS))

    run_sweep(sheet)
    assert ws.writes != []

    ws.writes = []
    run_sweep(sheet)
    assert ws.writes == []


# --- What should stop the run ---------------------------------------


def test_missing_ongoing_tab_stops_the_run():
    sheet = FakeSpreadsheet({SERVICES_TAB: services(*GOOD_ROWS)})
    with pytest.raises(WorksheetNotFound):
        run_sweep(sheet)