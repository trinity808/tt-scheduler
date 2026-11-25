"""
Tests for scripts/check_reminders_live.py, which need no Google and no Microsoft: the
guards, the two test providers, the settings the command is given, the guard on every
request to Microsoft, the cases and their dates, the stand-in spreadsheet, and the whole
script run against an in-memory sheet.

The live check itself needs the sandbox sheet and a Microsoft account, so it is run by
hand. A script that can send real email must not be trusted unchecked, so these tests
stop it from breaking quietly, and prove that nothing it does can reach an address that
isn't one of the two test addresses.

Run from the project root:
    python -m pytest tests/test_check_reminders_live.py -v
"""

import os
from datetime import date, datetime, timedelta, timezone

import gspread
import pytest

from app.exam_date import parse_exam_date
from app.reminder_rules import DAY_1, DAY_3, DAY_5, DAY_8, DAY_10, DAY_11_OVERDUE, decide_reminders
from tests.fakes import FakeWorksheet

import scripts.check_reminders_live as live


# Note: the script's functions are used as live.<name>, never imported by name, because
# pytest would collect an imported function called test_lines as a test.


SMITH = "jane.smith.4410@example.test"
DOE = "john.doe.5521@example.test"
DR_REAL = "the.real.director.9001@example.test"
STRANGER = "someone.else.7733@example.test"

NOW = datetime(2026, 11, 18, 17, 0, tzinfo=timezone.utc)   # Wednesday, 10:00 in Arizona
TODAY = date(2026, 11, 18)

REAL_ENV = {
    "MICROSOFT_TENANT_ID": "tenant-0000",
    "MICROSOFT_CLIENT_ID": "client-0000",
    "MICROSOFT_CLIENT_SECRET": "secret-value-5521-do-not-print",
    "MICROSOFT_SENDER_EMAIL": "support.sender.3300@example.test",
    "STAFF_CC_EMAILS": DR_REAL,
    "REMINDERS_GO_LIVE": "false",
    "REMINDERS_TEST_RECIPIENT": STRANGER,
}


@pytest.fixture(autouse=True)
def start_from_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    live.results.clear()


@pytest.fixture(autouse=True)
def alerts_are_off(monkeypatch):
    """The real config/settings.json is not read: its alert setting is whatever the test says."""
    monkeypatch.setattr("app.config_loader.load_config", lambda: {"health_check": {"email_notifications_enabled": False}})


class Tab(FakeWorksheet):
    """A worksheet that knows its title, as a real one does."""

    def __init__(self, title, rows, row_count=1000):
        super().__init__(rows, row_count)
        self.title = title


class Sandbox:
    """The whole spreadsheet: tabs by title, and the calls the script makes to add and delete them."""

    def __init__(self, title="Autoscheduler Sandbox", tabs=None):
        self.title = title
        self.tabs = {name: Tab(name, rows) for name, rows in (tabs or {}).items()}
        self.added, self.deleted = [], []

    def worksheet(self, name):
        if name not in self.tabs:
            raise gspread.WorksheetNotFound(name)
        return self.tabs[name]

    def worksheets(self):
        return list(self.tabs.values())

    def add_worksheet(self, title, rows, cols):
        tab = Tab(title, [[""] * cols], row_count=rows)
        self.tabs[title] = tab
        self.added.append(title)
        return tab

    def del_worksheet(self, tab):
        del self.tabs[tab.title]
        self.deleted.append(tab.title)


CONTACTS = [
    ["Name", "Email", "Role"],
    ["Dr. Jane Smith", SMITH, "Provider"],
    ["Dr. John Doe", DOE, "Provider"],
]

REAL_ONGOING = [
    ["Case ID", "Date & Time", "Provider", "Provider Email"],
    ["15419900", "November 11th, 2026 08:00 AM MST", "Dr. Smith", SMITH],
]


def sandbox(contacts=CONTACTS, title="Autoscheduler Sandbox"):
    tabs = {"Ongoing": REAL_ONGOING}
    if contacts is not None:
        tabs["Contacts"] = contacts
    return Sandbox(title, tabs)


class Reply:
    def __init__(self, status_code, payload=None):
        self.status_code, self.payload = status_code, payload

    def json(self):
        if self.payload is None:
            raise ValueError("not json")
        return self.payload


