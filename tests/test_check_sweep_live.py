"""
Tests for the helpers in scripts/check_sweep_live.py that need no Google: the
request pacer and its retry, choosing which scenarios to run, and the small
functions that pick a case out of a report or a test out of a cell.

The live check itself needs the sandbox sheet, so it is run by hand. These tests
stop the parts around it from breaking quietly.

Run from the project root:
    python -m pytest tests/test_check_sweep_live.py -v
"""

import contextlib
import io
import sys
from types import SimpleNamespace

import pytest
from gspread.exceptions import APIError

import scripts.check_sweep_live as live


# Note: the script's functions are used as live.<name>, never imported by name,
# because pytest would collect an imported function called test_lines as a test.


# --- The pacer ------------------------------------------------------


class Clock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def make_pacer(limit=3, window=60.0):
    clock, said = Clock(), []
    return live.Pacer(limit, window, clock.time, clock.sleep, said.append), clock, said


def test_the_pacer_defaults_to_45_requests_a_minute():
    pacer = live.Pacer()
    assert (pacer.limit, pacer.window) == (45, 60.0)


def test_requests_under_the_limit_never_wait():
    pacer, clock, _ = make_pacer(limit=3)
    for _ in range(3):
        pacer.wait()
    assert clock.slept == []


def test_the_request_over_the_limit_waits_until_the_oldest_has_aged_out():
    pacer, clock, _ = make_pacer(limit=3)
    for _ in range(4):
        pacer.wait()
    assert len(clock.slept) == 1 and clock.slept[0] == pytest.approx(60.5)


def test_requests_spread_over_more_than_a_window_never_wait():
    pacer, clock, _ = make_pacer(limit=2)
    for _ in range(10):
        pacer.wait()
        clock.now += 40
    assert clock.slept == []


def test_a_long_wait_is_announced_and_a_short_one_is_not():
    pacer, clock, said = make_pacer(limit=1)
    pacer.wait()
    pacer.wait()
    assert len(said) == 1 and "pausing" in said[0]

    pacer, clock, said = make_pacer(limit=1)
    pacer.wait()
    clock.now = 58.0
    pacer.wait()
    assert said == []


def test_the_limit_holds_in_every_window_over_a_long_run():
    pacer, clock, _ = make_pacer(limit=5)
    stamps = []
    for _ in range(40):
        pacer.wait()
        stamps.append(clock.now)
    for t in stamps:
        assert sum(1 for s in stamps if t - 60 < s <= t) <= 5


def test_a_pause_is_shown_on_the_real_screen_not_in_captured_output(monkeypatch):
    screen = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", screen)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        live.say_on_screen("(pausing 51 s)")
    assert captured.getvalue() == ""                   # nothing leaks into the sweep's captured output
    assert screen.getvalue() == "(pausing 51 s)\n"


class BrokenScreen:
    """A console that can't be written to, as the real one can't be under a test runner on Windows."""

    def write(self, text):
        raise OSError("[WinError 6] The handle is invalid")

    def flush(self):
        raise OSError("[WinError 6] The handle is invalid")


def test_a_pause_that_cannot_be_shown_does_not_stop_the_run(monkeypatch, capsys):
    monkeypatch.setattr(sys, "__stdout__", BrokenScreen())
    live.say_on_screen("(pausing 51 s)")                # does not raise
    assert "(pausing 51 s)" in capsys.readouterr().err


class ScreenThatFailsWhenFlushed:
    """Takes the text into a buffer, and only fails when it is actually written out."""

    def write(self, text):
        return len(text)

    def flush(self):
        raise OSError("[WinError 6] The handle is invalid")


def test_a_screen_that_fails_only_when_flushed_does_not_lose_the_message(monkeypatch, capsys):
    monkeypatch.setattr(sys, "__stdout__", ScreenThatFailsWhenFlushed())
    live.say_on_screen("(pausing 51 s)")
    assert "(pausing 51 s)" in capsys.readouterr().err


def test_a_pause_with_no_screen_at_all_goes_to_stderr(monkeypatch, capsys):
    monkeypatch.setattr(sys, "__stdout__", None)
    live.say_on_screen("(pausing 51 s)")
    assert "(pausing 51 s)" in capsys.readouterr().err


# --- The retry ------------------------------------------------------


def api_error(status):
    """An APIError with the given status, without needing a real HTTP response."""
    error = APIError.__new__(APIError)
    Exception.__init__(error, f"[{status}]")
    error.response = SimpleNamespace(status_code=status)
    return error


