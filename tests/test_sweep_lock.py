"""
Tests for the lock the sweep's command line takes: app/apply_comment_updates.py,
main(). The lock itself is tested in test_run_lock.py. These check how the
sweep uses it:

- A live run holds sweep.lock while it works, and always gives it back.
- A previewing run takes no lock and is never blocked.
- A second live run that finds the lock says so and does nothing, and that is
  not a failure: it exits successfully, with no alert.
- An old lock is taken over, with a warning. How old is a setting.
- A lock that can't be created is a failure, not something to ignore.

Each test runs in its own scratch folder, where sweep.lock lives.

Run from the project root:
    python -m pytest tests/test_sweep_lock.py -v
"""

import json
import os
import threading
import time

import pytest

from app.apply_comment_updates import ONGOING_TAB, SweepReport, main
from app.case_metadata import set_case_tests
from app.run_lock import acquire
from app.services_cell import ServiceLine, render_services_cell
from app.services_tab import ServicesTab
from tests.fake_metadata import FakeMetadataClient
from tests.fakes import FakeSpreadsheet, FakeWorksheet
from tests.log_cleanup import clean_logger  # noqa: F401


WISC = "96101-WISC-V"
PSYCH = "90791"
CARS = "96130-CARS-2"

HEADERS = ["Case ID", "Provider", "Psychometrist", "Services Requested", "Price", "Comments"]
CELL = render_services_cell([
    ServiceLine(WISC, "Wechsler Intelligence Scale for Children"),
    ServiceLine(PSYCH, "Psychiatric Exam"),
])
COMMENT = "Provider: Dr. Doe\nAdd: cars"


def world():
    ongoing = FakeWorksheet([HEADERS, ["111", "", "", CELL, "484.00", COMMENT]])
    services = FakeWorksheet([
        ["Code", "Aliases", "Name"],
        [WISC, "WISC", "Wechsler Intelligence Scale for Children"],
        [PSYCH, "Psychiatric Exam", "Psychiatric Exam"],
        [CARS, "CARS", "Childhood Autism Rating Scale"],
    ])
    client = FakeMetadataClient()
    set_case_tests(client, 2, "111", [WISC, PSYCH])
    return FakeSpreadsheet({ONGOING_TAB: ongoing, "Services": services}), ongoing, client


@pytest.fixture(autouse=True)
def scratch_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def alerts(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.apply_comment_updates.notify_error",
        lambda subject, message, config=None, exc_info=None: calls.append(subject),
    )
    return calls


def config_for(tmp_path, **extra):
    return {"health_check": {"log_folder": str(tmp_path / "logs")}, **extra}


def run(tmp_path, args, config=None, **kwargs):
    spreadsheet, ongoing, client = world()
    result = main(args, spreadsheet=spreadsheet, metadata=client, pause=False,
                  config=config if config is not None else config_for(tmp_path), **kwargs)
    return ongoing, result


def log(tmp_path, name="sweep.log"):
    return (tmp_path / "logs" / name).read_text(encoding="utf-8")


def hold_lock(minutes_old=0):
    lock = acquire("sweep.lock", "live", 15 * 60)
    if minutes_old:
        then = time.time() - minutes_old * 60
        os.utime("sweep.lock", (then, then))
    return lock


def read_lock():
    with open("sweep.lock", encoding="utf-8") as handle:
        return json.load(handle)


def quiet_report(dry_run=False):
    return SweepReport(results=[], services=ServicesTab(), services_flag="unchanged", dry_run=dry_run)


def watching_the_sweep(monkeypatch, seen):
    """Replace the sweep itself with one that notes what the lock looks like."""
    def sweep(spreadsheet, metadata, dry_run=False):
        seen["exists"] = os.path.exists("sweep.lock")
        seen["info"] = read_lock() if seen["exists"] else None
        return quiet_report(dry_run)

    monkeypatch.setattr("app.apply_comment_updates.run_sweep", sweep)


# --- A live run holds the lock, and always gives it back ------------


def test_a_live_run_holds_the_lock_while_it_works(tmp_path, monkeypatch):
    seen = {}
    watching_the_sweep(monkeypatch, seen)
    run(tmp_path, ["--live"])
    assert seen["exists"]
    assert seen["info"]["mode"] == "live"
    assert seen["info"]["pid"] == os.getpid()
    assert (tmp_path / "sweep.lock").parent == tmp_path  # in the working directory


