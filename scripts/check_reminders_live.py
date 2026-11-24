"""
Live check of the reminders, run through the real Google Sheets API and, for the
groups that send, the real Microsoft account.

It works on scratch tabs that it creates in the sandbox sheet, and deletes them at
the end. It never touches a real tab: the reminders command is handed a stand-in
spreadsheet that maps "Ongoing", "Finished" and "Reminders Sent" to scratch tabs
and refuses any other name. The only real tab it reads is Contacts, once, to find
the two test providers. Every case it checks is made up here.

Who it can email. It uses two providers, named with --providers (the default is
"Dr. Jane Smith" and "Dr. John Doe"), and takes their addresses from the sandbox
Contacts tab, through the same lookup the sweep uses. Whoever runs it puts their own
addresses on those two rows first. Nothing else is ever emailed: every request to
Microsoft goes through a guard that stops an email addressed to anyone else, whatever
the rest of the script does.

Its settings are given to the command in memory. Your .env is read for the Microsoft
settings and never changed, and three settings are replaced for the run: real sending
is switched on, the test address is the first provider, and the Director copy goes to
the second provider, so a Director address in .env is never emailed.

It refuses to run unless the sheet's title contains "sandbox", and on a weekend, when
no reminder is sent. A group that sends real email also waits for you to type yes,
after showing the sheet and the two addresses.

Every run starts from nothing: it removes scratch tabs left by an earlier failed run,
builds fresh ones, and deletes them at the end. The command runs from a temporary
folder, so its lock file and logs never touch the project folder.

Google limits a user to 60 read requests a minute, so every request goes through the
same pacer the sweep check uses.

STEPS SO FAR: set-up and clean-up, the guards, the stand-in spreadsheet, the cases, and
the "plumbing" group, which runs a preview on the cases and sends nothing.

Run from the project root:
    python -m scripts.check_reminders_live --plan      what it would do, touching nothing
    python -m scripts.check_reminders_live --list      the groups of scenarios
    python -m scripts.check_reminders_live             everything
    python -m scripts.check_reminders_live --only plumbing
"""

import argparse
import contextlib
import io
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import timedelta

import gspread
import requests
from gspread.utils import rowcol_to_a1

from app.contacts_tab import load_contacts_tab
from app.mail_sender import MailError, environment, read_mail_settings
from app.reminder_rules import BY_KEY, DAY_1, DAY_5, DAY_10, DAY_11_OVERDUE, arizona_today, decide_reminders
from app.reminders import main as reminders_main
from app.reminders_sheet import (
    CASE_ID,
    DATE_TIME,
    FINISHED_TAB,
    ONGOING_TAB,
    PROVIDER,
    PROVIDER_EMAIL,
    SENT_TAB,
)


PREFIX = "ReminderCheck "

DEFAULT_PROVIDERS = ("Dr. Jane Smith", "Dr. John Doe")

ONGOING_HEADERS = [
    CASE_ID, "Patient Name", DATE_TIME, PROVIDER, PROVIDER_EMAIL, "Psychometrist", "Psychometrist Email",
]
FINISHED_HEADERS = [CASE_ID, "Patient Name", PROVIDER]

NOT_A_PATIENT = "LIVE CHECK (not a patient)"

TOKEN_URL_START = "https://login.microsoftonline.com/"
GRAPH_URL_START = "https://graph.microsoft.com/"

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    line = f"  [{'OK' if ok else 'FAILED'}] {label}"
    if not ok and detail:
        line += f"  ->  {detail}"
    print(line)


class Refusal(Exception):
    """The run must not start. `problems` says why, in words with no email address in them."""

    def __init__(self, problems):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


# --- The two test providers ----------------------------------------------


@dataclass
class Person:
    listed: str    # as the Contacts tab lists them, such as "Dr. Jane Smith"
    typed: str     # as OA writes them in a comment, such as "Dr. Smith"
    email: str = field(repr=False)


def typed_name(listed):
    """'Dr. Jane Smith' -> 'Dr. Smith': the way a comment names them, last name only."""
    words = listed.replace(",", " ").split()
    return f"Dr. {words[-1]}" if words else ""


