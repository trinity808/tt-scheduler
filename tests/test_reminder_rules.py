"""
Tests for app/reminder_rules.py: Dr.'s reminder schedule, as plain rules on dates.

The calendar used is May 2026, where May 1 is a Friday, so May 4 is a Monday.
Every expected date in the weekend table is written out by hand, not worked out
by the code under test.

Run from the project root:
    python -m pytest tests/test_reminder_rules.py -v
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.reminder_rules import (
    ARIZONA,
    BY_KEY,
    DAY_1,
    DAY_3,
    DAY_5,
    DAY_8,
    DAY_10,
    DAY_11_OVERDUE,
    MATERIALS_UNKNOWN,
    SCHEDULE,
    Reminder,
    arizona_today,
    build_message,
    decide_reminders,
    due_date,
    report_due_date,
    send_date,
)


def d(day, month=5):
    return date(2026, month, day)


def a_year_of_exams():
    return [date(2026, 1, 1) + timedelta(days=n) for n in range(365)]


KEYS = [DAY_1, DAY_3, DAY_5, DAY_8, DAY_10, DAY_11_OVERDUE]

# Where each reminder goes out, for an exam on each weekday of the week of May 4.
# One list per exam day, in the order Day 1, 3, 5, 8, 10, 11.
WEEKEND_TABLE = {
    "Mon May 4":  [d(5),  d(7),  d(11), d(12), d(14), d(15)],  # Day 5 is a Saturday: on to Monday
    "Tue May 5":  [d(6),  d(8),  d(11), d(13), d(15), d(18)],  # Day 5 Sunday -> Mon; Day 11 Saturday -> Mon
    "Wed May 6":  [d(7),  d(11), d(11), d(14), d(15), d(18)],  # Day 3 Sat -> Mon; Day 10 Sat -> Fri; Day 11 Sun -> Mon
    "Thu May 7":  [d(8),  d(11), d(12), d(15), d(15), d(18)],  # Day 3 Sun -> Mon; Day 10 Sun -> Fri
    "Fri May 8":  [d(11), d(11), d(13), d(15), d(18), d(19)],  # Day 1 Sat -> Mon; Day 8 Sat -> Fri
    "Sat May 9":  [d(11), d(12), d(14), d(15), d(19), d(20)],  # Day 1 Sun -> Mon; Day 8 Sun -> Fri
    "Sun May 10": [d(11), d(13), d(15), d(18), d(20), d(21)],  # nothing lands on a weekend
}
EXAM_DAYS = {
    "Mon May 4": d(4), "Tue May 5": d(5), "Wed May 6": d(6), "Thu May 7": d(7),
    "Fri May 8": d(8), "Sat May 9": d(9), "Sun May 10": d(10),
}


def simulate(exam, uploaded_on=None, submitted_on=None, materials_known=True):
    """
    Runs every day from the exam to 24 days after it, logging what goes out, the way a
    daily run with a sent log would. Materials count as not uploaded until
    `uploaded_on` (None while not known, when materials_known is False); the report as
    not submitted until `submitted_on`. Returns [(day, key)] in order.
    """
    log, out = set(), []
    for n in range(25):
        today = exam + timedelta(days=n)
        if materials_known:
            uploaded = uploaded_on is not None and today >= uploaded_on
        else:
            uploaded = None
        submitted = submitted_on is not None and today >= submitted_on
        decision = decide_reminders(
            exam, today, report_submitted=submitted, materials_uploaded=uploaded, already_sent=log
        )
        if decision.send:
            log.add(decision.send.key)
            out.append((today, decision.send.key))
    return out


# --- The schedule itself ---------------------------------------------------


def test_the_schedule_is_drs_six_reminders_in_order():
    assert [(r.key, r.day) for r in SCHEDULE] == [
        (DAY_1, 1), (DAY_3, 3), (DAY_5, 5), (DAY_8, 8), (DAY_10, 10), (DAY_11_OVERDUE, 11),
    ]


def test_only_the_overdue_notice_copies_the_director():
    assert [r.key for r in SCHEDULE if r.cc_director] == [DAY_11_OVERDUE]


def test_the_report_is_due_ten_calendar_days_after_the_exam():
    assert report_due_date(d(4)) == d(14)
    assert report_due_date(d(30)) == date(2026, 6, 9)


def test_the_due_day_is_the_exam_day_plus_the_reminders_day():
    assert due_date(d(4), BY_KEY[DAY_5]) == d(9)


# --- Weekends: where each reminder goes out ---------------------------------


@pytest.mark.parametrize("exam_name", list(WEEKEND_TABLE))
def test_each_reminder_goes_out_on_the_hand_worked_day(exam_name):
    exam = EXAM_DAYS[exam_name]
    assert [send_date(exam, BY_KEY[key]) for key in KEYS] == WEEKEND_TABLE[exam_name]


def test_nothing_is_ever_sent_on_a_weekend():
    for exam in a_year_of_exams():
        for reminder in SCHEDULE:
            assert send_date(exam, reminder).weekday() < 5, (exam, reminder.key)


def test_warnings_before_a_deadline_never_come_after_it_and_move_back_at_most_two_days():
    for exam in a_year_of_exams():
        for key in (DAY_8, DAY_10):
            reminder = BY_KEY[key]
            moved = send_date(exam, reminder)
            assert due_date(exam, reminder) - timedelta(days=2) <= moved <= due_date(exam, reminder)


def test_reminders_that_follow_an_event_never_come_before_it_and_move_on_at_most_two_days():
    for exam in a_year_of_exams():
        for key in (DAY_1, DAY_3, DAY_5, DAY_11_OVERDUE):
            reminder = BY_KEY[key]
            moved = send_date(exam, reminder)
            assert due_date(exam, reminder) <= moved <= due_date(exam, reminder) + timedelta(days=2)


def test_the_overdue_notice_is_never_sent_before_the_report_is_overdue():
    for exam in a_year_of_exams():
        assert send_date(exam, BY_KEY[DAY_11_OVERDUE]) > report_due_date(exam)


def test_a_weekday_reminder_is_not_moved():
    assert send_date(d(4), BY_KEY[DAY_8]) == d(12)


# --- Which reminder goes today ---------------------------------------------


def test_the_day_5_check_in_goes_to_a_case_five_days_after_its_exam():
    decision = decide_reminders(d(4), d(11))  # Day 5 is Saturday May 9, so Monday May 11
    assert decision.send.key == DAY_5 and decision.already_sent is None


def test_a_day_with_no_reminder_sends_nothing():
    decision = decide_reminders(d(4), d(6))
    assert decision.send is None and decision.already_sent is None and decision.unsent == ()


def test_nothing_goes_before_or_on_the_exam_day_or_long_after_the_overdue_notice():
    exam = d(4)
    for today in (d(1), d(3), d(4)):
        assert decide_reminders(exam, today).send is None
    for n in range(12, 60):
        assert decide_reminders(exam, exam + timedelta(days=n)).send is None


def test_the_overdue_notice_goes_to_an_unsubmitted_case_with_the_director_copied():
    decision = decide_reminders(d(4), d(15), report_submitted=False)
    assert decision.send.key == DAY_11_OVERDUE and decision.send.cc_director


def test_the_overdue_notice_is_sent_once_not_every_day_after():
    sent = simulate(d(4))
    assert [key for _, key in sent].count(DAY_11_OVERDUE) == 1


# --- Reminders stop once the item is complete (rule 1) ----------------------


@pytest.mark.parametrize("submitted_after_exam_days", range(0, 13))
def test_nothing_is_sent_on_or_after_the_day_the_report_is_in(submitted_after_exam_days):
    for exam in (d(4), d(7), d(8)):
        submitted_on = exam + timedelta(days=submitted_after_exam_days)
        sent = simulate(exam, uploaded_on=exam + timedelta(days=2), submitted_on=submitted_on)
        assert all(day < submitted_on for day, _ in sent), (exam, submitted_on, sent)


def test_materials_reminders_stop_once_materials_are_uploaded():
    exam = d(4)  # Day 1 Tue May 5, Day 3 Thu May 7
    assert decide_reminders(exam, d(5), materials_uploaded=True).send is None
    assert decide_reminders(exam, d(7), materials_uploaded=True).send is None
    assert decide_reminders(exam, d(7), materials_uploaded=True).unsent == ()


def test_the_report_reminders_do_not_depend_on_the_materials():
    for uploaded in (None, True, False):
        assert decide_reminders(d(4), d(11), materials_uploaded=uploaded).send.key == DAY_5


# --- Day 3: only if materials are not uploaded, and nobody can say yet -------


def test_day_3_goes_when_materials_are_known_not_to_be_uploaded():
    decision = decide_reminders(d(4), d(7), materials_uploaded=False)  # Day 3 is Thursday May 7
    assert decision.send.key == DAY_3


def test_day_3_is_not_sent_while_materials_status_is_unknown_and_the_report_says_so():
    decision = decide_reminders(d(4), d(7), materials_uploaded=None)
    assert decision.send is None
    assert [(r.key, reason) for r, reason in decision.unsent] == [(DAY_3, MATERIALS_UNKNOWN)]


def test_day_3_is_never_sent_while_nothing_tracks_materials():
    for exam in a_year_of_exams():
        assert DAY_3 not in [key for _, key in simulate(exam, materials_known=False)]


def test_day_1_is_still_sent_while_materials_status_is_unknown():
    sent = simulate(d(4), materials_known=False)
    assert (d(5), DAY_1) in sent


def test_day_3_is_not_mentioned_at_all_once_the_report_is_in():
    decision = decide_reminders(d(4), d(7), report_submitted=True, materials_uploaded=None)
    assert decision.send is None and decision.unsent == ()


# --- Two reminders on one day: only the later one goes ----------------------


def test_when_day_8_and_day_10_share_a_friday_only_day_10_goes():
    exam = d(7)  # Thursday: Day 8 is Friday May 15, Day 10 is Sunday May 17, moved back to May 15
    decision = decide_reminders(exam, d(15))
    assert decision.send.key == DAY_10
    assert [(r.key, "Day 10" in reason) for r, reason in decision.unsent] == [(DAY_8, True)]


def test_running_again_after_the_later_one_was_sent_does_not_send_the_earlier_one():
    exam = d(7)
    decision = decide_reminders(exam, d(15), already_sent={DAY_10})
    assert decision.send is None
    assert decision.already_sent.key == DAY_10


def test_running_again_when_only_the_earlier_one_is_logged_still_sends_the_later_one():
    decision = decide_reminders(d(7), d(15), already_sent={DAY_8})
    assert decision.send.key == DAY_10


def test_when_day_3_and_day_5_share_a_monday_day_5_goes():
    exam = d(6)  # Wednesday: Day 3 is Saturday, moved on to Monday May 11, and Day 5 is Monday May 11
    decision = decide_reminders(exam, d(11), materials_uploaded=False)
    assert decision.send.key == DAY_5
    assert [r.key for r, _ in decision.unsent] == [DAY_3]


def test_when_day_1_and_day_3_share_a_monday_and_materials_are_unknown_day_1_still_goes():
    exam = d(8)  # Friday: Day 1 is Saturday, moved on to Monday May 11, and Day 3 is Monday May 11
    decision = decide_reminders(exam, d(11), materials_uploaded=None)
    assert decision.send.key == DAY_1
    assert [(r.key, reason) for r, reason in decision.unsent] == [(DAY_3, MATERIALS_UNKNOWN)]


def test_a_second_run_the_same_day_changes_nothing_for_any_exam_day():
    for exam in a_year_of_exams():
        for n in range(25):
            today = exam + timedelta(days=n)
            first = decide_reminders(exam, today, materials_uploaded=False)
            if first.send is None:
                continue
            again = decide_reminders(
                exam, today, materials_uploaded=False, already_sent={first.send.key}
            )
            assert again.send is None and again.already_sent.key == first.send.key


# --- What a provider who does everything on time receives (rule 4) ----------


def test_a_provider_who_meets_every_deadline_gets_at_most_four_reminders():
    for exam in a_year_of_exams():
        sent = simulate(
            exam, uploaded_on=exam + timedelta(days=2), submitted_on=exam + timedelta(days=11)
        )
        keys = [key for _, key in sent]
        assert len(keys) <= 4, (exam, sent)
        assert set(keys) <= {DAY_1, DAY_5, DAY_8, DAY_10}, (exam, sent)


def test_a_provider_with_no_collisions_gets_exactly_day_1_5_8_and_10():
    sent = simulate(d(4), uploaded_on=d(6), submitted_on=d(15))  # a Monday exam
    assert sent == [(d(5), DAY_1), (d(11), DAY_5), (d(12), DAY_8), (d(14), DAY_10)]


def test_a_case_that_is_never_done_gets_the_overdue_notice_and_stops_there():
    sent = simulate(d(4), uploaded_on=d(6))
    assert sent[-1] == (d(15), DAY_11_OVERDUE)


# --- Arizona time -------------------------------------------------------------


def test_arizona_is_seven_hours_behind_utc_all_year():
    assert ARIZONA.utcoffset(datetime(2026, 1, 15)) == timedelta(hours=-7)
    assert ARIZONA.utcoffset(datetime(2026, 7, 15)) == timedelta(hours=-7)


def test_today_is_the_date_in_arizona_not_in_utc():
    late = datetime(2026, 5, 12, 6, 59, tzinfo=timezone.utc)   # 11:59 pm on May 11 in Arizona
    just_after = datetime(2026, 5, 12, 7, 0, tzinfo=timezone.utc)  # midnight on May 12
    assert arizona_today(late) == d(11)
    assert arizona_today(just_after) == d(12)


def test_today_is_the_same_in_arizona_whatever_zone_the_computer_is_in():
    pacific = timezone(timedelta(hours=-8))
    assert arizona_today(datetime(2026, 1, 20, 23, 30, tzinfo=pacific)) == date(2026, 1, 21)


# --- What the email says -------------------------------------------------------


def message(key, exam=d(4), send_on=None, name="Dr. Doe", case="15419897"):
    reminder = BY_KEY[key]
    return build_message(reminder, case, exam, send_on or send_date(exam, reminder), name)


@pytest.mark.parametrize("key", KEYS)
def test_every_email_names_the_case_the_exam_and_the_sender(key):
    subject, body = message(key)
    assert "15419897" in subject and "15419897" in body
    assert "Monday, May 4, 2026" in body
    assert body.startswith("Hello Dr. Doe,")
    assert body.endswith("Trinity Tree Psychological Services")


@pytest.mark.parametrize("key", KEYS)
def test_no_email_has_a_dash_or_asks_for_anything_about_the_patient(key):
    subject, body = message(key)
    assert "\u2014" not in subject + body and "patient" not in (subject + body).lower()


def test_without_a_provider_name_the_greeting_is_plain():
    assert message(DAY_5, name="")[1].startswith("Hello,\n")
    assert message(DAY_5, name="  ")[1].startswith("Hello,\n")


def test_day_1_gives_the_48_hour_target_and_the_report_due_date():
    subject, body = message(DAY_1)
    assert "behavioral observations and testing materials" in subject
    assert "48 hours" in body and "Thursday, May 14, 2026" in body


def test_day_5_gives_the_due_date():
    assert "due on Thursday, May 14, 2026" in message(DAY_5)[1]


def test_day_8_on_its_own_day_says_two_days():
    subject, body = message(DAY_8)  # Tuesday May 12, due Thursday May 14
    assert "due in 2 days" in subject and "due in 2 days" in body


def test_day_8_moved_to_a_friday_names_the_date_instead_of_saying_two_days():
    exam = d(8)  # Friday exam: Day 8 is Saturday, goes out Friday May 15, report due Monday May 18
    subject, body = message(DAY_8, exam=exam)
    assert "2 days" not in subject + body
    assert "due on Monday, May 18, 2026" in body


def test_day_10_on_the_due_day_says_today():
    subject, body = message(DAY_10)  # Thursday May 14, due Thursday May 14
    assert "due today" in subject and "due today" in body


def test_day_10_moved_back_to_a_friday_never_says_today():
    exam = d(7)  # Thursday exam: Day 10 is Sunday May 17, goes out Friday May 15
    subject, body = message(DAY_10, exam=exam)
    assert "today" not in subject + body
    assert "due on Sunday, May 17, 2026" in body


def test_the_overdue_notice_says_it_was_due_and_copies_the_director():
    subject, body = message(DAY_11_OVERDUE)
    assert subject.startswith("OVERDUE")
    assert "was due on Thursday, May 14, 2026" in body and "Director is copied" in body


def test_an_unknown_reminder_is_refused():
    with pytest.raises(ValueError, match="Unknown reminder"):
        build_message(Reminder("DAY_99", 99, "x", "back"), "1", d(4), d(5), "")