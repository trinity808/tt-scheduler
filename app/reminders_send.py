"""
Sends the reminders a RunPlan says are due, and logs each one as it goes.

deliver() is the loop, kept apart from the web request and from the sheet so it can
be tested with stand-ins for both: `send` is any function taking (to, subject, body,
cc) that raises MailError when the email didn't go, and `log` is a SentLog (or None).

For each reminder, in the plan's order:
  1. Work out who it goes to. Normally the provider, and the Director is copied on
     the overdue notice. In a test run (test_recipient given) it goes to that one
     address instead, nobody is copied, the subject starts with [TEST], and the first
     lines of the email say who it would really have gone to.
  2. Send it. If that fails, note why and carry on with the next one. Nothing is
     logged for it, so the next run tries again.
  3. Once it is accepted, write its row to the Reminders Sent tab straight away.
     If the row can't be written, STOP: the email has gone but the next run would
     not know, and every further send would add to the problem. What is left is
     reported as not attempted, and the message says which email to log by hand.

A test run (test_recipient given) never writes a row, even if a log is passed, so a real
run later still sends.

Nothing here handles an address except to pass it to `send`. Names are what get
logged and reported.
"""

from dataclasses import dataclass, field

from app.mail_sender import MailError


TEST_PREFIX = "[TEST] "


@dataclass
class SendResult:
    sent: list = field(default_factory=list)           # Planned, in order
    failed: list = field(default_factory=list)         # (Planned, reason)
    not_attempted: list = field(default_factory=list)  # Planned, left over after a log failure
    log_problem: str = ""                              # set if the run stopped over the log


def _name(planned):
    return planned.case.provider_name or "the provider"


def notice_for_test(planned):
    """The lines put above a test email, saying where the real one would have gone."""
    copy = ", with the Director copied" if planned.reminder.cc_director else ""
    return (
        f"[TEST] This reminder would have gone to {_name(planned)}{copy}. "
        "Nothing was written to the Reminders Sent tab.\n\n"
    )


def _label(planned):
    return f"Case {planned.case.case_id}: {planned.reminder.label}"


def deliver(plan, send, log=None, stamp=None, test_recipient=None, cc_emails=(), logger=None):
    """
    Sends everything in plan.to_send and returns a SendResult. `stamp` is a function
    returning the current Arizona datetime, for the log rows. `logger` is for one line
    per email sent or failed.
    """
    result = SendResult()

    for index, planned in enumerate(plan.to_send):
        subject, body = planned.subject, planned.body
        if test_recipient:
            to, cc = test_recipient, ()
            subject = TEST_PREFIX + subject
            body = notice_for_test(planned) + body
        else:
            to = planned.case.provider_email
            cc = tuple(cc_emails) if planned.reminder.cc_director else ()
            if planned.reminder.cc_director and not cc:
                # The plan already holds these back. This is the last line of defence.
                reason = "the Director's address to copy isn't set"
                result.failed.append((planned, reason))
                if logger:
                    logger.error("%s was not sent: %s", _label(planned), reason)
                continue

        try:
            send(to, subject, body, cc)
        except MailError as error:
            result.failed.append((planned, str(error)))
            if logger:
                logger.error("%s to %s was not sent: %s", _label(planned), _name(planned), error)
            continue

        result.sent.append(planned)
        if logger:
            kind = "Test email for" if test_recipient else "Sent"
            logger.info("%s %s to %s", kind, _label(planned), _name(planned))

        if log is not None and not test_recipient:  # a test email is never logged as sent
            try:
                log.record(planned.case.case_id, planned.reminder.key, planned.case.provider_name, stamp())
            except Exception as error:
                result.log_problem = (
                    f"{_label(planned)} was sent, but writing it to the Reminders Sent tab failed "
                    f"({type(error).__name__}: {error}). Add a row for it by hand "
                    f"(Case ID {planned.case.case_id}, Reminder {planned.reminder.key}), or the next run will "
                    "send it again. No further reminders were sent."
                )
                result.not_attempted = list(plan.to_send[index + 1 :])
                if logger:
                    logger.error(result.log_problem)
                break

    return result