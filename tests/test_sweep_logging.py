"""
Tests for the logging the sweep's command line does: app/apply_comment_updates.py,
main().

A run writes logs/sweep.log (and sweep_error.log for failures): when it started
and in which mode, the whole report as one record, and when it finished. A
failure is logged with its traceback, the alert is called, and the error is
raised again so the process still exits with an error.

The logger doesn't pass records up to pytest, so these read the log files, and
attach a small collector to see the records themselves.

Run from the project root:
    python -m pytest tests/test_sweep_logging.py -v
"""

import logging
import re

import pytest
from gspread.exceptions import WorksheetNotFound

from app.apply_comment_updates import ONGOING_TAB, main
from app.case_metadata import set_case_tests
from app.health_monitor import get_logger
from app.services_cell import ServiceLine, render_services_cell
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


def world(comment="Provider: Dr. Doe\nAdd: cars"):
    ongoing = FakeWorksheet([HEADERS, ["111", "", "", CELL, "484.00", comment]])
    services = FakeWorksheet([
        ["Code", "Aliases", "Name"],
        [WISC, "WISC", "Wechsler Intelligence Scale for Children"],
        [PSYCH, "Psychiatric Exam", "Psychiatric Exam"],
        [CARS, "CARS", "Childhood Autism Rating Scale"],
    ])
    spreadsheet = FakeSpreadsheet({ONGOING_TAB: ongoing, "Services": services})
    client = FakeMetadataClient()
    set_case_tests(client, 2, "111", [WISC, PSYCH])
    return spreadsheet, client


def config_for(folder):
    return {"health_check": {"log_folder": str(folder)}}


def run(args, folder, comment="Provider: Dr. Doe\nAdd: cars", **kwargs):
    spreadsheet, client = world(comment)
    main(args, spreadsheet=spreadsheet, metadata=client, pause=False,
         config=config_for(folder), **kwargs)


def read(folder, name="sweep.log"):
    return (folder / name).read_text(encoding="utf-8")


