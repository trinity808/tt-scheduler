"""
Works out which reminders are due today, and either shows who would be emailed or
sends them all to a test address.

By default this only previews. It reads the Ongoing, Finished and Reminders Sent
tabs, applies Dr.'s schedule (see reminder_rules.py), and prints a report. It sends
nothing and writes nothing, to the sheet or anywhere else but its own log files.

Run from the project root:
    python -m app.reminders                     preview today's reminders (Arizona time)
    python -m app.reminders --today 2026-11-23  as if today were that date
    python -m app.reminders --show-text         also print each email's text
    python -m app.reminders --live --test       send them ALL to the test address
    add --pause to wait for Enter at the end, for a double-clicked shortcut.

--live --test sends every email to REMINDERS_TEST_RECIPIENT in .env instead of to the
provider, marked [TEST], with nobody copied. It is how to try the sending. It writes
nothing to the sheet. --live on its own, which would email the providers, is not
available yet and is refused. --today works with --test as it does on a preview. The
four Microsoft settings must be in .env (see mail_sender.py). A run that finds one
missing, or any other safeguard unmet, refuses to start and says why, before touching
the sheet.

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

A live test sends one email at a time (see reminders_send.py). A failed send is
reported and the rest carry on. A live run takes reminders.lock first, so a second one
started at the same time does nothing; the lock covers one computer, not two.

The report never shows an email address, only names, and neither does the log.
It writes logs/reminders.log, and logs/reminders_error.log for failures: when it
started, the whole report as one record (a warning if anything needs attention),
one line for each email sent or failed, and when it finished, with counts. A failure
is logged with its traceback, the usual alert email is sent if emails are enabled,
and the error is raised again so the process still exits with an error. A run in
which some sends failed prints its report, sends the alert, and exits with code 1.
Separate files from the sweep's and the pipeline's, since two processes must not
write to one rotating file.
"""

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime

from app.config_loader import load_config
from app.exam_date import parse_exam_date
from app.health_monitor import notify_error, setup_logging
from app.mail_sender import MailError, environment, get_access_token, read_mail_settings, send_email
from app.reminder_rules import arizona_today, build_message, decide_reminders
from app.reminders_send import SendResult, deliver
from app.reminders_sheet import PROVIDER_EMAIL, read_reminder_data
from app.run_lock import LockHeld, acquire, describe, minutes_from_config, release


REMINDERS_LOG = "reminders.log"
REMINDERS_ERROR_LOG = "reminders_error.log"
REMINDERS_LOCK = "reminders.lock"
LOCK_STALE_SETTING = "reminders_lock_stale_minutes"

TEST_RECIPIENT = "REMINDERS_TEST_RECIPIENT"


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


def _refusals(args, env):
    """What stops a live run from starting, as a list of sentences. Empty if it can start."""
    problems = []
    if args.test:
        if not _valid_address((env.get(TEST_RECIPIENT) or "").strip()):
            problems.append(
                f"--test needs {TEST_RECIPIENT} in .env, set to an address you can read."
            )
    else:
        problems.append(
            "Sending to the providers isn't available in this version. "
            f"Use --live --test to send every email to {TEST_RECIPIENT} instead."
        )
    try:
        read_mail_settings(env)
    except MailError as error:
        problems.append(str(error))
    return problems


def _take_lock(logger, config, mode):
    """
    The reminders' lock, or None if another live run holds it, which has been logged and
    printed. Anything else that goes wrong, such as a folder that can't be written to,
    is raised so the run fails visibly. Same pattern as the sweep's.
    """
    minutes, problem = minutes_from_config(config, key=LOCK_STALE_SETTING)
    if problem:
        logger.warning(problem)

    try:
        lock = acquire(REMINDERS_LOCK, mode, minutes * 60)
    except LockHeld as held:
        logger.warning("Reminders skipped (%s): another live run is going (%s)", mode, held)
        print(f"Another live reminders run is already going ({held}), so this run did nothing.")
        return None

    if lock.taken_over is not None:
        logger.warning(
            "Took over a stale reminders lock (%s), older than the %s-minute limit.",
            describe(lock.taken_over, lock.taken_over_age),
            minutes,
        )
    return lock


def _release_lock(logger, lock):
    """Gives the lock back. It must never raise, or it would hide a real error."""
    try:
        if not release(lock):
            logger.warning(
                "The reminders lock was no longer this run's when it finished. It was "
                "probably taken over because this run outlasted the stale limit."
            )
    except Exception:
        logger.exception("Could not release the reminders lock")


