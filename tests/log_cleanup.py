"""
Shared cleanup for tests that set up the process-wide logger.

The logger is one object for the whole process, so a test that sets up its log
files must not leave them attached for the next test. The handlers are closed
as well as removed, because Windows can't delete a temporary folder while a log
file in it is still open. Only the file handlers are ours to remove: pytest
attaches its own capture handlers to this logger while a test runs.

Import `clean_logger` into a test module and it applies to every test there.
Not collected by pytest, since the filename doesn't start with test_.
"""

from logging.handlers import RotatingFileHandler

import pytest

from app.health_monitor import get_logger


def file_handlers():
    return [h for h in get_logger().handlers if isinstance(h, RotatingFileHandler)]


def _release():
    logger = get_logger()
    for handler in file_handlers():
        handler.close()
        logger.removeHandler(handler)


@pytest.fixture(autouse=True)
def clean_logger():
    _release()
    yield
    _release()