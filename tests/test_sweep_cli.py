"""
Tests for the command line in app/apply_comment_updates.py: main().

The sweep itself is tested elsewhere. These check what the command adds:
it previews unless told to write, it prints the report, and it waits for a
key only when someone is at a terminal, so a scheduled run never hangs.

Run from the project root:
    python -m pytest tests/test_sweep_cli.py -v
"""

import pytest

from app.apply_comment_updates import ONGOING_TAB, main
from app.case_metadata import set_case_tests
from app.services_cell import ServiceLine, render_services_cell
from tests.fake_metadata import FakeMetadataClient
from tests.fakes import FakeSpreadsheet, FakeWorksheet


WISC = "96101-WISC-V"
PSYCH = "90791"
CARS = "96130-CARS-2"

HEADERS = ["Case ID", "Provider", "Psychometrist", "Services Requested", "Price", "Comments"]
CELL = render_services_cell([
    ServiceLine(WISC, "Wechsler Intelligence Scale for Children"),
    ServiceLine(PSYCH, "Psychiatric Exam"),
])


def setup():
    """A sheet with one case asking for a provider and a new test, and its client."""
    ongoing = FakeWorksheet([
        HEADERS,
        ["111", "", "", CELL, "484.00", "Provider: Dr. Doe\nAdd: cars"],
    ])
    services = FakeWorksheet([
        ["Code", "Aliases", "Name"],
        [WISC, "WISC", "Wechsler Intelligence Scale for Children"],
        [PSYCH, "Psychiatric Exam", "Psychiatric Exam"],
        [CARS, "CARS", "Childhood Autism Rating Scale"],
    ])
    spreadsheet = FakeSpreadsheet({ONGOING_TAB: ongoing, "Services": services})
    client = FakeMetadataClient()
    set_case_tests(client, 2, "111", [WISC, PSYCH])
    return spreadsheet, ongoing, client


class Terminal:
    """Stands in for sys.stdin, saying whether someone is at a terminal."""

    def __init__(self, at_terminal):
        self._at_terminal = at_terminal

    def isatty(self):
        return self._at_terminal


# --- Preview by default ---------------------------------------------


def test_by_default_it_only_previews(capsys):
    spreadsheet, ongoing, client = setup()
    main([], spreadsheet=spreadsheet, metadata=client, pause=False)

    assert ongoing.writes == []
    assert ongoing.notes == {}
    out = capsys.readouterr().out
    assert "DRY RUN: nothing was written to the sheet." in out
    assert "python -m app.apply_comment_updates --live" in out
    assert "Provider: would be set to Dr. Doe" in out


def test_live_writes_the_changes(capsys):
    spreadsheet, ongoing, client = setup()
    main(["--live"], spreadsheet=spreadsheet, metadata=client, pause=False)

    assert ongoing.value(2, "Provider") == "Dr. Doe"
    assert "(added)" in ongoing.value(2, "Services Requested")
    out = capsys.readouterr().out
    assert "DRY RUN" not in out
    assert "Provider: set to Dr. Doe" in out
    assert f"add {CARS}" in out


def test_a_preview_followed_by_live_does_what_the_preview_said(capsys):
    spreadsheet, ongoing, client = setup()
    main([], spreadsheet=spreadsheet, metadata=client, pause=False)
    preview = capsys.readouterr().out
    assert f"add {CARS}" in preview and ongoing.writes == []

    main(["--live"], spreadsheet=spreadsheet, metadata=client, pause=False)
    assert CARS in ongoing.value(2, "Services Requested")


def test_running_live_twice_changes_nothing_the_second_time(capsys):
    spreadsheet, ongoing, client = setup()
    main(["--live"], spreadsheet=spreadsheet, metadata=client, pause=False)
    writes = len(ongoing.writes)
    capsys.readouterr()

    main(["--live"], spreadsheet=spreadsheet, metadata=client, pause=False)

    assert len(ongoing.writes) == writes
    assert "Tests: already up to date, no change" in capsys.readouterr().out


def test_an_unknown_option_is_rejected(capsys):
    spreadsheet, ongoing, client = setup()
    with pytest.raises(SystemExit):
        main(["--lve"], spreadsheet=spreadsheet, metadata=client, pause=False)
    assert ongoing.writes == []


# --- The pause ------------------------------------------------------


@pytest.fixture
def typed(monkeypatch):
    """Records every prompt shown by input()."""
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt))
    return prompts


def test_it_does_not_wait_when_nobody_is_at_a_terminal(monkeypatch, typed):
    monkeypatch.setattr("sys.stdin", Terminal(False))
    spreadsheet, _, client = setup()
    main([], spreadsheet=spreadsheet, metadata=client)
    assert typed == []


def test_it_waits_for_a_key_when_someone_is_at_a_terminal(monkeypatch, typed):
    monkeypatch.setattr("sys.stdin", Terminal(True))
    spreadsheet, _, client = setup()
    main([], spreadsheet=spreadsheet, metadata=client)
    assert len(typed) == 1
    assert "Press Enter to close" in typed[0]


def test_the_pause_can_be_forced_either_way(monkeypatch, typed):
    monkeypatch.setattr("sys.stdin", Terminal(False))
    spreadsheet, _, client = setup()
    main([], spreadsheet=spreadsheet, metadata=client, pause=True)
    assert len(typed) == 1

    monkeypatch.setattr("sys.stdin", Terminal(True))
    main([], spreadsheet=spreadsheet, metadata=client, pause=False)
    assert len(typed) == 1


# --- The real client ------------------------------------------------


def test_without_a_client_given_it_builds_one_from_the_ongoing_tab(monkeypatch, capsys):
    built = []

    class RecordingClient(FakeMetadataClient):
        def __init__(self, worksheet):
            super().__init__()
            built.append(worksheet)

    monkeypatch.setattr("app.sheets_metadata_client.SheetsMetadataClient", RecordingClient)
    spreadsheet, ongoing, _ = setup()

    main([], spreadsheet=spreadsheet, pause=False)

    assert built == [ongoing]