class FakeClient:
    def __init__(self, behaviours):
        self.behaviours, self.calls = list(behaviours), 0

    def request(self, *args, **kwargs):
        self.calls += 1
        what = self.behaviours.pop(0) if self.behaviours else "ok"
        if isinstance(what, Exception):
            raise what
        return (what, args, kwargs)


def paced_client(behaviours, retries=3):
    client = FakeClient(behaviours)
    pacer, _, said = make_pacer(limit=1000)
    waited = []
    live.install_pacing(client, pacer, retries=retries, pause=30.0, sleep=waited.append, say=said.append)
    return client, pacer, waited, said


def test_a_request_passes_through_with_its_arguments_and_result():
    client, _, _, _ = paced_client(["ok"])
    assert client.request("get", "url", params=1) == ("ok", ("get", "url"), {"params": 1})


def test_every_request_goes_through_the_pacer():
    client, pacer, _, _ = paced_client([])
    for _ in range(7):
        client.request()
    assert len(pacer.times) == 7


def test_a_refusal_for_being_over_the_limit_is_tried_again_after_a_pause():
    client, _, waited, said = paced_client([api_error(429), api_error(429), "fine"])
    assert client.request()[0] == "fine"
    assert client.calls == 3 and waited == [30.0, 30.0]
    assert len(said) == 2 and "limit was hit" in said[0]


def test_another_error_is_raised_at_once_without_a_retry():
    client, _, waited, _ = paced_client([api_error(500)])
    with pytest.raises(APIError):
        client.request()
    assert client.calls == 1 and waited == []


def test_after_the_retries_are_used_up_the_error_is_raised():
    client, _, waited, _ = paced_client([api_error(429)] * 5, retries=2)
    with pytest.raises(APIError):
        client.request()
    assert client.calls == 3 and waited == [30.0, 30.0]


def test_only_a_429_counts_as_over_the_limit():
    assert live.is_over_the_limit(api_error(429)) is True
    assert live.is_over_the_limit(api_error(403)) is False
    assert live.is_over_the_limit(ValueError("x")) is False


# --- Choosing what to run -------------------------------------------

GROUP_NAMES = ["chain", "flags", "nothing-stored", "rewrites", "moves", "interrupted-move"]


def test_every_scenario_the_script_covers_belongs_to_exactly_one_group():
    # 17 (the pause) and 18 (the lock) are not in the script. They stay in the manual check.
    numbers = [n for _, scenarios, _ in live.GROUPS for n in scenarios]
    assert sorted(numbers) == list(range(1, 17)) + [19]


@pytest.mark.parametrize("only, expected", [
    (None, GROUP_NAMES),
    ("", GROUP_NAMES),
    ("6", ["rewrites"]),
    ("7,9", ["rewrites"]),
    ("15", ["moves"]),
    ("19", ["interrupted-move"]),
    ("16,19", ["moves", "interrupted-move"]),
    ("6,15", ["rewrites", "moves"]),
    ("15,6", ["rewrites", "moves"]),                   # always in the usual order
    ("moves", ["moves"]),
    ("nothing-stored", ["nothing-stored"]),
    ("1,4,12", ["chain", "flags", "nothing-stored"]),
    (" 6 , moves ", ["rewrites", "moves"]),
])
def test_choosing_groups(only, expected):
    assert [name for name, _, _ in live.choose_groups(only)] == expected


@pytest.mark.parametrize("bad", ["99", "0", "six", "moves,99", "chain;flags"])
def test_something_that_is_not_a_scenario_or_group_is_refused(bad):
    with pytest.raises(ValueError, match="is not a scenario number or a group name"):
        live.choose_groups(bad)


def test_the_error_for_a_bad_choice_lists_what_is_valid():
    with pytest.raises(ValueError) as caught:
        live.choose_groups("99")
    text = str(caught.value)
    assert "Scenarios: 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 19." in text
    assert "Groups: chain, flags, nothing-stored, rewrites, moves, interrupted-move." in text


def test_the_listing_names_every_group_and_its_scenarios():
    text = live.describe_groups()
    for name in GROUP_NAMES:
        assert name in text
    assert "scenarios 6, 7, 8, 9" in text and "--only" in text


@pytest.fixture
def no_google(monkeypatch):
    def refuse():
        raise AssertionError("reached for Google")
    monkeypatch.setattr(live, "get_client", refuse)


def test_listing_the_groups_touches_nothing(no_google, capsys):
    assert live.main(["--list"]) == 0
    assert "chain" in capsys.readouterr().out


def test_a_bad_choice_stops_before_reaching_for_google(no_google, capsys):
    assert live.main(["--only", "99"]) == 2
    assert "not a scenario number or a group name" in capsys.readouterr().out


