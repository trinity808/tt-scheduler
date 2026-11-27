"""
Works out which reminders are due today, and prints who would be emailed.

This command only previews. It reads the Ongoing, Finished and Reminders Sent
tabs, applies Dr.'s schedule (see reminder_rules.py), and prints a report. It
sends nothing and writes nothing, to the sheet or anywhere else but its own log
files. Sending comes later, as a separate step with its own switch.

Run from the project root:
    python -m app.reminders                     today's reminders, in Arizona time
    python -m app.reminders --today 2026-11-23  as if today were that date
    python -m app.reminders --show-text         also print each email's text
    add --pause to wait for Enter at the end, for a double-clicked shortcut.

Order matters. Run it AFTER the sweep (which fills each case's Provider Email) and
after the screenshot matcher (which moves a case to Finished once its report is
in). A reminder is decided from the sheet as it is when this runs, so a case
whose report is in but hasn't been moved yet would still be reminded.

For each open case that isn't in Finished: the exam date is read from Date & Time,
and the schedule says whether a reminder is due today. A reminder that is due is
one of:
  would send       it has an address to go to
  needs attention  it is due but can't go: the exam date can't be read, no provider
                   is named, or the provider has no usable address
  already sent     the Reminders Sent tab says it went already
  not sent         due, but held back on purpose, with the reason
Cases with nothing due today are only counted.

The report never shows an email address, only names, and neither does the log.
It writes logs/reminders.log, and logs/reminders_error.log for failures: when it
started, the whole report as one record (a warning if anything needs attention),
and when it finished, with counts. A failure is logged with its traceback, the
usual alert email is sent if emails are enabled, and the error is raised again so
the process still exits with an error. Separate files from the sweep's and the
pipeline's, since two processes must not write to one rotating file.
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from app.config_loader import load_config
from app.exam_date import parse_exam_date
from app.health_monitor import notify_error, setup_logging
from app.reminder_rules import arizona_today, build_message, decide_reminders
from app.reminders_sheet import PROVIDER_EMAIL, read_reminder_data


REMINDERS_LOG = "reminders.log"
REMINDERS_ERROR_LOG = "reminders_error.log"


@dataclass
class Planned:
    """One reminder that is due today and can go out."""

    case: object        # a reminders_sheet.Case
    exam_date: object
    reminder: object    # a reminder_rules.Reminder
    subject: str
    body: str


@dataclass
class RunPlan:
    today: object
    looked_at: int = 0                              # open cases considered
    to_send: list = field(default_factory=list)     # Planned, in sheet order
    problems: list = field(default_factory=list)    # "Case 111: ..." that need attention
    already_sent: list = field(default_factory=list)  # (case_id, Reminder)
    not_sent: list = field(default_factory=list)    # (case_id, Reminder, reason)
    finished: int = 0                               # open cases skipped: in Finished
    nothing_due: int = 0
    notes: list = field(default_factory=list)       # things about the sheet itself
    log_found: bool = True


def _valid_address(text):
    return bool(text) and text.count("@") == 1 and "." in text.split("@")[1] and " " not in text


def _cannot_send(case, reminder):
    """Why a due reminder has nowhere to go. Never contains an address."""
    if not case.provider_email:
        if not case.provider_name:
            why = "no provider is named for this case in the comments"
        else:
            why = (
                f"{case.provider_name} has no email address "
                "(check the Contacts tab, and the note on the Comments cell)"
            )
    else:
        why = f"the {PROVIDER_EMAIL} isn't a valid address"
    return f"Case {case.case_id}: {reminder.label} is due today but can't be sent: {why}"


def plan_reminders(data, today):
    """The RunPlan for `today`, from a ReminderData (see reminders_sheet.py)."""
    plan = RunPlan(today=today, notes=list(data.notes), log_found=data.log_found)

    sent_by_case = {}
    for case_id, key in data.sent:
        sent_by_case.setdefault(case_id, set()).add(key)

    for case in data.cases:
        plan.looked_at += 1
        if case.case_id in data.finished_ids:
            plan.finished += 1
            continue

        try:
            exam_date = parse_exam_date(case.date_time_text)
        except ValueError as error:
            plan.problems.append(
                f"Case {case.case_id}: the exam date can't be read from Date & Time ({error})"
            )
            continue

        decision = decide_reminders(
            exam_date,
            today,
            report_submitted=False,  # a case in Finished never gets here
            materials_uploaded=None,  # nothing tracks this yet
            already_sent=sent_by_case.get(case.case_id, ()),
        )

        for reminder, reason in decision.unsent:
            plan.not_sent.append((case.case_id, reminder, reason))

        if decision.already_sent:
            plan.already_sent.append((case.case_id, decision.already_sent))
        elif decision.send:
            if not _valid_address(case.provider_email):
                plan.problems.append(_cannot_send(case, decision.send))
            else:
                subject, body = build_message(
                    decision.send, case.case_id, exam_date, today, case.provider_name
                )
                plan.to_send.append(Planned(case, exam_date, decision.send, subject, body))
        elif not decision.unsent:
            plan.nothing_due += 1

    return plan


def run_reminders(spreadsheet, today):
    """Reads the sheet and plans today's reminders. Writes nothing."""
    return plan_reminders(read_reminder_data(spreadsheet), today)


