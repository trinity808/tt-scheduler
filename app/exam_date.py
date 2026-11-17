"""
Reads the exam date out of an Ongoing row's "Date & Time" text.

The text is written by ongoing_row_mapper.py from the schedule PDF, for example
"May 4th, 2026 08:00 AM MST", but it is also an ordinary cell that people can
edit, so a few other common shapes are accepted too:

    May 4th, 2026 08:00 AM MST      month name, day with or without st/nd/rd/th
    May 4, 2026\\n 8:00 AM MST       the date and time may be on separate lines
    Sept. 4, 2026                   month abbreviations, with or without a dot
    Monday, May 4th, 2026           a leading weekday is ignored
    5/4/2026   05/04/26             month first, as in the US
    2026-05-04                      year first

Only the date is used. The time of day never changes which day a reminder goes
out (Day 0 is the exam's calendar date), so it is read past and ignored, and so
is the time zone. The date is taken as written: the exams are in Arizona and so
are the reminders' day counts.

Anything else raises ValueError with a short reason, so the caller can report
the case instead of guessing at a date.
"""

import calendar
import re
from datetime import date


def _month_names():
    names = {}
    for number in range(1, 13):
        names[calendar.month_name[number].lower()] = number
        names[calendar.month_abbr[number].lower()] = number
    names["sept"] = 9
    return names


_MONTHS = _month_names()

# "May 4th, 2026": a word, a day (with an optional ordinal ending), then a year.
_WORD_DATE = re.compile(
    r"(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?\b\s*,?\s*(?P<year>\d{4})\b"
)
_ISO_DATE = re.compile(r"\b(?P<year>\d{4})-(?P<month>\d{1,2})-(?P<day>\d{1,2})\b")
_US_DATE = re.compile(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>\d{4}|\d{2})\b")


def _make_date(year, month, day, text):
    try:
        return date(year, month, day)
    except ValueError:
        raise ValueError(f"{text.strip()!r} isn't a real date") from None


def parse_exam_date(text):
    """The exam's calendar date, from the cell's text. Raises ValueError if it can't be read."""
    if text is None or not str(text).strip():
        raise ValueError("it is empty")
    text = str(text)

    for match in _WORD_DATE.finditer(text):
        month = _MONTHS.get(match.group("month").lower())
        if month is not None:  # a word like "Monday" isn't a month: keep looking
            return _make_date(int(match.group("year")), month, int(match.group("day")), text)

    match = _ISO_DATE.search(text)
    if match:
        return _make_date(
            int(match.group("year")), int(match.group("month")), int(match.group("day")), text
        )

    match = _US_DATE.search(text)
    if match:
        year = int(match.group("year"))
        if year < 100:
            year += 2000
        return _make_date(year, int(match.group("month")), int(match.group("day")), text)

    raise ValueError(f"no date found in {text.strip()!r}")