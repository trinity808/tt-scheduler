"""
Tests for app/schedule_generator.py's sort_table_by_datetime(), the
sort used by the legacy DOCX schedule that main.py still produces.

Each test builds a small table in memory and sorts it, so nothing is
written to disk.

Run from the project root:
    python -m pytest tests/test_schedule_generator.py -v
"""

from docx import Document

from app.schedule_generator import SCHEDULE_HEADERS, sort_table_by_datetime


DATE_TIME_COL = SCHEDULE_HEADERS.index("Date & Time")


def make_table(rows):
    """
    Builds a schedule table with the real headers. Each row is given
    as (case_id, date_time_text); other columns are left blank.
    """
    doc = Document()
    table = doc.add_table(rows=1, cols=len(SCHEDULE_HEADERS))
    for i, header in enumerate(SCHEDULE_HEADERS):
        table.rows[0].cells[i].text = header

    for case_id, date_time in rows:
        cells = table.add_row().cells
        cells[0].text = case_id
        cells[DATE_TIME_COL].text = date_time

    return table


def case_ids(table):
    return [row.cells[0].text for row in table.rows[1:]]


def test_rows_sorted_by_time_regardless_of_insertion_order():
    # Inserted deliberately out of order: 1 PM, 8 AM, 10 AM.
    table = make_table([
        ("30003", "May 4, 2026\n 1:00 PM MST"),
        ("30001", "May 4, 2026\n 8:00 AM MST"),
        ("30002", "May 4, 2026\n 10:00 AM MST"),
    ])
    sort_table_by_datetime(table)
    assert case_ids(table) == ["30001", "30002", "30003"]


def test_header_row_stays_first():
    table = make_table([
        ("30002", "May 4, 2026\n 10:00 AM MST"),
        ("30001", "May 4, 2026\n 8:00 AM MST"),
    ])
    sort_table_by_datetime(table)
    assert table.rows[0].cells[0].text == SCHEDULE_HEADERS[0]


def test_already_sorted_rows_stay_in_order():
    table = make_table([
        ("30001", "May 4, 2026\n 8:00 AM MST"),
        ("30002", "May 4, 2026\n 10:00 AM MST"),
    ])
    sort_table_by_datetime(table)
    assert case_ids(table) == ["30001", "30002"]


def test_date_written_as_produced_by_the_pipeline_is_sorted():
    # The pipeline writes dates like "May 4th", not "May 4".
    table = make_table([
        ("30002", "May 4th, 2026\n 10:00 AM MST"),
        ("30001", "May 4th, 2026\n 08:00 AM MST"),
    ])
    sort_table_by_datetime(table)
    assert case_ids(table) == ["30001", "30002"]


def test_unparseable_date_sorts_last():
    # Documents existing behavior: a date the parser can't read doesn't
    # raise an error, it silently sorts to the bottom.
    table = make_table([
        ("30009", "not a date"),
        ("30001", "May 4, 2026\n 8:00 AM MST"),
    ])
    sort_table_by_datetime(table)
    assert case_ids(table) == ["30001", "30009"]