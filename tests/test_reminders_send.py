"""
Tests for app/reminders_send.py: the loop that sends a plan's reminders, one at a time.

The email and the log are stand-ins that record what they were asked to do, in order,
so the tests can check the sequence (send, then log, then the next send), and make
either one fail where the test needs it to.

Run from the project root:
    python -m pytest tests/test_reminders_send.py -v
"""

from datetime import date, datetime

import pytest

from app.mail_sender import MailError
from app.reminder_rules import ARIZONA
from app.reminders import plan_reminders
from app.reminders_send import TEST_PREFIX, deliver, notice_for_test
from app.reminders_sheet import Case, ReminderData


TODAY = date(2026, 5, 11)  # a Monday
STAMP = datetime(2026, 5, 11, 8, 30, 5, tzinfo=ARIZONA)
DIRECTOR = "director.copy.7310@example.test"
TESTER = "tester.inbox.8812@example.test"


def address(case_id):
    return f"provider.{case_id}@example.test"


def case(case_id, exam="May 4th, 2026 08:00 AM MST", name=None):
    return Case(case_id, 2, exam, name or f"Dr. {case_id}", address(case_id))


def plan_of(*cases):
    """A plan for TODAY. Exam May 4 is due Day 5 today; exam April 30 is due the Day 11 notice."""
    return plan_reminders(ReminderData(cases=list(cases)), TODAY)


class Events:
    """Records sends and log rows in the order they happen."""

    def __init__(self, fail_for=(), log_fails_at=None):
        self.events = []
        self.fail_for = set(fail_for)
        self.log_fails_at = log_fails_at
        self.rows = []

    def send(self, to, subject, body, cc):
        if to in self.fail_for:
            raise MailError("Microsoft refused the email (HTTP 400, ErrorInvalidRecipients)")
        self.events.append(("send", to, subject, body, cc))

    def record(self, case_id, key, recipient, when):
        if self.log_fails_at == len(self.rows) + 1:
            raise RuntimeError("the sheet could not be reached")
        self.rows.append((case_id, key, recipient, when))
        self.events.append(("log", case_id, key))

    def kinds(self):
        return [e[0] for e in self.events]


class Lines:
    """Stands in for the logger."""

    def __init__(self):
        self.lines = []

    def info(self, message, *args):
        self.lines.append(("info", message % args))

    def error(self, message, *args):
        self.lines.append(("error", message % args))


def run(plan, events, **kwargs):
    log = kwargs.pop("log", events)
    return deliver(plan, events.send, log=log, stamp=lambda: STAMP, **kwargs)


# --- The normal run ---------------------------------------------------------------


def test_each_reminder_goes_to_its_provider_in_the_plans_order():
    events = Events()
    result = run(plan_of(case("111"), case("222")), events)
    assert [e[1] for e in events.events if e[0] == "send"] == [address("111"), address("222")]
    assert [p.case.case_id for p in result.sent] == ["111", "222"]
    assert result.failed == [] and result.not_attempted == [] and result.log_problem == ""


def test_the_subject_and_text_are_the_plans_own():
    events = Events()
    plan = plan_of(case("111"))
    run(plan, events)
    (_, _, subject, body, cc) = events.events[0]
    assert (subject, body) == (plan.to_send[0].subject, plan.to_send[0].body)
    assert cc == ()


def test_each_email_is_logged_straight_after_it_goes_and_before_the_next_one():
    events = Events()
    run(plan_of(case("111"), case("222"), case("333")), events)
    assert events.kinds() == ["send", "log", "send", "log", "send", "log"]


def test_a_row_has_the_case_the_reminder_the_providers_name_and_the_time():
    events = Events()
    run(plan_of(case("111", name="Dr. Doe")), events)
    assert events.rows == [("111", "DAY_5", "Dr. Doe", STAMP)]


def test_the_overdue_notice_copies_the_director_and_the_others_do_not():
    events = Events()
    run(plan_of(case("111"), case("222", exam="April 30th, 2026")), events, cc_emails=[DIRECTOR])
    copies = {e[1]: e[4] for e in events.events if e[0] == "send"}
    assert copies == {address("111"): (), address("222"): (DIRECTOR,)}


def test_an_empty_plan_sends_and_logs_nothing():
    events = Events()
    result = run(plan_of(), events)
    assert events.events == [] and result.sent == []


# --- A send that fails --------------------------------------------------------------


def test_a_failed_send_is_reported_with_its_reason_and_the_rest_carry_on():
    events = Events(fail_for=[address("222")])
    result = run(plan_of(case("111"), case("222"), case("333")), events)
    assert [p.case.case_id for p in result.sent] == ["111", "333"]
    ((failed, reason),) = result.failed
    assert failed.case.case_id == "222" and "ErrorInvalidRecipients" in reason