def test_the_lock_is_gone_when_a_live_run_finishes(tmp_path):
    run(tmp_path, ["--live"])
    assert not os.path.exists("sweep.lock")


def test_the_lock_is_gone_after_a_failed_live_run_and_the_failure_is_still_raised(tmp_path, monkeypatch):
    def failing(spreadsheet, metadata, dry_run=False):
        raise RuntimeError("sheet unreachable")

    monkeypatch.setattr("app.apply_comment_updates.run_sweep", failing)
    with pytest.raises(RuntimeError, match="sheet unreachable"):
        run(tmp_path, ["--live"])
    assert not os.path.exists("sweep.lock")
    assert "Sweep failed (live)" in log(tmp_path, "sweep_error.log")


def test_two_live_runs_one_after_the_other_both_go_ahead(tmp_path):
    first, _ = run(tmp_path, ["--live"])
    second, _ = run(tmp_path, ["--live"])
    assert first.value(2, "Provider") == "Dr. Doe"
    assert second.value(2, "Provider") == "Dr. Doe"
    assert log(tmp_path).count("Sweep started (live)") == 2


# --- Previews take no lock and are never blocked --------------------


def test_a_preview_never_takes_the_lock(tmp_path, monkeypatch):
    seen = {}
    watching_the_sweep(monkeypatch, seen)
    run(tmp_path, [])
    assert seen["exists"] is False


def test_a_preview_is_not_blocked_by_a_live_runs_lock(tmp_path, capsys):
    held = hold_lock()
    run(tmp_path, [])
    assert "DRY RUN" in capsys.readouterr().out
    assert read_lock()["token"] == held.token   # untouched


# --- A second live run backs off ------------------------------------


def test_a_live_run_is_blocked_by_a_fresh_lock(tmp_path):
    held = hold_lock()
    ongoing, _ = run(tmp_path, ["--live"])
    assert ongoing.writes == []
    assert ongoing.notes == {}
    assert read_lock()["token"] == held.token   # the holder's lock is untouched


def test_a_blocked_run_exits_successfully(tmp_path, alerts):
    hold_lock()
    _, result = run(tmp_path, ["--live"])    # no exception, no SystemExit
    assert result is None


def test_a_blocked_run_says_so_on_the_console_and_in_the_log(tmp_path, capsys):
    hold_lock()
    run(tmp_path, ["--live"])
    out = capsys.readouterr().out
    assert f"Another live sweep is already running (held by process {os.getpid()} (live)" in out
    assert "so this run did nothing" in out
    text = log(tmp_path)
    assert "WARNING" in text
    assert "Sweep skipped (live): another sweep is running (held by process" in text


def test_a_blocked_run_logs_no_start_no_report_and_no_finish(tmp_path):
    hold_lock()
    run(tmp_path, ["--live"])
    text = log(tmp_path)
    assert "Sweep started" not in text
    assert "Sweep report" not in text
    assert "Sweep finished" not in text


def test_a_blocked_run_is_not_a_failure_so_it_sends_no_alert(tmp_path, alerts):
    hold_lock()
    run(tmp_path, ["--live"])
    assert alerts == []
    assert not (tmp_path / "logs" / "sweep_error.log").read_text(encoding="utf-8").strip()


def test_a_blocked_run_does_not_wait_for_a_key(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt))
    hold_lock()
    spreadsheet, _, client = world()
    main(["--live", "--pause"], spreadsheet=spreadsheet, metadata=client,
         config=config_for(tmp_path))
    assert asked == []


def test_two_live_runs_at_the_same_time_only_one_goes_ahead(tmp_path, monkeypatch):
    entered, let_go = threading.Event(), threading.Event()
    errors = []

    def slow_sweep(spreadsheet, metadata, dry_run=False):
        entered.set()
        assert let_go.wait(10)
        return quiet_report(dry_run)

    monkeypatch.setattr("app.apply_comment_updates.run_sweep", slow_sweep)

    def first_run():
        try:
            run(tmp_path, ["--live"])
        except Exception as error:  # reported below, since a thread can't raise into the test
            errors.append(error)

    first = threading.Thread(target=first_run)
    first.start()
    assert entered.wait(10)
    run(tmp_path, ["--live"])        # the second run, while the first is still working
    let_go.set()
    first.join(10)

    assert errors == []
    text = log(tmp_path)
    assert text.count("Sweep started (live)") == 1
    assert text.count("Sweep skipped (live)") == 1
    assert not os.path.exists("sweep.lock")


# --- Old locks ------------------------------------------------------