class Collector(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture
def records():
    collector = Collector()
    get_logger().addHandler(collector)
    yield collector.records
    get_logger().removeHandler(collector)


@pytest.fixture
def alerts(monkeypatch):
    """Replaces the alert email with something that records how it was called."""
    calls = []
    monkeypatch.setattr(
        "app.apply_comment_updates.notify_error",
        lambda subject, message, config=None, exc_info=None: calls.append(
            {"subject": subject, "message": message, "config": config, "exc_info": exc_info}
        ),
    )
    return calls


@pytest.fixture(autouse=True)
def scratch_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


# --- A normal run ---------------------------------------------------


def test_a_live_run_logs_when_it_started_what_it_found_and_when_it_finished(tmp_path):
    run(["--live"], tmp_path)
    text = read(tmp_path)
    assert "Sweep started (live)" in text
    assert "Sweep report (live):" in text
    assert "Provider: set to Dr. Doe" in text
    assert "Sweep finished (live): 1 comment(s), 1 field(s) updated, 0 flagged" in text
    assert text.index("Sweep started") < text.index("Sweep report") < text.index("Sweep finished")


def test_a_preview_is_labeled_as_one_everywhere(tmp_path):
    run([], tmp_path)
    text = read(tmp_path)
    assert "Sweep started (preview)" in text
    assert "Sweep report (preview):" in text
    assert "Sweep finished (preview):" in text
    assert "would be updated" in text
    assert "(live)" not in text


def test_the_report_is_one_record_not_one_per_line(tmp_path, records):
    run(["--live"], tmp_path)
    reports = [r for r in records if r.getMessage().startswith("Sweep report")]
    assert len(reports) == 1
    message = reports[0].getMessage()
    assert "Case 111:" in message and "Provider: set to Dr. Doe" in message
    assert message.count("\n") >= 3


def test_the_finish_line_ends_with_how_long_it_took(tmp_path):
    run(["--live"], tmp_path)
    assert re.search(r"Sweep finished \(live\): .*, \d+\.\ds$", read(tmp_path), re.MULTILINE)


def test_the_report_is_still_printed(tmp_path, capsys):
    run(["--live"], tmp_path)
    out = capsys.readouterr().out
    assert "Provider: set to Dr. Doe" in out
    assert "Sweep started" not in out


def test_an_idle_run_still_leaves_a_trace(tmp_path):
    run(["--live"], tmp_path, comment="")
    text = read(tmp_path)
    assert "Sweep started (live)" in text
    assert "No rows had a comment to process." in text
    assert "Sweep finished (live): 0 comment(s), 0 field(s) updated, 0 flagged" in text


# --- How loud it is -------------------------------------------------


def test_a_clean_run_is_logged_at_info(tmp_path, records):
    run(["--live"], tmp_path)
    report = next(r for r in records if r.getMessage().startswith("Sweep report"))
    assert report.levelno == logging.INFO
    assert all(r.levelno < logging.WARNING for r in records)


def test_a_run_with_a_flagged_case_is_a_warning_that_says_how_many(tmp_path, records):
    run(["--live"], tmp_path, comment="Add: wisc")  # WISC is already on the case
    report = next(r for r in records if r.getMessage().startswith("Sweep report"))
    assert report.levelno == logging.WARNING
    assert report.getMessage().startswith("Sweep report (live), 1 case(s) need attention:")
    assert "WARNING" in read(tmp_path)


def test_a_flagged_case_is_not_written_to_the_error_file(tmp_path):
    run(["--live"], tmp_path, comment="Add: wisc")
    assert not (tmp_path / "sweep_error.log").read_text(encoding="utf-8").strip()


# --- Failures -------------------------------------------------------


def test_a_failure_is_logged_with_its_traceback_and_raised_again(tmp_path):
    spreadsheet = FakeSpreadsheet({"Services": FakeWorksheet([["Code", "Aliases"]])})  # no Ongoing tab
    with pytest.raises(WorksheetNotFound):
        main(["--live"], spreadsheet=spreadsheet, metadata=FakeMetadataClient(),
             pause=False, config=config_for(tmp_path))

    errors = read(tmp_path, "sweep_error.log")
    assert "Sweep failed (live)" in errors
    assert "Traceback (most recent call last)" in errors
    assert "WorksheetNotFound" in errors
    text = read(tmp_path)
    assert "Sweep started (live)" in text
    assert "Sweep finished" not in text


def test_a_failure_while_connecting_to_google_is_logged_too(tmp_path, monkeypatch):
    def no_network():
        raise RuntimeError("no network")

    monkeypatch.setattr("app.sheet_client.get_client", no_network)
    with pytest.raises(RuntimeError, match="no network"):
        main(["--live"], pause=False, config=config_for(tmp_path))
    assert "RuntimeError: no network" in read(tmp_path, "sweep_error.log")


def test_a_failed_preview_says_it_was_a_preview(tmp_path):
    spreadsheet = FakeSpreadsheet({})
    with pytest.raises(WorksheetNotFound):
        main([], spreadsheet=spreadsheet, metadata=FakeMetadataClient(),
             pause=False, config=config_for(tmp_path))
    assert "Sweep failed (preview)" in read(tmp_path, "sweep_error.log")


def test_a_failure_sends_the_alert_once_with_the_details(tmp_path, alerts):
    config = config_for(tmp_path)
    with pytest.raises(WorksheetNotFound):
        main(["--live"], spreadsheet=FakeSpreadsheet({}), metadata=FakeMetadataClient(),
             pause=False, config=config)

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["subject"] == "Sweep failed"
    assert "(live)" in alert["message"]
    assert alert["config"] is config
    assert alert["exc_info"][0] is WorksheetNotFound


def test_a_successful_run_sends_no_alert(tmp_path, alerts):
    run(["--live"], tmp_path)
    run(["--live"], tmp_path, comment="Add: wisc")  # flagged, still not a failure
    assert alerts == []


def test_a_failure_is_logged_even_when_the_settings_cannot_be_read(tmp_path):
    with pytest.raises(WorksheetNotFound):
        main(["--live"], spreadsheet=FakeSpreadsheet({}), metadata=FakeMetadataClient(),
             pause=False)  # no config given, and tmp_path has no config/settings.json
    assert "Sweep failed (live)" in read(tmp_path / "logs", "sweep_error.log")


# --- Where the logs go ----------------------------------------------


def test_the_log_folder_comes_from_the_settings(tmp_path):
    folder = tmp_path / "somewhere" / "else"
    run(["--live"], folder)
    assert (folder / "sweep.log").exists()
    assert (folder / "sweep_error.log").exists()
    assert not (tmp_path / "logs").exists()


def test_without_a_settings_file_it_logs_to_a_logs_folder_and_says_why(tmp_path):
    spreadsheet, client = world()
    main(["--live"], spreadsheet=spreadsheet, metadata=client, pause=False)
    text = read(tmp_path / "logs")
    assert "Could not read config/settings.json" in text
    assert "Sweep started (live)" in text


def test_the_pipelines_log_files_are_never_touched(tmp_path):
    run(["--live"], tmp_path)
    assert not (tmp_path / "scheduler.log").exists()
    assert not (tmp_path / "scheduler_error.log").exists()


def test_running_twice_logs_each_run_once(tmp_path):
    run(["--live"], tmp_path)
    run(["--live"], tmp_path)
    text = read(tmp_path)
    assert text.count("Sweep started (live)") == 2
    assert text.count("Sweep finished (live)") == 2
    assert text.count("Sweep report (live)") == 2


def test_a_bad_option_exits_before_anything_is_logged(tmp_path):
    spreadsheet, client = world()
    with pytest.raises(SystemExit):
        main(["--lve"], spreadsheet=spreadsheet, metadata=client, pause=False,
             config=config_for(tmp_path))
    assert not (tmp_path / "sweep.log").exists()