def test_a_failed_send_is_not_logged_so_the_next_run_tries_it_again():
    events = Events(fail_for=[address("222")])
    run(plan_of(case("111"), case("222"), case("333")), events)
    assert [r[0] for r in events.rows] == ["111", "333"]


def test_a_bug_is_not_mistaken_for_a_failed_send_and_what_went_before_is_still_logged():
    events = Events()

    def send(to, subject, body, cc):
        if to == address("222"):
            raise RuntimeError("a bug")
        events.send(to, subject, body, cc)

    with pytest.raises(RuntimeError):
        deliver(plan_of(case("111"), case("222")), send, log=events, stamp=lambda: STAMP)
    assert [r[0] for r in events.rows] == ["111"]


# --- A log that can't be written ----------------------------------------------------


def test_when_a_row_cant_be_written_the_run_stops_and_the_rest_are_not_attempted():
    events = Events(log_fails_at=2)
    result = run(plan_of(case("111"), case("222"), case("333"), case("444")), events)
    assert [e[1] for e in events.events if e[0] == "send"] == [address("111"), address("222")]
    assert [p.case.case_id for p in result.sent] == ["111", "222"]  # 222 did go out
    assert [p.case.case_id for p in result.not_attempted] == ["333", "444"]


def test_the_message_says_which_email_to_log_by_hand():
    result = run(plan_of(case("111"), case("222")), Events(log_fails_at=2))
    message = result.log_problem
    assert "Case 222" in message and "DAY_5" in message and "by hand" in message
    assert "sent again" in message or "send it again" in message
    assert "RuntimeError" in message


def test_a_failed_log_on_the_last_email_leaves_nothing_not_attempted_but_still_reports_it():
    result = run(plan_of(case("111")), Events(log_fails_at=1))
    assert result.log_problem and result.not_attempted == []


# --- A test run ---------------------------------------------------------------------


def test_a_test_run_sends_everything_to_the_test_address_with_nobody_copied():
    events = Events()
    run(plan_of(case("111"), case("222", exam="April 30th, 2026")), events, test_recipient=TESTER, cc_emails=[DIRECTOR])
    sends = [e for e in events.events if e[0] == "send"]
    assert [(e[1], e[4]) for e in sends] == [(TESTER, ()), (TESTER, ())]


def test_a_test_email_is_marked_in_the_subject_and_says_who_it_would_have_gone_to():
    events = Events()
    plan = plan_of(case("111", name="Dr. Doe"))
    run(plan, events, test_recipient=TESTER)
    (_, _, subject, body, _) = events.events[0]
    assert subject == TEST_PREFIX + plan.to_send[0].subject and subject.startswith("[TEST] ")
    assert body.startswith("[TEST] This reminder would have gone to Dr. Doe.")
    assert body.endswith(plan.to_send[0].body)


def test_the_test_notice_for_the_overdue_notice_says_the_director_would_have_been_copied():
    planned = plan_of(case("222", exam="April 30th, 2026", name="Dr. Smith")).to_send[0]
    assert "would have gone to Dr. Smith, with the Director copied." in notice_for_test(planned)


def test_the_test_notice_calls_an_unnamed_provider_the_provider():
    planned = plan_of(Case("111", 2, "May 4th, 2026", "", address("111"))).to_send[0]
    assert "gone to the provider." in notice_for_test(planned)


def test_a_test_run_never_logs_even_when_a_log_is_passed():
    events = Events()
    run(plan_of(case("111"), case("222")), events, test_recipient=TESTER)
    assert events.rows == [] and "log" not in events.kinds()


# --- The last line of defence -------------------------------------------------------


def test_an_overdue_notice_with_nobody_to_copy_is_not_sent_even_if_the_plan_let_it_through():
    events = Events()
    result = run(plan_of(case("222", exam="April 30th, 2026")), events, cc_emails=())
    assert events.events == []
    ((planned, reason),) = result.failed
    assert "Director" in reason


# --- What is logged ------------------------------------------------------------------


def test_the_log_has_a_line_for_each_email_with_the_name_and_never_the_address():
    events, lines = Events(fail_for=[address("222")]), Lines()
    run(plan_of(case("111", name="Dr. Doe"), case("222", name="Dr. Smith")), events, logger=lines)
    text = "\n".join(f"{level}: {message}" for level, message in lines.lines)
    assert ("info", "Sent Case 111: Day 5 report check-in to Dr. Doe") in lines.lines
    assert any(level == "error" and "Case 222" in m and "Dr. Smith" in m for level, m in lines.lines)
    for case_id in ("111", "222"):
        assert address(case_id) not in text


def test_a_test_email_is_logged_as_a_test():
    lines = Lines()
    run(plan_of(case("111", name="Dr. Doe")), Events(), logger=lines, test_recipient=TESTER)
    assert lines.lines == [("info", "Test email for Case 111: Day 5 report check-in to Dr. Doe")]
    assert TESTER not in str(lines.lines)