def _date_argument(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} isn't a date written like 2026-11-23") from None


def main(argv=None, spreadsheet=None, config=None, now=None, pause=None):
    """
    The command line: work out today's reminders, print the report, and log the run.

    It only previews. `spreadsheet` and `config` are for tests; left out, they are the
    real ones. `now` is the current time, for tests. `pause` decides whether to wait for
    Enter at the end; left out, it follows the --pause flag, which is off unless given.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Show which reminders are due today and who would get them. "
            "Sends nothing and writes nothing to the sheet."
        )
    )
    parser.add_argument(
        "--today",
        type=_date_argument,
        metavar="YYYY-MM-DD",
        help="Work out the reminders as if this were today's date (Arizona).",
    )
    parser.add_argument(
        "--show-text",
        action="store_true",
        help="Also print the text of each email that would be sent.",
    )
    parser.add_argument(
        "--pause",
        action="store_true",
        help="Wait for Enter before closing, so a double-clicked window stays open.",
    )
    args = parser.parse_args(argv)

    # Logging comes first, so even a failure to read the settings or to reach
    # Google leaves a trace.
    config_problem = None
    if config is None:
        try:
            config = load_config()
        except Exception as error:
            config, config_problem = {}, error
    logger = setup_logging(config, REMINDERS_LOG, REMINDERS_ERROR_LOG)
    if config_problem:
        logger.warning(
            "Could not read config/settings.json (%s), so the default log "
            "folder is used.",
            config_problem,
        )

    today = args.today or arizona_today(now)
    logger.info("Reminders started (preview, for %s)", today)
    started = time.monotonic()

    try:
        if spreadsheet is None:
            from app.sheet_client import get_client

            spreadsheet = get_client()

        from app.reminders_report import format_report, summary_line

        plan = run_reminders(spreadsheet, today)
        text = format_report(plan, show_text=args.show_text, today_given=args.today is not None)

        if plan.problems or plan.notes:
            logger.warning(
                "Reminders report (preview), %s need(s) attention:\n%s",
                len(plan.problems) + len(plan.notes),
                format_report(plan, today_given=args.today is not None),
            )
        else:
            logger.info(
                "Reminders report (preview):\n%s",
                format_report(plan, today_given=args.today is not None),
            )
        logger.info(summary_line(plan, time.monotonic() - started))

        print(text)
    except Exception as error:
        logger.exception("Reminders failed (preview)")
        notify_error(
            "Reminders failed", f"The reminders failed (preview): {error}", config, sys.exc_info()
        )
        raise

    if pause is None:
        pause = args.pause
    if pause:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass  # nothing to read from (closed or empty input): just finish


if __name__ == "__main__":
    main()