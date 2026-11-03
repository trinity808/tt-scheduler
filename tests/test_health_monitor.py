"""
Tests for the logging setup in app/health_monitor.py.

The logger is shared by the whole process, so each test starts and ends with
it clean (see tests/log_cleanup.py).

The logger doesn't pass records up to the root logger, so pytest's caplog
can't see them. These tests read the log files instead.

Run from the project root:
    python -m pytest tests/test_health_monitor.py -v
"""

import re
from logging.handlers import RotatingFileHandler

import pytest

from app.health_monitor import get_logger, setup_logging
from tests.log_cleanup import clean_logger, file_handlers  # noqa: F401


def config_for(folder):
    return {"health_check": {"log_folder": str(folder)}}


def read(folder, name):
    return (folder / name).read_text(encoding="utf-8")


# --- The pipeline's files are still the default ---------------------


def test_the_default_files_are_the_pipelines(tmp_path):
    setup_logging(config_for(tmp_path))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scheduler.log", "scheduler_error.log"]


def test_the_default_files_are_used_when_called_the_way_main_py_calls_it(tmp_path):
    logger = setup_logging(config_for(tmp_path))
    logger.info("pipeline line")
    assert "pipeline line" in read(tmp_path, "scheduler.log")


# --- Other commands can name their own files ------------------------


def test_custom_names_are_used(tmp_path):
    setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sweep.log", "sweep_error.log"]


def test_a_custom_command_never_touches_the_pipelines_files(tmp_path):
    logger = setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    logger.info("sweep line")
    logger.error("sweep failure")
    assert not (tmp_path / "scheduler.log").exists()
    assert not (tmp_path / "scheduler_error.log").exists()


def test_each_name_can_be_given_on_its_own(tmp_path):
    setup_logging(config_for(tmp_path), info_log_name="only_info.log")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["only_info.log", "scheduler_error.log"]


# --- What goes where ------------------------------------------------


def test_info_goes_to_the_main_file_and_not_the_error_file(tmp_path):
    logger = setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    logger.info("an ordinary line")
    assert "an ordinary line" in read(tmp_path, "sweep.log")
    assert "an ordinary line" not in read(tmp_path, "sweep_error.log")


def test_warnings_go_to_the_main_file_only(tmp_path):
    logger = setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    logger.warning("needs attention")
    assert "needs attention" in read(tmp_path, "sweep.log")
    assert "needs attention" not in read(tmp_path, "sweep_error.log")


def test_errors_go_to_both_files(tmp_path):
    logger = setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    logger.error("it failed")
    assert "it failed" in read(tmp_path, "sweep.log")
    assert "it failed" in read(tmp_path, "sweep_error.log")


def test_a_logged_exception_includes_its_traceback(tmp_path):
    logger = setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    try:
        raise ValueError("bad sheet")
    except ValueError:
        logger.exception("Sweep failed")
    text = read(tmp_path, "sweep_error.log")
    assert "Sweep failed" in text
    assert "Traceback (most recent call last)" in text
    assert "ValueError: bad sheet" in text


def test_lines_have_the_usual_format(tmp_path):
    logger = setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    logger.info("hello")
    pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} \| INFO \| trinity_scheduler \| hello$"
    assert re.search(pattern, read(tmp_path, "sweep.log"), re.MULTILINE)


# --- Setup itself ---------------------------------------------------


def test_the_log_folder_is_created_if_missing(tmp_path):
    folder = tmp_path / "not" / "there" / "yet"
    setup_logging(config_for(folder), "sweep.log", "sweep_error.log")
    assert (folder / "sweep.log").exists()


def test_with_no_config_it_uses_a_logs_folder_in_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    setup_logging(None, "sweep.log", "sweep_error.log")
    assert (tmp_path / "logs" / "sweep.log").exists()


def test_files_rotate_at_two_megabytes_and_keep_five_backups(tmp_path):
    logger = setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    handlers = file_handlers()
    assert len(handlers) == 2
    assert all(h.maxBytes == 2_000_000 and h.backupCount == 5 for h in handlers)


def test_it_returns_the_shared_logger(tmp_path):
    assert setup_logging(config_for(tmp_path)) is get_logger()


def test_the_logger_does_not_pass_records_to_the_root_logger(tmp_path):
    assert setup_logging(config_for(tmp_path)).propagate is False


# --- Calling it more than once --------------------------------------


def test_calling_it_twice_does_not_write_every_line_twice(tmp_path):
    setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    logger = setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
    logger.info("only once please")
    assert read(tmp_path, "sweep.log").count("only once please") == 1
    assert len(file_handlers()) == 2


def test_the_first_call_decides_which_files_are_used(tmp_path):
    setup_logging(config_for(tmp_path), "first.log", "first_error.log")
    logger = setup_logging(config_for(tmp_path), "second.log", "second_error.log")
    logger.info("goes to the first files")
    assert "goes to the first files" in read(tmp_path, "first.log")
    assert not (tmp_path / "second.log").exists()


def test_a_handler_someone_else_attached_does_not_stop_the_files_being_created(tmp_path):
    # A test tool, or any other code, may attach a handler to this logger
    # before setup runs. That must not make setup think it is already done.
    import logging

    other = logging.NullHandler()
    get_logger().addHandler(other)
    try:
        setup_logging(config_for(tmp_path), "sweep.log", "sweep_error.log")
        assert sorted(p.name for p in tmp_path.iterdir()) == ["sweep.log", "sweep_error.log"]
    finally:
        get_logger().removeHandler(other)