def test_a_stale_lock_is_taken_over_and_the_run_goes_ahead(tmp_path):
    hold_lock(minutes_old=16)
    ongoing, _ = run(tmp_path, ["--live"])
    assert ongoing.value(2, "Provider") == "Dr. Doe"
    text = log(tmp_path)
    assert "Took over a stale sweep lock (held by process" in text
    assert "16.0 minutes old" in text
    assert "older than the 15-minute limit" in text
    assert not os.path.exists("sweep.lock")


def test_a_lock_just_under_the_limit_still_blocks(tmp_path):
    held = hold_lock(minutes_old=14)
    ongoing, _ = run(tmp_path, ["--live"])
    assert ongoing.writes == []
    assert read_lock()["token"] == held.token


def test_the_limit_comes_from_the_settings(tmp_path):
    hold_lock(minutes_old=2)
    ongoing, _ = run(tmp_path, ["--live"], config=config_for(tmp_path, sweep_lock_stale_minutes=1))
    assert ongoing.value(2, "Provider") == "Dr. Doe"      # 2 minutes is older than 1


def test_a_longer_limit_in_the_settings_blocks_a_lock_that_would_otherwise_be_old(tmp_path):
    held = hold_lock(minutes_old=20)
    ongoing, _ = run(tmp_path, ["--live"], config=config_for(tmp_path, sweep_lock_stale_minutes=30))
    assert ongoing.writes == []
    assert read_lock()["token"] == held.token


def test_an_invalid_limit_is_warned_about_and_fifteen_minutes_is_used(tmp_path):
    held = hold_lock(minutes_old=10)
    ongoing, _ = run(tmp_path, ["--live"], config=config_for(tmp_path, sweep_lock_stale_minutes=0))
    assert ongoing.writes == []                            # 15 applied, so 10 minutes still blocks
    assert read_lock()["token"] == held.token
    assert "sweep_lock_stale_minutes must be a positive number" in log(tmp_path)
    assert "so 15 is used" in log(tmp_path)


def test_a_run_that_was_taken_over_does_not_delete_its_successors_lock(tmp_path, monkeypatch):
    def sweep_that_is_taken_over(spreadsheet, metadata, dry_run=False):
        with open("sweep.lock", "w", encoding="utf-8") as handle:
            json.dump({"pid": 999, "mode": "live", "token": "someone-elses"}, handle)
        return quiet_report(dry_run)

    monkeypatch.setattr("app.apply_comment_updates.run_sweep", sweep_that_is_taken_over)
    run(tmp_path, ["--live"])
    assert read_lock()["token"] == "someone-elses"
    assert "no longer this run's when it finished" in log(tmp_path)


# --- When the lock itself goes wrong --------------------------------


def test_a_lock_that_cannot_be_created_fails_the_run_visibly(tmp_path, monkeypatch, alerts):
    def cannot_create(*args, **kwargs):
        raise OSError("read-only folder")

    monkeypatch.setattr("app.apply_comment_updates.acquire", cannot_create)
    spreadsheet, ongoing, client = world()
    with pytest.raises(OSError, match="read-only folder"):
        main(["--live"], spreadsheet=spreadsheet, metadata=client, pause=False,
             config=config_for(tmp_path))
    assert ongoing.writes == []
    assert alerts == ["Sweep failed"]
    assert "Sweep failed (live)" in log(tmp_path, "sweep_error.log")


def test_a_failure_to_give_the_lock_back_does_not_hide_the_real_error(tmp_path, monkeypatch):
    def failing(spreadsheet, metadata, dry_run=False):
        raise ValueError("boom")

    def cannot_release(lock):
        raise OSError("file in use")

    monkeypatch.setattr("app.apply_comment_updates.run_sweep", failing)
    monkeypatch.setattr("app.apply_comment_updates.release", cannot_release)
    with pytest.raises(ValueError, match="boom"):
        run(tmp_path, ["--live"])
    assert "Could not release the sweep lock" in log(tmp_path)


def test_a_failure_to_give_the_lock_back_after_a_good_run_is_logged_and_not_raised(tmp_path, monkeypatch):
    def cannot_release(lock):
        raise OSError("file in use")

    monkeypatch.setattr("app.apply_comment_updates.release", cannot_release)
    ongoing, _ = run(tmp_path, ["--live"])      # does not raise
    assert ongoing.value(2, "Provider") == "Dr. Doe"
    assert "Could not release the sweep lock" in log(tmp_path)