def resolve_providers(spreadsheet, names):
    """
    The two test providers, found the way the sweep finds a provider: by the name as a
    comment would write it, in the Contacts tab. Raises Refusal, with no address in its
    words, if either can't be used. `spreadsheet` is the real sheet, and only its
    Contacts tab is read.
    """
    if len(names) != 2:
        raise Refusal(["Two provider names are needed, for example --providers \"Dr. Jane Smith, Dr. John Doe\""])

    tab = load_contacts_tab(spreadsheet)
    if tab.lookup is None:
        raise Refusal(list(tab.problems))

    problems, people = [], []
    for listed in names:
        by_full_name = tab.lookup.find(listed, role="provider")
        if by_full_name.status != "found":
            problems.append(
                f"'{listed}' is not in the Contacts tab as a Provider ({by_full_name.status}). "
                "Add them, with an address of your own, and run this again"
            )
            continue
        typed = typed_name(listed)
        by_comment = tab.lookup.find(typed, role="provider")
        if by_comment.status != "found" or by_comment.email != by_full_name.email:
            problems.append(
                f"'{typed}', which is how a comment would name '{listed}', doesn't find them "
                f"in Contacts ({by_comment.status}), so the check would not match the sweep"
            )
            continue
        people.append(Person(listed=listed, typed=typed, email=by_full_name.email))

    if problems:
        raise Refusal(problems)
    if people[0].email.casefold() == people[1].email.casefold():
        raise Refusal(["The two providers have the same address in Contacts. They need different ones"])
    return people


# --- The guards and the settings -----------------------------------------


def check_sandbox(title):
    if "sandbox" not in str(title).casefold():
        raise Refusal([
            f"The sheet is titled '{title}', and a title without the word 'sandbox' is not "
            "taken to be the sandbox. This check never runs anywhere else"
        ])


def check_weekday(today):
    if today.weekday() >= 5:
        raise Refusal([
            f"Today is a {today:%A}, and reminders are never sent on a weekend, so none of "
            "the check's reminders would be due. Run it on a weekday"
        ])


def build_env(real_env, people):
    """
    The settings the command is given, as a new dict. The Microsoft settings come from
    `real_env` (.env), and must all be there. Three settings are replaced whatever .env
    says: real sending is on, the test address is the first provider, and the Director
    copy goes to the second. Raises Refusal naming a missing Microsoft setting.
    """
    env = dict(real_env)
    try:
        read_mail_settings(env)
    except MailError as error:
        raise Refusal([str(error)]) from None
    env["REMINDERS_GO_LIVE"] = "true"
    env["REMINDERS_TEST_RECIPIENT"] = people[0].email
    env["STAFF_CC_EMAILS"] = people[1].email
    return env


def allowed_addresses(people):
    return {person.email.casefold() for person in people}


# --- The guard on every request to Microsoft -------------------------------


class Blocked(Exception):
    """A request the guard stopped. Its words never include the address."""


@dataclass
class Sent:
    to: list = field(repr=False)
    cc: list = field(repr=False)
    subject: str
    status: object


@dataclass
class Outbox:
    sent: list = field(default_factory=list)
    tokens: int = 0


def guarded_post(allowed, outbox, real_post=None):
    """
    The `post` the command is given. Only two kinds of request pass: a sign-in, and an
    email whose every recipient, copies included, is in `allowed`. Anything else raises
    Blocked before it leaves. What passes is recorded in `outbox`.
    """

    def post(url, **kwargs):
        if url.startswith(TOKEN_URL_START):
            outbox.tokens += 1
            return (real_post or requests.post)(url, **kwargs)
        if url.startswith(GRAPH_URL_START) and url.endswith("/sendMail"):
            message = kwargs["json"]["message"]
            to = [r["emailAddress"]["address"] for r in message.get("toRecipients", [])]
            cc = [r["emailAddress"]["address"] for r in message.get("ccRecipients", [])]
            if not to or any(address.casefold() not in allowed for address in to + cc):
                raise Blocked(
                    "an email was about to go to an address that is not one of the two test "
                    "addresses, so it was stopped before it was sent"
                )
            reply = (real_post or requests.post)(url, **kwargs)
            outbox.sent.append(Sent(to=to, cc=cc, subject=message.get("subject", ""), status=getattr(reply, "status_code", None)))
            return reply
        raise Blocked("a request was about to go somewhere other than Microsoft's sign-in or sendMail, so it was stopped")

    return post


# --- The cases ---------------------------------------------------------------


def _ordinal(day):
    if 10 <= day % 100 <= 20:
        ending = "th"
    else:
        ending = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{ending}"


def exam_text(exam):
    """The exam's Date & Time cell, worded the way the schedule PDF's text is."""
    return f"{exam:%B} {_ordinal(exam.day)}, {exam.year} 08:00 AM MST"