class Web:
    """
    Stands in for requests.post, as Microsoft answers it: a sign-in with the right secret gives
    a token and one with any other is refused, and an email is accepted only from the real
    sender, since a mailbox that doesn't exist is refused.
    """

    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if "oauth2" in url:
            if kwargs.get("data", {}).get("client_secret") != REAL_ENV["MICROSOFT_CLIENT_SECRET"]:
                return Reply(401, {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."})
            return Reply(200, {"access_token": "token-value-9917"})
        if "support.sender.3300%40example.test" not in url:
            return Reply(404, {"error": {"code": "ErrorInvalidUser"}})
        return Reply(202)


class AcceptsAnything(Web):
    """A Microsoft that accepts any secret and any sender, which the real one does not."""

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Reply(200, {"access_token": "token-value-9917"}) if "oauth2" in url else Reply(202)


def people():
    return [
        live.Person("Dr. Jane Smith", "Dr. Smith", SMITH),
        live.Person("Dr. John Doe", "Dr. Doe", DOE),
    ]


def go(argv=None, book=None, web=None, answers=(), now=NOW, environ=None):
    """Runs the script's main. Returns (exit code, the book, the web stand-in, the questions asked)."""
    book = book or sandbox()
    web = web or Web()
    questions, replies = [], list(answers)

    def ask(question):
        questions.append(question)
        return replies.pop(0)

    code = live.main(argv or [], sheet=book, ask=ask, post=web, now=now, environ=REAL_ENV if environ is None else environ)
    return code, book, web, questions


# --- The two test providers -----------------------------------------------------------


@pytest.mark.parametrize(
    "listed, typed",
    [("Dr. Jane Smith", "Dr. Smith"), ("Dr. John Doe", "Dr. Doe"), ("Jane Smith", "Dr. Smith")],
)
def test_a_comment_names_a_provider_by_last_name(listed, typed):
    assert live.typed_name(listed) == typed


def test_the_providers_are_found_the_way_the_sweep_finds_them():
    found = live.resolve_providers(sandbox(), ["Dr. Jane Smith", "Dr. John Doe"])
    assert [(p.listed, p.typed, p.email) for p in found] == [
        ("Dr. Jane Smith", "Dr. Smith", SMITH),
        ("Dr. John Doe", "Dr. Doe", DOE),
    ]


def refusal_for(contacts, names=("Dr. Jane Smith", "Dr. John Doe")):
    with pytest.raises(live.Refusal) as caught:
        live.resolve_providers(sandbox(contacts), list(names))
    return " | ".join(caught.value.problems)


def test_a_provider_missing_from_contacts_is_named_and_refused():
    text = refusal_for(CONTACTS[:2])
    assert "Dr. John Doe" in text and "not in the Contacts tab" in text


def test_a_provider_with_no_address_is_refused():
    text = refusal_for([CONTACTS[0], CONTACTS[1], ["Dr. John Doe", "", "Provider"]])
    assert "Dr. John Doe" in text


def test_someone_listed_only_as_a_psychometrist_is_not_a_provider():
    text = refusal_for([CONTACTS[0], CONTACTS[1], ["Dr. John Doe", DOE, "Psychometrist"]])
    assert "Dr. John Doe" in text


def test_a_last_name_that_fits_two_providers_is_refused_because_the_sweep_would_not_guess():
    text = refusal_for(CONTACTS + [["Dr. Mary Smith", "mary.smith.2@example.test", "Provider"]])
    assert "Dr. Smith" in text and "ambiguous" in text


def test_the_same_address_twice_is_refused():
    text = refusal_for([CONTACTS[0], CONTACTS[1], ["Dr. John Doe", SMITH.upper(), "Provider"]])
    assert "same address" in text


def test_a_missing_contacts_tab_is_refused_with_what_the_loader_says():
    text = refusal_for(None)
    assert "Contacts" in text


def test_two_names_are_required():
    with pytest.raises(live.Refusal, match="Two provider names"):
        live.resolve_providers(sandbox(), ["Dr. Jane Smith"])


def test_no_refusal_ever_contains_an_address():
    for contacts in (CONTACTS[:2], [CONTACTS[0], CONTACTS[1], ["Dr. John Doe", SMITH, "Provider"]], None):
        text = refusal_for(contacts)
        assert SMITH not in text and DOE not in text


# --- The guards ------------------------------------------------------------------------


@pytest.mark.parametrize("title", ["Autoscheduler Sandbox", "my SANDBOX copy", "sandbox"])
def test_a_title_with_sandbox_in_it_is_allowed(title):
    live.check_sandbox(title)


@pytest.mark.parametrize("title", ["Trinity Tree Tracking", "", "Autoscheduler"])
def test_any_other_title_is_refused(title):
    with pytest.raises(live.Refusal, match="sandbox"):
        live.check_sandbox(title)


def test_a_weekend_is_refused_and_a_weekday_is_not():
    for offset in range(7):
        day = date(2026, 11, 16) + timedelta(days=offset)   # Monday to Sunday
        if day.weekday() < 5:
            live.check_weekday(day)
        else:
            with pytest.raises(live.Refusal, match="weekend"):
                live.check_weekday(day)


# --- The settings the command is given --------------------------------------------------


def test_the_settings_replace_the_director_the_test_address_and_go_live_whatever_env_says():
    env = live.build_env(REAL_ENV, people())
    assert env["STAFF_CC_EMAILS"] == DOE
    assert env["REMINDERS_TEST_RECIPIENT"] == SMITH
    assert env["REMINDERS_GO_LIVE"] == "true"
    assert DR_REAL not in env.values() and STRANGER not in env.values()


def test_the_microsoft_settings_come_from_env_unchanged():
    env = live.build_env(REAL_ENV, people())
    for name in ("MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_SENDER_EMAIL"):
        assert env[name] == REAL_ENV[name]


def test_the_real_environment_is_not_changed():
    before = dict(REAL_ENV)
    live.build_env(REAL_ENV, people())
    assert REAL_ENV == before


@pytest.mark.parametrize("name", ["MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_SENDER_EMAIL"])
def test_a_missing_microsoft_setting_is_named_and_refused(name):
    with pytest.raises(live.Refusal) as caught:
        live.build_env({k: v for k, v in REAL_ENV.items() if k != name}, people())
    assert name in str(caught.value)


def test_the_allowed_addresses_are_the_two_providers_in_lower_case():
    assert live.allowed_addresses(people()) == {SMITH, DOE}
    assert live.allowed_addresses([live.Person("A", "Dr. A", "Mixed.Case@Example.Test")]) == {"mixed.case@example.test"}


# --- The guard on every request to Microsoft -----------------------------------------


SEND_URL = "https://graph.microsoft.com/v1.0/users/support.sender.3300%40example.test/sendMail"
TOKEN_URL = "https://login.microsoftonline.com/tenant-0000/oauth2/v2.0/token"


def message(to, cc=(), subject="A subject"):
    return {
        "message": {
            "subject": subject,
            "toRecipients": [{"emailAddress": {"address": a}} for a in to],
            "ccRecipients": [{"emailAddress": {"address": a}} for a in cc],
        }
    }


def guarded():
    web, outbox = Web(), live.Outbox()
    return live.guarded_post({SMITH, DOE}, outbox, web), web, outbox


def test_a_sign_in_passes_and_is_counted():
    post, web, outbox = guarded()
    post(TOKEN_URL, data={})
    assert outbox.tokens == 1 and len(web.calls) == 1


def test_an_email_to_a_test_address_passes_and_is_recorded_after_it_is_accepted():
    post, web, outbox = guarded()
    reply = post(SEND_URL, json=message([SMITH], [DOE], "Hello"))
    assert reply.status_code == 202
    ((sent,),) = [outbox.sent]
    assert (sent.to, sent.cc, sent.subject, sent.status) == ([SMITH], [DOE], "Hello", 202)


def test_the_addresses_are_compared_without_regard_to_capitals():
    post, web, outbox = guarded()
    post(SEND_URL, json=message([SMITH.upper()]))
    assert len(web.calls) == 1


@pytest.mark.parametrize(
    "to, cc",
    [([STRANGER], []), ([SMITH], [STRANGER]), ([SMITH, STRANGER], []), ([DR_REAL], [DOE]), ([], []), ([], [SMITH])],
)
def test_an_email_with_any_other_recipient_is_stopped_before_it_leaves(to, cc):
    post, web, outbox = guarded()
    with pytest.raises(live.Blocked) as caught:
        post(SEND_URL, json=message(to, cc))
    assert web.calls == [] and outbox.sent == []
    for address in (SMITH, DOE, STRANGER, DR_REAL):
        assert address not in str(caught.value)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example.test/sendMail",
        "https://graph.microsoft.com/v1.0/me/messages",
        "http://login.microsoftonline.com/x/token",
        "https://graph.microsoft.com/v1.0/users/x/sendMail/extra",
    ],
)
def test_a_request_to_anywhere_else_is_stopped(url):
    post, web, _ = guarded()
    with pytest.raises(live.Blocked):
        post(url, json=message([SMITH]))
    assert web.calls == []


