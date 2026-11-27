"""
When each reminder goes out, and what it says: Dr.'s DDS case reminder schedule
for providers (phase 1). Pure rules, no sheet and no email, so every rule can be
tested with plain dates.

The schedule. Day 0 is the date of the exam, and deadlines are calendar days:

    Day 1   upload behavioral observations and testing materials
    Day 3   materials overdue notice        only if materials aren't uploaded
    Day 5   midpoint check-in on the report
    Day 8   report due in 2 days
    Day 10  report due today
    Day 11  overdue notice, director copied only if the report isn't submitted

Every reminder goes to the provider. Reminders stop once the item is complete:
once materials are uploaded (Day 1 and 3), and once the report is submitted
(everything else). A provider who meets every deadline gets at most four.

Weekends. Nothing goes out on a Saturday or Sunday. A reminder that falls on one
is moved, in the direction that keeps it useful:
  - Day 8 and Day 10 warn before a deadline, so they move back to the Friday
    before (Dr.'s own rule for Day 8, applied to Day 10 as well). The message
    names the due date, so it stays right when it arrives early.
  - Day 1, 3, 5 and 11 follow something that has already happened, so they move
    on to the Monday after. Day 11 in particular can't go earlier: on the Friday
    before, nothing is overdue yet.
If two reminders end up on the same day, only the later one goes.

What each case needs to know is passed in, not looked up here:
  report_submitted   True once the case has been moved to Finished.
  materials_uploaded True or False when it is known, None when it isn't. Nothing
                     tracks it yet, so it is None, and then Day 3 is not sent
                     (it is "only if materials aren't uploaded", and nobody
                     can say). The rule is written already, so tracking it
                     later means passing the value in, not changing this file.
  already_sent       the keys of reminders already sent for this case. Working
                     out which reminder is due comes first, and only then is
                     the log looked at, so running again the same day never
                     lets an earlier reminder slip out in the later one's place.

The day is counted in Arizona time, which is MST all year (Arizona has no
daylight saving), so a fixed offset is exact and needs no time zone database.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


ARIZONA = timezone(timedelta(hours=-7), "MST")

DAY_1 = "DAY_1"
DAY_3 = "DAY_3"
DAY_5 = "DAY_5"
DAY_8 = "DAY_8"
DAY_10 = "DAY_10"
DAY_11_OVERDUE = "DAY_11_OVERDUE"

REPORT_DAYS = 10  # the report is due this many calendar days after the exam

BACK = "back"        # a weekend reminder moves to the Friday before
FORWARD = "forward"  # a weekend reminder moves to the Monday after


@dataclass(frozen=True)
class Reminder:
    key: str
    day: int
    label: str
    shift: str
    cc_director: bool = False


SCHEDULE = (
    Reminder(DAY_1, 1, "Day 1 materials request", FORWARD),
    Reminder(DAY_3, 3, "Day 3 materials overdue notice", FORWARD),
    Reminder(DAY_5, 5, "Day 5 report check-in", FORWARD),
    Reminder(DAY_8, 8, "Day 8 report due soon", BACK),
    Reminder(DAY_10, 10, "Day 10 report due", BACK),
    Reminder(DAY_11_OVERDUE, 11, "Day 11 overdue notice", FORWARD, cc_director=True),
)

BY_KEY = {reminder.key: reminder for reminder in SCHEDULE}

MATERIALS_UNKNOWN = "materials status isn't tracked yet, so it isn't sent"


def arizona_today(now=None):
    """Today's date in Arizona. `now` is for tests."""
    return (now or datetime.now(timezone.utc)).astimezone(ARIZONA).date()


def due_date(exam_date, reminder):
    """The calendar day the schedule names, before any weekend move."""
    return exam_date + timedelta(days=reminder.day)


def report_due_date(exam_date):
    return exam_date + timedelta(days=REPORT_DAYS)


def send_date(exam_date, reminder):
    """The day the reminder goes out: its due day, or the nearest weekday it moves to."""
    due = due_date(exam_date, reminder)
    weekday = due.weekday()  # Monday is 0, Saturday 5, Sunday 6
    if weekday < 5:
        return due
    if reminder.shift == BACK:
        return due - timedelta(days=weekday - 4)
    return due + timedelta(days=7 - weekday)