def _send_plan(plan, env, post, logger):
    """The live part: sign in, then send each email to the test address. Returns a SendResult."""
    if not plan.to_send:
        return SendResult()  # nothing due: no need to sign in

    settings = read_mail_settings(env)
    token = get_access_token(settings, post)

    def send(to, subject, body, cc):
        send_email(settings, token, to, subject, body, cc, post)

    return deliver(
        plan,
        send,
        test_recipient=(env.get(TEST_RECIPIENT) or "").strip(),
        logger=logger,
    )


def _finish(pause, args, exit_code):
    """Waits for Enter if asked to, then exits with `exit_code` unless it is 0."""
    if pause is None:
        pause = args.pause
    if pause:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass  # nothing to read from (closed or empty input): just finish
    if exit_code:
        sys.exit(exit_code)


def main(argv=None, spreadsheet=None, config=None, now=None, pause=None, environ=None, post=None):
    """
    The command line: work out today's reminders, print the report, and log the run.

    It previews unless given --live, which for now works only with --test (see the top of
    this file). `spreadsheet` and `config` are for tests; left out, they are the real
    ones. `now` is the current time, for tests. `environ` is the settings (.env), and
    `post` is the function that makes web requests, both for tests. `pause` decides whether
    to wait for Enter at the end; left out, it follows the --pause flag, which is off
    unless given.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Show which reminders are due today and who would get them. Previews unless "
            f"given --live --test, which sends every email to {TEST_RECIPIENT} instead."
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
        help="Also print the text of each email.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Send the emails. For now only together with --test.",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help=f"With --live: send every email to {TEST_RECIPIENT} instead of the provider, copying nobody.",
    )
    parser.add_argument(
        "--pause",
        action="store_true",
        help="Wait for Enter before closing, so a double-clicked window stays open.",
    )
    args = parser.parse_args(argv)
    if args.test and not args.live:
        parser.error("--test only works together with --live")
    mode = "live test" if args.test else "live" if args.live else "preview"

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

    env = environment(environ) if args.live else None
    today = args.today or arizona_today(now)

    if args.live:
        refusals = _refusals(args, env)
        if refusals:
            logger.warning("Reminders refused (%s): %s", mode, " ".join(refusals))
            print("Nothing was done.\n" + "\n".join(f"  - {problem}" for problem in refusals))
            _finish(pause, args, 2)
            return

    lock = None
    if args.live:
        lock = _take_lock(logger, config, mode)
        if lock is None:
            _finish(pause, args, 0)
            return

    logger.info("Reminders started (%s, for %s)", mode, today)
    started = time.monotonic()
    exit_code = 0

    try:
        if spreadsheet is None:
            from app.sheet_client import get_client

            spreadsheet = get_client()

        from app.reminders_report import format_report, summary_line

        plan = run_reminders(spreadsheet, today)

        result = None
        if args.live:
            result = _send_plan(plan, env, post, logger)

        text = format_report(
            plan, show_text=args.show_text, today_given=args.today is not None, result=result, mode=mode
        )
        failed = result is not None and bool(result.failed)

        if plan.problems or plan.notes or failed:
            logger.warning(
                "Reminders report (%s), %s need(s) attention:\n%s",
                mode,
                len(plan.problems) + len(plan.notes) + (len(result.failed) if result else 0),
                format_report(plan, today_given=args.today is not None, result=result, mode=mode),
            )
        else:
            logger.info(
                "Reminders report (%s):\n%s",
                mode,
                format_report(plan, today_given=args.today is not None, result=result, mode=mode),
            )
        logger.info(summary_line(plan, time.monotonic() - started, result, mode))

        print(text)

        if failed:
            exit_code = 1
            notify_error(
                "Reminders failed",
                f"The reminders ({mode}) did not all go out: {len(result.failed)} failed. "
                "See reminders_error.log.",
                config,
                sys.exc_info(),
            )
    except Exception as error:
        logger.exception("Reminders failed (%s)", mode)
        notify_error(
            "Reminders failed", f"The reminders failed ({mode}): {error}", config, sys.exc_info()
        )
        raise
    finally:
        if lock is not None:
            _release_lock(logger, lock)

    _finish(pause, args, exit_code)


if __name__ == "__main__":
    main()