# --- Picking parts out of a report or a cell ------------------------

REPORT = """Case S01:
  Provider: already Dr. Doe, no change

Case S04:
  Tests updated:
    add 96101-WIAT-III
  NEEDS ATTENTION:
    Add: 96101-WISC-V  (skipped)

Case S10:
  Provider: set to Dr. Doe

3 comment(s) processed, 1 field(s) updated, 2 flagged for attention."""


def test_a_cases_block_runs_from_its_line_to_the_next_cases():
    block = live.case_block(REPORT, "S04")
    assert block.startswith("Case S04:") and "add 96101-WIAT-III" in block
    assert "NEEDS ATTENTION:" in block


def test_a_block_does_not_include_the_neighbouring_cases():
    block = live.case_block(REPORT, "S04")
    assert "S01" not in block and "S10" not in block and "Provider" not in block


def test_a_case_that_is_not_in_the_report_has_an_empty_block():
    assert live.case_block(REPORT, "S99") == ""


def test_a_case_id_that_starts_the_same_is_not_confused():
    report = "Case S1:\n  one\nCase S10:\n  ten\n"
    assert live.case_block(report, "S1") == "Case S1:\n  one"
    assert live.case_block(report, "S10") == "Case S10:\n  ten"


def test_the_last_block_includes_the_summary_line():
    assert "3 comment(s) processed" in live.case_block(REPORT, "S10")


def test_the_tests_in_a_cell_are_its_lines_without_separators_or_blanks():
    cell = "1. a\n____________________\n2. b\n\n  \n____ ____\n3. c"
    assert live.test_lines(cell) == ["1. a", "2. b", "3. c"]


def test_a_cell_is_built_from_its_tests_with_a_separator_between():
    assert live.build_cell("____", ["1. a", "2. b", "3. c"]) == "1. a\n____\n2. b\n____\n3. c"
    assert live.build_cell("____", ["1. a"]) == "1. a"


def test_a_number_is_stripped_from_the_front_of_a_test_line():
    assert live.strip_number("4. 96101-WIAT-III - text") == "96101-WIAT-III - text"
    assert live.strip_number("no number here") == "no number here"


def test_rows_by_id_skips_the_header_and_blank_rows_and_ignores_spaces():
    table = [["Case ID", "x"], ["S2", ""], ["", ""], [" S1 ", ""], ["S3"]]
    assert live.rows_by_id(table) == {"S2": 2, " S1 ": 4, "S3": 5}


def test_a_row_is_padded_to_the_headers_and_a_missing_row_is_blank():
    table = [live.ONGOING_HEADERS, ["S01", "John Doe"]]
    row = live.row_of(table, 2)
    assert row["Case ID"] == "S01" and row["Comments"] == "" and set(row) == set(live.ONGOING_HEADERS)
    assert live.row_of(table, 9) == {h: "" for h in live.ONGOING_HEADERS}


# --- The expected lines tie to the real cell layout ------------------


def test_the_expected_lines_match_how_a_cell_is_really_laid_out():
    env = live.build_env(None, [], None, "unused")
    assert len(env.original_lines) == 3
    assert env.separator and env.separator.strip("_") == ""
    assert live.build_cell(env.separator, env.original_lines) == env.plain
    assert env.cars4.startswith("4. ") and env.cars5.startswith("5. ")
    assert env.wais_swapped.endswith("(changed from 96101-WISC-V)")


# --- The helpers for an interrupted move ----------------------------


class Worksheet:
    def __init__(self):
        self.title, self.deleted = "tab", []

    def get_all_values(self):
        return [["Case ID"], ["S1"]]

    def delete_rows(self, row):
        self.deleted.append(row)


def test_a_worksheet_that_fails_to_delete_is_otherwise_the_one_it_wraps():
    real = Worksheet()
    wrapped = live.FailsToDelete(real)
    assert wrapped.title == "tab"
    assert wrapped.get_all_values() == [["Case ID"], ["S1"]]


def test_a_worksheet_that_fails_to_delete_does_not_delete():
    real = Worksheet()
    with pytest.raises(RuntimeError, match="API error"):
        live.FailsToDelete(real).delete_rows(2)
    assert real.deleted == []


def test_the_log_catcher_keeps_the_warnings_of_one_logger_until_it_is_closed():
    import logging
    logger = logging.getLogger("tests.log_catcher")
    catcher = live.LogCatcher("tests.log_catcher")
    logger.warning("first %s", "one")
    logger.info("not kept")
    catcher.close()
    logger.warning("after closing")
    assert catcher.messages == ["first one"]