def test_a_blocked_email_is_not_recorded_and_a_later_allowed_one_still_goes():
    post, web, outbox = guarded()
    with pytest.raises(live.Blocked):
        post(SEND_URL, json=message([STRANGER]))
    post(SEND_URL, json=message([DOE]))
    assert [s.to for s in outbox.sent] == [[DOE]]


# --- The cases ---------------------------------------------------------------------------


@pytest.mark.parametrize("day", range(1, 32))
def test_the_exam_text_is_read_back_as_the_same_date(day):
    exam = date(2026, 3, day)
    assert parse_exam_date(live.exam_text(exam)) == exam


@pytest.mark.parametrize("day, ending", [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"), (23, "23rd"), (30, "30th"), (31, "31st")])
def test_ordinals(day, ending):
    assert live._ordinal(day) == ending


WEEKDAYS = [date(2026, 11, 2) + timedelta(days=n) for n in range(21) if (date(2026, 11, 2) + timedelta(days=n)).weekday() < 5]


@pytest.mark.parametrize("today", WEEKDAYS)
@pytest.mark.parametrize("key", [DAY_1, DAY_5, DAY_8, DAY_10, DAY_11_OVERDUE])
def test_each_reminder_can_be_made_due_on_any_weekday(today, key):
    exam = live.exam_for(key, today)
    decision = decide_reminders(exam, today)
    assert decision.send is not None and decision.send.key == key


def test_day_8_on_a_friday_uses_a_different_exam_date_because_day_10_would_win_otherwise():
    friday = date(2026, 11, 6)
    assert live.exam_for(DAY_8, friday) != friday - timedelta(days=8)
    assert live.exam_for(DAY_8, friday) == friday - timedelta(days=7)


def test_day_3_cannot_be_made_due_because_it_is_never_sent():
    with pytest.raises(ValueError):
        live.exam_for(DAY_3, TODAY)


@pytest.mark.parametrize("today", WEEKDAYS)
def test_the_standard_roster_has_one_due_reminder_per_case(today):
    first, second = people()
    roster = live.standard_roster(today, first, second)
    assert [c.reminder for c in roster] == [DAY_1, DAY_5, DAY_10, DAY_11_OVERDUE]
    for case in roster:
        assert decide_reminders(case.exam, today).send.key == case.reminder


def test_the_day_11_notice_belongs_to_the_first_provider_so_its_copy_goes_to_a_different_address():
    roster = live.standard_roster(TODAY, *people())
    day_11 = [c for c in roster if c.reminder == DAY_11_OVERDUE][0]
    assert day_11.provider_email == SMITH and live.build_env(REAL_ENV, people())["STAFF_CC_EMAILS"] == DOE


def test_a_case_row_follows_the_headers_and_is_never_a_patient():
    case = live.make_case("LC-9", DAY_5, TODAY, people()[1], psychometrist="Dr. Doe", psychometrist_email=SMITH)
    row = dict(zip(live.ONGOING_HEADERS, case.row()))
    assert row["Case ID"] == "LC-9" and row["Provider"] == "Dr. Doe" and row["Provider Email"] == DOE
    assert row["Psychometrist Email"] == SMITH and row["Patient Name"] == live.NOT_A_PATIENT
    assert parse_exam_date(row["Date & Time"]) == TODAY - timedelta(days=5)


def test_a_case_with_no_provider_has_blank_provider_cells():
    row = dict(zip(live.ONGOING_HEADERS, live.make_case("LC-9", DAY_5, TODAY).row()))
    assert row["Provider"] == "" and row["Provider Email"] == ""


# --- The scratch tabs --------------------------------------------------------------------


def test_the_stand_in_gives_only_the_tabs_it_holds_and_refuses_real_ones():
    book, created = sandbox(), []
    stand_in = live.build_scratch(book, created, [])
    assert stand_in.worksheet("Ongoing").title == live.PREFIX + "Ongoing"
    assert stand_in.worksheet("Finished").title == live.PREFIX + "Finished"
    for name in ("Contacts", "Services", "Reminders Sent", "Ongoing "):
        assert live.is_refused(stand_in, name)


def test_the_real_ongoing_tab_is_never_given_even_though_the_name_matches():
    book, created = sandbox(), []
    stand_in = live.build_scratch(book, created, [])
    assert stand_in.worksheet("Ongoing") is not book.tabs["Ongoing"]


def test_the_scratch_ongoing_tab_holds_the_cases_under_the_headers():
    book, created = sandbox(), []
    cases = live.standard_roster(TODAY, *people())
    stand_in = live.build_scratch(book, created, cases)
    table = stand_in.worksheet("Ongoing").get_all_values()
    assert table[0] == live.ONGOING_HEADERS and [r[0] for r in table[1:]] == ["LC-1", "LC-2", "LC-3", "LC-4"]
    assert stand_in.worksheet("Finished").get_all_values() == [live.FINISHED_HEADERS]


def test_a_tab_the_command_creates_gets_the_prefix_in_its_real_name_and_is_found_under_its_own():
    book, created = sandbox(), []
    stand_in = live.build_scratch(book, created, [])
    tab = stand_in.add_worksheet(title="Reminders Sent", rows=1000, cols=5)
    assert tab.title == live.PREFIX + "Reminders Sent" and (live.PREFIX + "Reminders Sent") in book.tabs
    assert stand_in.worksheet("Reminders Sent") is tab and tab in created and "Reminders Sent" not in book.tabs


def test_leftover_scratch_tabs_are_removed_and_real_ones_are_not(capsys):
    book = sandbox()
    book.add_worksheet(live.PREFIX + "Ongoing", 10, 3)
    book.add_worksheet(live.PREFIX + "Reminders Sent", 10, 3)
    live.remove_leftovers(book)
    assert sorted(book.tabs) == ["Contacts", "Ongoing"]
    assert capsys.readouterr().out.count("leftover") == 2


# --- Choosing groups ---------------------------------------------------------------------


def test_with_no_choice_every_group_runs():
    assert [g[0] for g in live.choose_groups(None)] == [g[0] for g in live.GROUPS]
    assert [g[0] for g in live.choose_groups("")] == [g[0] for g in live.GROUPS]


def test_a_group_can_be_chosen_by_name():
    assert [g[0] for g in live.choose_groups("plumbing")] == ["plumbing"]


def test_something_that_is_neither_a_number_nor_a_group_is_refused_with_the_choices():
    with pytest.raises(ValueError, match="plumbing"):
        live.choose_groups("nonsense")
    with pytest.raises(ValueError, match="not a scenario number"):
        live.choose_groups("99")


def test_the_list_names_every_group():
    text = live.describe_groups()
    for name, *_ in live.GROUPS:
        assert name in text


def test_the_groups_have_a_name_a_scenario_list_a_sends_flag_and_a_function():
    for name, scenarios, sends, function in live.GROUPS:
        assert isinstance(name, str) and isinstance(scenarios, tuple) and isinstance(sends, bool) and callable(function)
    assert len({g[0] for g in live.GROUPS}) == len(live.GROUPS)


# --- The whole script, against the in-memory sheet ---------------------------------------


def test_the_list_touches_nothing(capsys):
    code = live.main(["--list"], sheet=None)
    assert code == 0 and "plumbing" in capsys.readouterr().out


def test_a_bad_choice_is_refused_before_anything_is_read(capsys):
    code = live.main(["--only", "nonsense"], sheet=None)
    assert code == 2 and "not a scenario number" in capsys.readouterr().out


def test_the_plan_shows_what_it_would_do_and_changes_nothing(capsys):
    code, book, web, questions = go(["--plan"])
    out = capsys.readouterr().out
    assert code == 0 and book.added == [] and book.deleted == [] and web.calls == [] and questions == []
    assert "Autoscheduler Sandbox" in out and SMITH in out and DOE in out
    for case_id in ("LC-1", "LC-2", "LC-3", "LC-4"):
        assert case_id in out
    assert "nothing was written and nothing was sent" in out
    assert all(tab.writes == [] for tab in book.tabs.values())


def test_the_plan_says_the_director_copy_goes_to_the_second_provider_not_env(capsys):
    go(["--plan"])
    out = capsys.readouterr().out
    assert f"the Director copy goes to  {DOE}" in out and DR_REAL not in out and STRANGER not in out


@pytest.mark.parametrize("title", ["Trinity Tree Tracking", ""])
def test_a_sheet_that_is_not_the_sandbox_is_refused_and_nothing_is_done(capsys, title):
    code, book, web, _ = go(book=sandbox(title=title))
    assert code == 2 and "Nothing was done." in capsys.readouterr().out
    assert book.added == [] and web.calls == []


def test_a_weekend_is_refused_and_nothing_is_done(capsys):
    saturday = datetime(2026, 11, 21, 17, 0, tzinfo=timezone.utc)
    code, book, web, _ = go(now=saturday)
    assert code == 2 and "weekend" in capsys.readouterr().out and book.added == []


def test_a_provider_missing_from_contacts_is_refused_and_nothing_is_done(capsys):
    code, book, web, _ = go(book=sandbox(CONTACTS[:2]))
    out = capsys.readouterr().out
    assert code == 2 and "Dr. John Doe" in out and book.added == []
    assert SMITH not in out


def test_a_missing_microsoft_setting_is_refused_and_nothing_is_done(capsys):
    env = {k: v for k, v in REAL_ENV.items() if k != "MICROSOFT_CLIENT_SECRET"}
    code, book, _, _ = go(environ=env)
    assert code == 2 and "MICROSOFT_CLIENT_SECRET" in capsys.readouterr().out and book.added == []


def test_other_providers_can_be_chosen(capsys):
    contacts = CONTACTS + [["Dr. Ann Lee", "ann.lee.6@example.test", "Provider"]]
    code, book, _, _ = go(["--providers", "Dr. Jane Smith, Dr. Ann Lee", "--plan"], book=sandbox(contacts))
    out = capsys.readouterr().out
    assert code == 0 and "Dr. Lee" in out and "ann.lee.6@example.test" in out and DOE not in out


def test_the_plumbing_group_runs_a_preview_on_scratch_tabs_and_cleans_up_after_itself(capsys):
    code, book, web, questions = go(["--only", "plumbing"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "checks passed" in out and "FAILED" not in out
    assert sorted(book.tabs) == ["Contacts", "Ongoing"]            # only the real tabs are left
    assert sorted(book.deleted) == [live.PREFIX + "Finished", live.PREFIX + "Ongoing"]
    assert web.calls == [] and questions == []                      # a preview signs in to nothing and asks nothing


def test_the_real_tabs_are_never_written_to(capsys):
    code, book, _, _ = go(["--only", "plumbing"])
    assert all(tab.writes == [] for tab in book.tabs.values())


def test_the_report_in_the_output_has_no_address_in_it(capsys):
    go(["--only", "plumbing"])
    out = capsys.readouterr().out
    report = out.split("--- the report, for reference ---")[1].split("--- end of report ---")[0]
    assert SMITH not in report and DOE not in report and "Case LC-4" in report


def test_the_command_runs_in_a_temporary_folder_so_the_project_folder_gets_no_lock_or_log(tmp_path, capsys):
    before = set(os.listdir(tmp_path))
    go(["--only", "plumbing"])
    assert os.getcwd() == str(tmp_path) and set(os.listdir(tmp_path)) == before


def test_a_failed_check_makes_the_exit_code_1(capsys, monkeypatch):
    def failing(ctx):
        live.check("this one fails", False, "on purpose")

    monkeypatch.setattr(live, "GROUPS", [("failing", (), False, failing)])
    code, book, _, _ = go()
    assert code == 1 and "[FAILED] this one fails  ->  on purpose" in capsys.readouterr().out
    assert book.tabs.keys() == {"Contacts", "Ongoing"}


def test_results_do_not_carry_over_from_one_run_to_the_next(capsys, monkeypatch):
    monkeypatch.setattr(live, "GROUPS", [("failing", (), False, lambda ctx: live.check("fails", False))])
    assert go()[0] == 1
    monkeypatch.setattr(live, "GROUPS", [("fine", (), False, lambda ctx: live.check("fine", True))])
    assert go()[0] == 0


def test_the_scratch_tabs_are_deleted_even_when_a_group_crashes():
    def crashing(ctx):
        live.build_scratch(ctx.sheet, ctx.created, [])
        raise RuntimeError("a bug in a group")

    book = sandbox()
    original = live.GROUPS
    live.GROUPS = [("crashing", (), False, crashing)]
    try:
        with pytest.raises(RuntimeError, match="a bug in a group"):
            go(book=book)
    finally:
        live.GROUPS = original
    assert sorted(book.tabs) == ["Contacts", "Ongoing"]


# A group that sends asks first.


def sending_group(seen):
    def function(ctx):
        seen.append(ctx)
        live.check("it ran", True)

    return ("sending", (), True, function)


def test_a_group_that_sends_waits_for_yes_after_showing_the_sheet_and_the_addresses(capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(live, "GROUPS", [sending_group(seen)])
    code, book, _, questions = go(answers=["yes"])
    out = capsys.readouterr().out
    assert code == 0 and len(questions) == 1 and len(seen) == 1
    assert out.index("Autoscheduler Sandbox") < out.index("REAL email") and SMITH in out and DOE in out


@pytest.mark.parametrize("answer", ["no", "", "y", "yes please", "maybe"])
def test_anything_but_yes_stops_before_anything_is_created(capsys, monkeypatch, answer):
    seen = []
    monkeypatch.setattr(live, "GROUPS", [sending_group(seen)])
    code, book, web, _ = go(answers=[answer])
    assert code == 2 and seen == [] and book.added == [] and web.calls == []
    assert "Nothing was done." in capsys.readouterr().out


def test_yes_is_accepted_in_any_capitals_with_spaces(capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(live, "GROUPS", [sending_group(seen)])
    assert go(answers=["  YES "])[0] == 0 and len(seen) == 1


def test_a_group_that_sends_is_given_a_post_that_blocks_other_addresses(capsys, monkeypatch):
    blocked = []

    def function(ctx):
        try:
            ctx.post(SEND_URL, json=message([STRANGER]))
        except live.Blocked:
            blocked.append(True)
        ctx.post(SEND_URL, json=message([SMITH], [DOE]))
        live.check("one email went out", len(ctx.outbox.sent) == 1)

    monkeypatch.setattr(live, "GROUPS", [("sending", (), True, function)])
    code, _, web, _ = go(answers=["yes"])
    assert code == 0 and blocked == [True] and len(web.calls) == 1


def test_a_group_is_given_settings_that_never_include_a_stranger(capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(live, "GROUPS", [sending_group(seen)])
    go(answers=["yes"])
    env = seen[0].env
    assert env["STAFF_CC_EMAILS"] == DOE and env["REMINDERS_TEST_RECIPIENT"] == SMITH
    assert DR_REAL not in env.values() and STRANGER not in env.values()


def test_a_group_that_does_not_send_is_not_asked_about(capsys):
    _, _, _, questions = go(["--only", "plumbing"])
    assert questions == []


# --- Running the command ------------------------------------------------------------------


def test_run_reminders_reports_the_exit_code_the_command_ended_with(tmp_path):
    book, created = sandbox(), []
    stand_in = live.build_scratch(book, created, live.standard_roster(TODAY, *people()))
    env = live.build_env(REAL_ENV, people())
    outcome = live.run_reminders(stand_in, ["--today", "2026-11-18", "--live"], env, Web(), str(tmp_path), NOW)
    assert outcome.exit_code == 2 and "Nothing was done." in outcome.output   # a real send can't use --today


def test_run_reminders_hands_back_an_exception_the_command_raises(tmp_path):
    book = sandbox()
    stand_in = live.ScratchSpreadsheet(book, [])           # no Ongoing tab
    env = live.build_env(REAL_ENV, people())
    outcome = live.run_reminders(stand_in, [], env, Web(), str(tmp_path), NOW)
    assert outcome.exit_code == 1 and isinstance(outcome.error, gspread.WorksheetNotFound)


# --- Chunk B: the groups "quiet" and "test-mode" --------------------------------------------


MONDAY_TO_FRIDAY = [datetime(2026, 11, 16 + n, 17, 0, tzinfo=timezone.utc) for n in range(5)]   # 10:00 in Arizona


def sends_and_signins(web):
    emails = [kwargs["json"]["message"] for url, kwargs in web.calls if url.endswith("/sendMail")]
    return emails, [url for url, _ in web.calls if "oauth2" in url]


def addresses(message):
    return [r["emailAddress"]["address"] for key in ("toRecipients", "ccRecipients") for r in message.get(key, [])]


def test_the_guard_records_what_an_email_said():
    post, web, outbox = guarded()
    body = {"message": {**message([SMITH], subject="S")["message"], "body": {"contentType": "Text", "content": "Hello"}}}
    post(SEND_URL, json=body)
    assert outbox.sent[0].body == "Hello"
    assert "Hello" not in repr(outbox.sent[0])


def test_the_groups_are_named_and_numbered_as_the_checklist_has_them():
    groups = {name: (scenarios, sends) for name, scenarios, sends, _ in live.GROUPS}
    assert groups["plumbing"] == ((), False)
    assert groups["quiet"] == ((3, 9, 11), False)
    assert groups["test-mode"] == ((6, 10), True)
    assert groups["failures"] == ((7, 8), True)


@pytest.mark.parametrize(
    "only, expected",
    [("3", ["quiet"]), ("9", ["quiet"]), ("3,9", ["quiet"]), ("6", ["test-mode"]), ("10", ["test-mode"]),
     ("6,10", ["test-mode"]), ("3,6", ["quiet", "test-mode"]), ("test-mode,quiet", ["quiet", "test-mode"]),
     ("7", ["failures"]), ("8", ["failures"]), ("7,8", ["failures"]), ("11", ["quiet"]),
     ("failures,plumbing", ["plumbing", "failures"])],
)
def test_scenarios_and_groups_choose_the_groups_that_hold_them(only, expected):
    assert [g[0] for g in live.choose_groups(only)] == expected


# The cases for scenarios 6 and 10


@pytest.mark.parametrize("today", WEEKDAYS)
def test_the_test_mode_roster_has_five_due_cases_and_one_with_no_address(today):
    first, second = people()
    roster = live.roster_for_test_mode(today, first, second)
    assert [c.case_id for c in roster] == ["LC-61", "LC-62", "LC-63", "LC-64", "LC-65", "LC-67"]
    for case, key in zip(roster[:5], [DAY_1, DAY_5, DAY_8, DAY_10, DAY_11_OVERDUE]):
        assert decide_reminders(case.exam, today).send.key == key and case.provider_email
    assert roster[5].provider_email == "" and roster[5].provider == first.typed


def test_the_roster_alternates_providers_with_the_day_11_case_the_first_providers():
    roster = live.roster_for_test_mode(TODAY, *people())
    assert [c.provider_email for c in roster[:5]] == [SMITH, DOE, SMITH, DOE, SMITH]


def test_the_day_3_case_is_due_on_the_fixed_day_and_nothing_else_is():
    assert live.HELD_BACK_TODAY.weekday() == 1       # a Tuesday, so nothing shifts it
    decision = decide_reminders(live.HELD_BACK_EXAM, live.HELD_BACK_TODAY)
    assert decision.send is None and decision.already_sent is None
    assert [r.key for r, _ in decision.unsent] == [DAY_3]


# The scratch helpers


def test_discard_scratch_deletes_every_tab_and_empties_the_list():
    book, created = sandbox(), []
    live.make_tab(book, created, "Ongoing", live.ONGOING_HEADERS)
    live.make_tab(book, created, "Finished", live.FINISHED_HEADERS)
    live.discard_scratch(book, created)
    assert created == [] and sorted(book.tabs) == ["Contacts", "Ongoing"]


def test_a_tab_that_fails_to_delete_stays_in_the_list_so_it_is_not_forgotten():
    book, created = sandbox(), []
    tab = live.make_tab(book, created, "Ongoing", live.ONGOING_HEADERS)

    def refuse(_):
        raise RuntimeError("Google said no")

    book.del_worksheet = refuse
    with pytest.raises(RuntimeError):
        live.discard_scratch(book, created)
    assert created == [tab]


def context_for(book):
    created = []
    return live.Context(book, created, "unused", live.build_env(REAL_ENV, people()), Web(), live.Outbox(), TODAY, people(), NOW)


def test_scratch_replaces_the_tabs_of_the_scenario_before_it():
    book = sandbox()
    ctx = context_for(book)
    live.scratch(ctx, [])
    live.scratch(ctx, [])
    assert [t for t in book.tabs if t.startswith(live.PREFIX)] == [live.PREFIX + "Ongoing", live.PREFIX + "Finished"]
    assert len(ctx.created) == 2


def test_scratch_can_leave_a_reminders_sent_tab_as_somebody_left_it():
    book = sandbox()
    ctx = context_for(book)
    table = [["Case ID", "Reminder"], ["LC-1", "DAY_1"]]
    stand_in = live.scratch(ctx, [], table)
    tab = stand_in.worksheet("Reminders Sent")
    assert tab.title == live.PREFIX + "Reminders Sent" and tab.get_all_values() == table
    live.scratch(ctx, [])
    assert live.PREFIX + "Reminders Sent" not in book.tabs


# The groups, run end to end against the in-memory sheet


def test_the_quiet_group_sends_nothing_asks_nothing_and_cleans_up(capsys):
    code, book, web, questions = go(["--only", "quiet"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "FAILED" not in out and "checks passed" in out
    assert web.calls == [] and questions == []
    assert sorted(book.tabs) == ["Contacts", "Ongoing"] and all(t.writes == [] for t in book.tabs.values())


@pytest.mark.parametrize("now", MONDAY_TO_FRIDAY)
def test_the_quiet_group_passes_on_every_weekday(capsys, now):
    code, *_ = go(["--only", "quiet"], now=now)
    assert code == 0, capsys.readouterr().out


@pytest.mark.parametrize("now", MONDAY_TO_FRIDAY)
def test_the_test_mode_group_passes_on_every_weekday(capsys, now):
    code, *_ = go(["--only", "test-mode"], answers=["yes"], now=now)
    assert code == 0, capsys.readouterr().out


def test_the_test_mode_group_asks_first_and_does_nothing_on_no(capsys):
    code, book, web, questions = go(["--only", "6"], answers=["no"])
    assert code == 2 and len(questions) == 1 and web.calls == [] and book.added == []


def test_the_test_mode_group_sends_six_test_emails_to_the_first_provider_alone(capsys):
    code, book, web, _ = go(["--only", "test-mode"], answers=["yes"])
    emails, signins = sends_and_signins(web)
    assert code == 0, capsys.readouterr().out
    assert len(emails) == 6 and len(signins) == 2           # five from the test run, one after the lock is taken over
    for sent in emails:
        assert [a.casefold() for a in addresses(sent)] == [SMITH]
        assert sent["subject"].startswith("[TEST] ")
    assert sorted(book.tabs) == ["Contacts", "Ongoing"]


def test_the_test_mode_group_never_writes_a_reminders_sent_tab(capsys):
    _, book, _, _ = go(["--only", "test-mode"], answers=["yes"])
    assert not any("Reminders Sent" in title for title in book.added)


def test_a_single_scenario_runs_only_its_group(capsys):
    _, _, _, questions = go(["--only", "3"])
    out = capsys.readouterr().out
    assert "scenario 3" in out and "scenario 9" in out and "scenario 6" not in out and questions == []


def test_everything_together_passes_and_asks_once(capsys):
    code, book, web, questions = go([], answers=["yes"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert len(questions) == 1 and "FAILED" not in out
    assert sorted(book.tabs) == ["Contacts", "Ongoing"]


def test_the_group_fails_when_the_command_copies_someone_on_a_test_email(capsys, monkeypatch):
    """The live check must notice a broken test mode, not just run it."""
    import app.reminders_send as send_module

    real = send_module.deliver

    def copying(plan, send, **kwargs):
        def with_copy(to, subject, body, cc):
            send(to, subject, body, (DOE,))
        return real(plan, with_copy, **kwargs)

    monkeypatch.setattr("app.reminders.deliver", copying)
    code, *_ = go(["--only", "6"], answers=["yes"])
    out = capsys.readouterr().out
    assert code == 1 and "[FAILED] no one was copied" in out


def test_the_group_fails_when_the_command_ignores_a_held_lock(capsys, monkeypatch):
    import app.reminders as reminders

    monkeypatch.setattr(reminders, "_take_lock", lambda logger, config, mode: reminders.acquire("elsewhere.lock", mode, 900))
    code, *_ = go(["--only", "10"], answers=["yes"])
    out = capsys.readouterr().out
    assert code == 1 and "FAILED" in out


# --- Chunk C: scenarios 7, 8 and 11 -----------------------------------------------------------


def test_the_wrong_sender_is_on_the_real_senders_domain_and_is_never_the_same_twice():
    first, second = live.wrong_sender(REAL_ENV), live.wrong_sender(REAL_ENV)
    assert first.endswith("@example.test") and first != second
    assert first != REAL_ENV["MICROSOFT_SENDER_EMAIL"] and "support.sender" not in first


@pytest.mark.parametrize(
    "config, expected",
    [({"health_check": {"email_notifications_enabled": True}}, True),
     ({"health_check": {"email_notifications_enabled": False}}, False),
     ({"health_check": {}}, None), ({}, None), ({"health_check": None}, None)],
)
def test_the_alert_setting_is_read_from_the_settings_file(monkeypatch, config, expected):
    monkeypatch.setattr("app.config_loader.load_config", lambda: config)
    assert live.read_alert_setting() == (expected, "")


def test_a_settings_file_that_cannot_be_read_is_reported(monkeypatch):
    def missing():
        raise FileNotFoundError("config/settings.json")

    monkeypatch.setattr("app.config_loader.load_config", missing)
    setting, problem = live.read_alert_setting()
    assert setting is None and "FileNotFoundError" in problem


@pytest.mark.parametrize("enabled, wanted", [(True, "are ON"), (False, "are OFF"), (None, "are OFF")])
def test_the_alert_scenario_says_whether_alerts_are_on_and_is_not_a_pass_or_fail(capsys, monkeypatch, enabled, wanted):
    monkeypatch.setattr("app.config_loader.load_config", lambda: {"health_check": {"email_notifications_enabled": enabled}})
    live.check_alerts(context_for(sandbox()))
    out = capsys.readouterr().out
    assert "[NOTE]" in out and wanted in out
    assert live.results == [True]         # only "the file can be read" counts


def test_an_unreadable_settings_file_fails_the_check(capsys, monkeypatch):
    monkeypatch.setattr("app.config_loader.load_config", lambda: (_ for _ in ()).throw(OSError("no file")))
    live.check_alerts(context_for(sandbox()))
    assert live.results == [False] and "[FAILED] the settings file can be read" in capsys.readouterr().out


def test_the_quiet_group_reports_the_alert_setting(capsys):
    code, *_ = go(["--only", "11"])
    out = capsys.readouterr().out
    assert code == 0 and "scenario 11" in out and "Failure alerts by email are OFF" in out


def test_the_failures_group_asks_first_and_does_nothing_on_no(capsys):
    code, book, web, questions = go(["--only", "failures"], answers=["no"])
    assert code == 2 and len(questions) == 1 and web.calls == [] and book.added == []


def test_the_failures_group_passes_and_sends_three_real_emails_to_the_two_providers(capsys):
    code, book, web, _ = go(["--only", "failures"], answers=["yes"])
    out = capsys.readouterr().out
    assert code == 0, out
    emails, signins = sends_and_signins(web)
    assert len(signins) == 3                                      # the wrong sender, the retry, the wrong secret
    assert len(emails) == 6 and not any(m["subject"].startswith("[TEST]") for m in emails)   # three refused, three accepted
    for sent in emails:
        assert set(a.casefold() for a in addresses(sent)) <= {SMITH, DOE}
    assert sorted(book.tabs) == ["Contacts", "Ongoing"]


@pytest.mark.parametrize("now", MONDAY_TO_FRIDAY)
def test_the_failures_group_passes_on_every_weekday(capsys, now):
    code, *_ = go(["--only", "failures"], answers=["yes"], now=now)
    assert code == 0, capsys.readouterr().out


def test_no_secret_or_sender_appears_anywhere_in_the_output(capsys):
    go(["--only", "failures"], answers=["yes"])
    out = capsys.readouterr().out
    assert REAL_ENV["MICROSOFT_CLIENT_SECRET"] not in out
    assert "support.sender.3300" not in out and "reminder-check-no-such-mailbox" not in out
    assert "wrong-secret-" not in out.replace("neither the wrong secret", "")


def test_the_check_notices_when_an_error_carries_the_senders_address_in_its_url(capsys, monkeypatch):
    import app.mail_sender as mail

    real = mail.send_email

    def leaky(settings, token, to, subject, body, cc=(), post=None):
        try:
            real(settings, token, to, subject, body, cc, post)
        except mail.MailError as error:
            raise mail.MailError(f"{error} at {mail.SEND_URL.format(sender=mail.quote(settings.sender, safe=''))}") from None

    monkeypatch.setattr("app.reminders.send_email", leaky)
    code, *_ = go(["--only", "7"], answers=["yes"])
    assert code == 1 and "[FAILED] no sender address and no secret is in the report" in capsys.readouterr().out


def test_the_check_notices_when_microsoft_accepts_a_wrong_sender(capsys):
    code, *_ = go(["--only", "7"], answers=["yes"], web=AcceptsAnything())
    out = capsys.readouterr().out
    assert code == 1 and "[FAILED] every email was tried and every one failed" in out


def test_the_check_notices_when_microsoft_accepts_a_wrong_secret(capsys):
    code, *_ = go(["--only", "8"], answers=["yes"], web=AcceptsAnything())
    out = capsys.readouterr().out
    assert code == 1 and "[FAILED] the run stops with an error and exit code 1" in out


def test_the_check_notices_when_a_failed_email_is_logged_as_sent(capsys, monkeypatch):
    import app.reminders_send as send_module

    real = send_module.deliver

    def logging_failures(plan, send, log=None, stamp=None, **kwargs):
        def lenient(to, subject, body, cc):
            try:
                send(to, subject, body, cc)
            except send_module.MailError:
                pass          # pretends it went
        return real(plan, lenient, log=log, stamp=stamp, **kwargs)

    monkeypatch.setattr("app.reminders.deliver", logging_failures)
    code, *_ = go(["--only", "7"], answers=["yes"])
    out = capsys.readouterr().out
    assert code == 1 and "FAILED" in out


def test_the_check_notices_when_the_command_goes_on_after_a_failed_sign_in(capsys, monkeypatch):
    monkeypatch.setattr("app.reminders.get_access_token", lambda settings, post=None: "token-anyway")
    code, *_ = go(["--only", "8"], answers=["yes"])
    out = capsys.readouterr().out
    assert code == 1 and "FAILED" in out