@dataclass(frozen=True)
class Decision:
    send: Reminder = None          # what to send today, if anything
    already_sent: Reminder = None  # what would have gone today, but already did
    unsent: tuple = ()             # (Reminder, reason) due today that won't go, worth saying


def _still_needed(reminder, report_submitted, materials_uploaded):
    """Rule 1: a reminder stops once the item it is about is complete."""
    if report_submitted:
        return False
    if reminder.key == DAY_1:
        return materials_uploaded is not True
    if reminder.key == DAY_3:
        return materials_uploaded is False
    return True


def decide_reminders(
    exam_date, today, report_submitted=False, materials_uploaded=None, already_sent=()
):
    """What to send for one case today. See the module docstring for the rules."""
    due_today = [r for r in SCHEDULE if send_date(exam_date, r) == today]

    unsent = []
    eligible = []
    for reminder in due_today:
        if reminder.key == DAY_3 and materials_uploaded is None and not report_submitted:
            unsent.append((reminder, MATERIALS_UNKNOWN))
        elif _still_needed(reminder, report_submitted, materials_uploaded):
            eligible.append(reminder)

    if not eligible:
        return Decision(unsent=tuple(unsent))

    chosen = max(eligible, key=lambda r: r.day)
    for reminder in eligible:
        if reminder is not chosen:
            unsent.append((reminder, f"{chosen.label} goes out the same day instead"))

    if chosen.key in already_sent:
        return Decision(already_sent=chosen, unsent=tuple(unsent))
    return Decision(send=chosen, unsent=tuple(unsent))


# --- What the email says --------------------------------------------------


def _long_date(day):
    return f"{day:%A}, {day:%B} {day.day}, {day.year}"


def _when_due(send_on, due):
    return "today" if send_on == due else f"on {_long_date(due)}"


def build_message(reminder, case_id, exam_date, send_on, provider_name=""):
    """
    (subject, body) for a reminder. The body names the case ID and dates only, never
    the patient. Wording that depends on timing ("due today", "in 2 days") is
    chosen from the real dates, so a reminder moved off a weekend never says
    something untrue.
    """
    due = report_due_date(exam_date)
    exam = _long_date(exam_date)
    greeting = f"Hello {provider_name.strip()}," if provider_name.strip() else "Hello,"
    case = f"DDS case {case_id}"

    if reminder.key == DAY_1:
        subject = f"{case}: please upload behavioral observations and testing materials"
        text = (
            f"The exam for {case} was on {exam}. Please upload the behavioral "
            "observations and testing materials for this case. Our internal target "
            "is within 48 hours of the exam."
            f"\n\nThe report is due {_when_due(send_on, due)}."
        )
    elif reminder.key == DAY_3:
        subject = f"Reminder: materials for {case} are overdue"
        text = (
            f"The behavioral observations and testing materials for {case} "
            f"(exam on {exam}) haven't been uploaded yet. Please upload them as "
            "soon as possible."
        )
    elif reminder.key == DAY_5:
        subject = f"Check-in: report for {case}"
        text = (
            f"This is a midpoint check-in on the report for {case} (exam on {exam}). "
            f"The report is due on {_long_date(due)}."
        )
    elif reminder.key == DAY_8:
        days_left = (due - send_on).days
        when = "in 2 days" if days_left == 2 else f"on {_long_date(due)}"
        subject = f"Report for {case} is due {when}"
        text = f"The report for {case} (exam on {exam}) is due {when}."
    elif reminder.key == DAY_10:
        when = _when_due(send_on, due)
        subject = f"Report for {case} is due {when}"
        text = f"The report for {case} (exam on {exam}) is due {when}."
    elif reminder.key == DAY_11_OVERDUE:
        subject = f"OVERDUE: report for {case}"
        text = (
            f"The report for {case} (exam on {exam}) was due on {_long_date(due)} and "
            "has not been submitted. The Director is copied on this notice. Please "
            "submit it as soon as possible."
        )
    else:
        raise ValueError(f"Unknown reminder: {reminder.key}")

    body = f"{greeting}\n\n{text}\n\nThank you,\nTrinity Tree Psychological Services"
    return subject, body