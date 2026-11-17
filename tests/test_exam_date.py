"""
Tests for app/exam_date.py: reading the exam date out of the Ongoing tab's
"Date & Time" text.

Run from the project root:
    python -m pytest tests/test_exam_date.py -v
"""

from datetime import date

import pytest

from app.exam_date import parse_exam_date


MAY_4 = date(2026, 5, 4)


# --- The text the PDF mapper writes, and what people type -----------------


@pytest.mark.parametrize(
    "text",
    [
        "May 4th, 2026 08:00 AM MST",       # exactly what ongoing_row_mapper.py writes
        "May 4, 2026 8:00 AM MST",
        "May 4, 2026\n 8:00 AM MST",        # date and time on separate lines
        "May 04, 2026 8:00 AM MST",
        "May 4 2026",                        # no comma
        "May 4th 2026 1:00 PM MST",
        "may 4th, 2026",                     # capitals don't matter
        "MAY 4, 2026",
        "  May 4th, 2026 08:00 AM MST  ",    # stray spaces
        "Monday, May 4th, 2026 08:00 AM MST",  # a leading weekday is ignored
        "5/4/2026",
        "05/04/2026",
        "5/4/2026 8:00:00",
        "05/04/26",
        "2026-05-04",
        "2026-05-04 08:00",
    ],
)
def test_the_shapes_that_mean_may_4_2026(text):
    assert parse_exam_date(text) == MAY_4


@pytest.mark.parametrize(
    "text, expected",
    [
        ("May 1st, 2026 8:00 AM MST", date(2026, 5, 1)),
        ("May 2nd, 2026", date(2026, 5, 2)),
        ("May 3rd, 2026", date(2026, 5, 3)),
        ("May 22nd, 2026", date(2026, 5, 22)),
        ("Sept 9, 2026", date(2026, 9, 9)),
        ("Sept. 9, 2026", date(2026, 9, 9)),
        ("Sep 9, 2026", date(2026, 9, 9)),
        ("September 9th, 2026", date(2026, 9, 9)),
        ("Dec 31, 2026", date(2026, 12, 31)),
        ("January 1st, 2027", date(2027, 1, 1)),
        ("Feb 28, 2026", date(2026, 2, 28)),
        ("Feb 29, 2028", date(2028, 2, 29)),
    ],
)
def test_ordinals_and_month_spellings(text, expected):
    assert parse_exam_date(text) == expected


def test_the_month_is_taken_first_for_numbers_like_5_4_2026():
    assert parse_exam_date("5/4/2026") == date(2026, 5, 4)
    assert parse_exam_date("12/1/2026") == date(2026, 12, 1)


# --- The time of day and the zone never matter -----------------------------


@pytest.mark.parametrize("time_text", ["08:00 AM MST", "10:00 AM MST", "1:00 PM MST", "11:59 PM MST", ""])
def test_the_time_of_day_changes_nothing(time_text):
    assert parse_exam_date(f"May 4th, 2026 {time_text}") == MAY_4


# --- What it refuses -------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", None, "\n"])
def test_nothing_there_is_refused(text):
    with pytest.raises(ValueError, match="empty"):
        parse_exam_date(text)


@pytest.mark.parametrize(
    "text", ["garbage", "TBD", "Monday", "8:00 AM MST", "May 2026", "May 4th", "4th of May 2026"]
)
def test_text_with_no_date_is_refused(text):
    with pytest.raises(ValueError, match="no date found"):
        parse_exam_date(text)


@pytest.mark.parametrize(
    "text", ["Feb 30, 2026", "Feb 29, 2026", "13/1/2026", "2026-13-01", "2026-02-30", "4/31/2026"]
)
def test_a_date_that_does_not_exist_is_refused(text):
    with pytest.raises(ValueError, match="isn't a real date"):
        parse_exam_date(text)


def test_a_word_that_is_not_a_month_is_not_taken_for_one():
    with pytest.raises(ValueError):
        parse_exam_date("Monday 4 2026")


def test_the_reason_names_the_text_so_the_case_can_be_fixed():
    with pytest.raises(ValueError, match="'garbage'"):
        parse_exam_date("garbage")