@dataclass
class CheckCase:
    case_id: str
    reminder: str                      # the key of the reminder that is due today, such as DAY_5
    exam: object                       # a date
    provider: str = ""                 # as OA types it; the cell can be left blank
    provider_email: str = field(default="", repr=False)
    psychometrist: str = ""
    psychometrist_email: str = field(default="", repr=False)

    def row(self):
        values = {
            CASE_ID: self.case_id,
            "Patient Name": NOT_A_PATIENT,
            DATE_TIME: exam_text(self.exam),
            PROVIDER: self.provider,
            PROVIDER_EMAIL: self.provider_email,
            "Psychometrist": self.psychometrist,
            "Psychometrist Email": self.psychometrist_email,
        }
        return [values[header] for header in ONGOING_HEADERS]


def exam_for(reminder_key, today):
    """
    An exam date that makes `reminder_key` due `today`. The natural one is its day number
    back from today, which lands on today itself whatever day of the week it is. It can
    fail on a Friday: Day 8 for that exam would meet Day 10 (which falls on the Sunday and
    moves back to Friday), and the later reminder wins. So the nearest other exam date that
    works is used, one that reaches today through a weekend shift. Raises ValueError if no
    exam date makes it due, as with Day 3, which isn't sent since nothing tracks materials.
    """
    wanted = BY_KEY[reminder_key]
    natural = today - timedelta(days=wanted.day)
    for back in sorted(range(1, 15), key=lambda n: (abs(n - wanted.day), n)):
        exam = today - timedelta(days=back)
        if decide_reminders(exam, today).send == wanted:
            return exam
    raise ValueError(f"{reminder_key} cannot be made due on {today} (the natural exam date is {natural})")


def make_case(case_id, reminder_key, today, person=None, **extra):
    """A case whose `reminder_key` is due today, for `person` as its provider (or none)."""
    provider = person.typed if person else ""
    email = person.email if person else ""
    return CheckCase(case_id, reminder_key, exam_for(reminder_key, today), provider, email, **extra)


def standard_roster(today, first, second):
    """
    Four cases, each with a different reminder due today, spread over the two providers.
    The Day 11 notice is the first provider's, so that its Director copy, which goes to
    the second, is to a different address.
    """
    return [
        make_case("LC-1", DAY_1, today, first),
        make_case("LC-2", DAY_5, today, second),
        make_case("LC-3", DAY_10, today, first),
        make_case("LC-4", DAY_11_OVERDUE, today, first),
    ]


# --- The scratch tabs ------------------------------------------------------


class ScratchSpreadsheet:
    """
    Stands in for the spreadsheet the command is given. Only the names it holds can be
    looked up, and each gives its scratch tab. Any other name raises WorksheetNotFound,
    even if a real tab has that name, and a method this class lacks fails instead of
    reaching the real sheet. A tab the command creates, such as Reminders Sent, is made
    with the prefix in its real name, is remembered for deletion, and is found under the
    name the command gave it.
    """

    def __init__(self, sheet, created):
        self._sheet = sheet
        self._created = created
        self._tabs = {}

    def adopt(self, name, worksheet):
        self._tabs[name] = worksheet

    def worksheet(self, title):
        if title not in self._tabs:
            raise gspread.WorksheetNotFound(title)
        return self._tabs[title]

    def add_worksheet(self, title, rows, cols):
        tab = self._sheet.add_worksheet(title=PREFIX + title, rows=rows, cols=cols)
        self._created.append(tab)
        self._tabs[title] = tab
        return tab


def is_refused(stand_in, name):
    try:
        stand_in.worksheet(name)
    except gspread.WorksheetNotFound:
        return True
    return False


def remove_leftovers(sheet):
    """Deletes scratch tabs left behind by an earlier run that failed."""
    for tab in sheet.worksheets():
        if tab.title.startswith(PREFIX):
            sheet.del_worksheet(tab)
            print(f"  (removed a leftover tab from an earlier run: {tab.title})")


def make_tab(sheet, created, title, headers, rows=30):
    """A new scratch tab with its header row. It is deleted at the end with the others."""
    tab = sheet.add_worksheet(title=PREFIX + title, rows=rows, cols=len(headers))
    created.append(tab)
    tab.update(range_name=f"A1:{rowcol_to_a1(1, len(headers))}", values=[headers])
    return tab


def write_table(tab, rows):
    end = rowcol_to_a1(len(rows), max(len(row) for row in rows))
    tab.update(range_name=f"A1:{end}", values=rows)


def build_scratch(sheet, created, cases):
    """The stand-in spreadsheet, with an Ongoing tab holding `cases` and an empty Finished tab."""
    stand_in = ScratchSpreadsheet(sheet, created)
    ongoing = make_tab(sheet, created, ONGOING_TAB, ONGOING_HEADERS, rows=max(30, len(cases) + 10))
    write_table(ongoing, [ONGOING_HEADERS] + [case.row() for case in cases])
    stand_in.adopt(ONGOING_TAB, ongoing)
    stand_in.adopt(FINISHED_TAB, make_tab(sheet, created, FINISHED_TAB, FINISHED_HEADERS))
    return stand_in


# --- Running the command ----------------------------------------------------


@dataclass
class Outcome:
    output: str
    exit_code: int
    error: object = None   # an exception the command raised, if it did


def reset_logging():
    """The command's log handlers outlive a run. Close them, so each run logs into its own folder."""
    logger = logging.getLogger("trinity_scheduler")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def run_reminders(stand_in, argv, env, post, work, now=None):
    """
    Runs the reminders' real command line on the stand-in spreadsheet, from `work`, where
    its logs and its lock file go. Returns what it printed, the exit code it ended with,
    and any exception it raised.
    """
    reset_logging()
    config = {"health_check": {"log_folder": work, "email_notifications_enabled": False}}
    out, code, error = io.StringIO(), 0, None
    here = os.getcwd()
    os.chdir(work)
    try:
        with contextlib.redirect_stdout(out):
            try:
                reminders_main(argv, spreadsheet=stand_in, config=config, now=now, pause=False, environ=env, post=post)
            except SystemExit as stop:
                code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
            except Exception as failure:
                code, error = 1, failure
    finally:
        os.chdir(here)
        reset_logging()
    return Outcome(out.getvalue(), code, error)


def show(text):
    print("  --- the report, for reference ---")
    for line in text.splitlines():
        print(f"  | {line}")
    print("  --- end of report ---")


# --- What a group is given ---------------------------------------------------


@dataclass
class Context:
    sheet: object
    created: list
    work: str
    env: dict
    post: object
    outbox: Outbox
    today: object
    people: list
    now: object = None

    @property
    def first(self):
        return self.people[0]

    @property
    def second(self):
        return self.people[1]


def check_plumbing(ctx):
    """
    The set-up works, end to end, and sends nothing: scratch tabs are built from the
    cases, the command previews them, and the stand-in refuses every real tab.
    """
    cases = standard_roster(ctx.today, ctx.first, ctx.second)
    stand_in = build_scratch(ctx.sheet, ctx.created, cases)

    check(
        "the stand-in refuses the real tabs",
        all(is_refused(stand_in, name) for name in ("Contacts", "Services", "Settings", SENT_TAB)),
    )

    outcome = run_reminders(stand_in, [], ctx.env, ctx.post, ctx.work, ctx.now)
    show(outcome.output)
    report = outcome.output

    check("the preview ran and ended normally", outcome.exit_code == 0 and outcome.error is None, f"{outcome.exit_code} {outcome.error}")
    check("it is headed as a preview", "PREVIEW: nothing was sent" in report)
    check("all four cases would be sent", f"Would send ({len(cases)}):" in report, report[:300])
    for case in cases:
        label = BY_KEY[case.reminder].label
        line = f"Case {case.case_id}: {label} to {case.provider}"
        check(f"{case.case_id}: {label} to {case.provider}", line in report, "that line is not in the report")
    check("only the Day 11 notice copies the Director", report.count("director copied") == 1)
    check("no address is in the report", not any(p.email in report for p in ctx.people))
    check("nothing was sent and nothing was signed in to", ctx.outbox.sent == [] and ctx.outbox.tokens == 0)
    check("a preview creates no Reminders Sent tab", not any(t.title == PREFIX + SENT_TAB for t in ctx.sheet.worksheets()))
    check("the lock was never taken", not os.path.exists(os.path.join(ctx.work, "reminders.lock")))


# name, the scenarios in it (numbers from the checklist), whether it sends real email, its function
GROUPS = [
    ("plumbing", (), False, check_plumbing),
]


def choose_groups(only):
    """
    The groups to run, in their usual order. `only` is a comma-separated list of scenario
    numbers and group names, or None for all of them. Raises ValueError for something that
    is neither.
    """
    if not only:
        return list(GROUPS)
    wanted = set()
    for token in (t.strip() for t in only.split(",") if t.strip()):
        matches = [g for g in GROUPS if token == g[0] or (token.isdigit() and int(token) in g[1])]
        if not matches:
            known = ", ".join(str(n) for n in sorted(n for g in GROUPS for n in g[1])) or "none yet"
            raise ValueError(
                f"'{token}' is not a scenario number or a group name. "
                f"Scenarios: {known}. Groups: {', '.join(g[0] for g in GROUPS)}."
            )
        wanted.update(g[0] for g in matches)
    return [g for g in GROUPS if g[0] in wanted]


def describe_groups():
    lines = ["The scenarios run in groups:"]
    for name, scenarios, sends, _ in GROUPS:
        numbers = f"scenarios {', '.join(str(n) for n in scenarios)}" if scenarios else "set-up only"
        lines.append(f"  {name:12} {numbers}{'  (sends email)' if sends else ''}")
    lines.append("Use --only with scenario numbers or group names, for example: --only plumbing")
    return "\n".join(lines)


# --- Starting up -----------------------------------------------------------


def connect():
    """The sandbox sheet, with every request paced to Google's limit."""
    from app.sheet_client import get_client
    from scripts.check_sweep_live import Pacer, install_pacing

    sheet = get_client()
    client = getattr(sheet, "client", None)
    if client is None or not hasattr(client, "request"):
        print("  (could not find the Google client to pace, so a long run may hit the request limit)")
    else:
        install_pacing(client, Pacer())
    return sheet


def plan_text(sheet_title, today, people, env, chosen, roster):
    lines = [
        f"Sandbox spreadsheet: {sheet_title}",
        f"Today (Arizona): {today:%A, %B} {today.day}, {today.year}",
        "The two test providers, from the Contacts tab:",
    ]
    for person in people:
        lines.append(f"  {person.listed}  (a comment would name them '{person.typed}')  {person.email}")
    lines += [
        "Settings for this run only (your .env is not changed):",
        f"  test emails go to          {env['REMINDERS_TEST_RECIPIENT']}",
        f"  the Director copy goes to  {env['STAFF_CC_EMAILS']}",
        "Cases it would put on scratch tabs, each due today:",
    ]
    for case in roster:
        who = case.provider or "(no provider)"
        lines.append(f"  {case.case_id}  {BY_KEY[case.reminder].label:34} {who:10} exam {case.exam:%a %b} {case.exam.day}")
    lines += [
        f"Scratch tabs are named '{PREFIX.strip()} ...' and deleted at the end. No real tab is read, except Contacts.",
        "Groups: " + "; ".join(f"{name}{' (sends email)' if sends else ''}" for name, _, sends, _ in chosen),
    ]
    return "\n".join(lines)


def main(argv=None, sheet=None, ask=input, post=None, now=None, environ=None):
    parser = argparse.ArgumentParser(description="Live check of the reminders, on scratch tabs in the sandbox sheet.")
    parser.add_argument("--only", help="run only these scenarios or groups, comma-separated (see --list)")
    parser.add_argument("--list", action="store_true", help="show the groups and exit, without touching Google")
    parser.add_argument("--plan", action="store_true", help="show what it would do, and touch nothing")
    parser.add_argument(
        "--providers",
        default=", ".join(DEFAULT_PROVIDERS),
        help="the two test providers, as the Contacts tab lists them, comma-separated",
    )
    args = parser.parse_args([] if argv is None else argv)
    results.clear()

    if args.list:
        print(describe_groups())
        return 0
    try:
        chosen = choose_groups(args.only)
    except ValueError as error:
        print(error)
        return 2

    if sheet is None:
        sheet = connect()
    today = arizona_today(now)
    names = [name.strip() for name in args.providers.split(",") if name.strip()]
    try:
        check_sandbox(sheet.title)
        check_weekday(today)
        people = resolve_providers(sheet, names)
        env = build_env(environment(environ), people)
    except Refusal as refusal:
        print("Nothing was done.\n" + "\n".join(f"  - {problem}" for problem in refusal.problems))
        return 2

    print(plan_text(sheet.title, today, people, env, chosen, standard_roster(today, *people)))
    if args.plan:
        print("\nThis was a plan: nothing was written and nothing was sent.")
        return 0

    if any(sends for _, _, sends, _ in chosen):
        print("\nThe groups that send email send REAL email, to the two addresses above and to no one else.")
        if ask("Type yes to continue: ").strip().casefold() != "yes":
            print("Nothing was done.")
            return 2
    print()

    outbox = Outbox()
    guarded = guarded_post(allowed_addresses(people), outbox, post)
    remove_leftovers(sheet)
    created = []
    try:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            ctx = Context(sheet, created, work, env, guarded, outbox, today, people, now)
            for name, scenarios, _, function in chosen:
                label = f"scenarios {', '.join(str(n) for n in scenarios)}" if scenarios else "set-up"
                print(f"=== {name}: {label} ===")
                function(ctx)
                print()
    finally:
        for tab in created:
            sheet.del_worksheet(tab)
        print("Scratch tabs deleted.")

    left = [t.title for t in sheet.worksheets() if t.title.startswith(PREFIX)]
    check("no scratch tab is left behind", left == [], f"left {left}")
    print(f"\n{sum(results)} of {len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))