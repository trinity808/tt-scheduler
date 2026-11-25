"""
Tests for the command line when it sends: python -m app.reminders --live, which emails
the providers, and --live --test, which sends every email to a test address instead.

The sheet is in memory and Microsoft is a stand-in function (see Web), so nothing real is
touched. The safeguards come first (what makes a run refuse to start), then the test run,
the real send and the Reminders Sent tab, a send that goes wrong, the lock, the report and
the log, and last that no address, secret or token reaches the screen, the logs or an alert.

The calendar is May 2026. Monday May 11 is the day: case 111 (exam May 4) is due its Day 5
check-in, and case 222 (exam April 30) its Day 11 overdue notice, which copies the Director.

Run from the project root:
    python -m pytest tests/test_reminders_live.py -v
"""

import json
import os
import time
from datetime import datetime, timezone

import pytest
from gspread.exceptions import WorksheetNotFound

from app.mail_sender import MailError
from app.reminders import REMINDERS_ERROR_LOG, REMINDERS_LOCK, REMINDERS_LOG, main
from app.reminders_sheet import SENT_HEADERS, SENT_TAB, read_reminder_data
from app.run_lock import acquire
from tests.fakes import FakeWorksheet
from tests.log_cleanup import clean_logger  # noqa: F401
from tests.test_mail_sender import DIRECTOR, SECRET, SENDER, TOKEN, Reply
from tests.test_reminders import DOE_ADDRESS, SMITH_ADDRESS, case, config_for, read, world


TESTER = "tester.inbox.8812@example.test"
NOW = datetime(2026, 5, 11, 15, 30, 5, tzinfo=timezone.utc)  # 8:30:05 on Monday May 11 in Arizona
EVERYTHING_PRIVATE = (DOE_ADDRESS, SMITH_ADDRESS, DIRECTOR, TESTER, SENDER, SECRET, TOKEN)

LIVE_ENV = {
    "MICROSOFT_TENANT_ID": "tenant-0000",
    "MICROSOFT_CLIENT_ID": "client-0000",
    "MICROSOFT_CLIENT_SECRET": SECRET,
    "MICROSOFT_SENDER_EMAIL": SENDER,
    "STAFF_CC_EMAILS": DIRECTOR,
    "REMINDERS_TEST_RECIPIENT": TESTER,
    "REMINDERS_GO_LIVE": "true",
}

TEST_RUN = ["--live", "--test"]


@pytest.fixture(autouse=True)
def scratch_folder(tmp_path, monkeypatch):
    """Run in an empty folder: the logs and reminders.lock land here, not in the project."""
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr("app.reminders.notify_error", lambda subject, message, config, exc_info: sent.append((subject, message)))
    return sent


@pytest.fixture
def typed(monkeypatch):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt))
    return prompts


class Web:
    """Stands in for requests.post: answers the token request and each send, and records them."""

    def __init__(self, token_status=200, refuse_cases=(), watch_lock=False):
        self.token_status = token_status
        self.refuse_cases = set(refuse_cases)  # case IDs whose email Microsoft refuses
        self.tokens = 0
        self.sends = []        # dicts: to, cc, subject, body
        self.lock_seen = []    # what reminders.lock held when each request came in
        self.watch_lock = watch_lock

    def __call__(self, url, **kwargs):
        if self.watch_lock and os.path.exists(REMINDERS_LOCK):
            with open(REMINDERS_LOCK, encoding="utf-8") as lock:
                self.lock_seen.append(json.load(lock)["mode"])
        if "oauth2/v2.0/token" in url:
            self.tokens += 1
            if self.token_status != 200:
                return Reply(self.token_status, {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."})
            return Reply(200, {"access_token": TOKEN})
        message = kwargs["json"]["message"]
        to = message["toRecipients"][0]["emailAddress"]["address"]
        if any(f"DDS case {case_id}" in message["subject"] for case_id in self.refuse_cases):
            return Reply(400, {"error": {"code": "ErrorInvalidRecipients", "message": f"{to} is not valid"}})
        self.sends.append(
            {
                "to": to,
                "cc": [c["emailAddress"]["address"] for c in message["ccRecipients"]],
                "subject": message["subject"],
                "body": message["body"]["content"],
            }
        )
        return Reply(202)


class Book:
    """The sheet's tabs, which can also add one."""

    def __init__(self, tabs):
        self.tabs = dict(tabs)
        self.added = []

    def worksheet(self, name):
        if name not in self.tabs:
            raise WorksheetNotFound(name)
        return self.tabs[name]

    def add_worksheet(self, title, rows, cols):
        self.added.append(title)
        self.tabs[title] = FakeWorksheet([[""] * cols], row_count=rows)
        return self.tabs[title]


class Untouchable:
    """A sheet that fails the test if anything reads it."""

    def worksheet(self, name):
        raise AssertionError("the sheet was read")

    def add_worksheet(self, *a, **k):
        raise AssertionError("the sheet was changed")


def sheet(extra_cases=(), finished=(), sent=None):
    cases = [
        case("111", provider="Dr. Doe", email=DOE_ADDRESS),
        case("222", when="April 30th, 2026 08:00 AM MST", provider="Dr. Smith", email=SMITH_ADDRESS),
    ] + list(extra_cases)
    _, tabs = world(cases, finished=finished, sent=sent)
    return Book(tabs)


def go(tmp_path, argv, book, env=None, web=None, config=None, now=NOW):
    """Runs the command. Returns the Web stand-in, to see what was sent."""
    web = web if web is not None else Web()
    main(
        argv,
        spreadsheet=book,
        config=config if config is not None else config_for(tmp_path),
        pause=False,
        environ=LIVE_ENV if env is None else env,
        post=web,
        now=now,
    )
    return web


def refused(tmp_path, argv, env=None):
    """Runs a command that should refuse to start. Returns its exit code."""
    web = Web()
    with pytest.raises(SystemExit) as stopped:
        main(
            argv,
            spreadsheet=Untouchable(),
            config=config_for(tmp_path),
            pause=False,
            environ=LIVE_ENV if env is None else env,
            post=web,
            now=NOW,
        )
    assert web.tokens == 0 and web.sends == []
    assert not os.path.exists(REMINDERS_LOCK)
    return stopped.value.code


def without(env, *names):
    return {k: v for k, v in env.items() if k not in names}


def table(book, tab=SENT_TAB):
    return book.tabs[tab].get_all_values()


# --- The safeguards: what stops a run before it starts -------------------------------


def test_a_real_send_is_refused_unless_go_live_is_switched_on_and_says_how_to_test(tmp_path, capsys):
    assert refused(tmp_path, ["--live"], without(LIVE_ENV, "REMINDERS_GO_LIVE")) == 2
    out = capsys.readouterr().out
    assert "Nothing was done." in out and "REMINDERS_GO_LIVE=true" in out and "--test" in out


@pytest.mark.parametrize("value", ["", "false", "no", "yes", "1", "on", "truee"])
def test_only_the_word_true_switches_real_sending_on(tmp_path, value):
    assert refused(tmp_path, ["--live"], {**LIVE_ENV, "REMINDERS_GO_LIVE": value}) == 2


@pytest.mark.parametrize("value", ["true", "TRUE", "  True "])
def test_true_in_any_capitals_switches_it_on(tmp_path, value):
    web = go(tmp_path, ["--live"], sheet(), {**LIVE_ENV, "REMINDERS_GO_LIVE": value})
    assert [s["to"] for s in web.sends] == [DOE_ADDRESS, SMITH_ADDRESS]


def test_a_test_run_does_not_need_go_live(tmp_path):
    web = go(tmp_path, TEST_RUN, sheet(), without(LIVE_ENV, "REMINDERS_GO_LIVE"))
    assert len(web.sends) == 2


@pytest.mark.parametrize("value", [None, "", "nope", "two words@example.test", "a@@b.test"])
def test_a_test_run_needs_a_valid_test_address(tmp_path, capsys, value):
    env = without(LIVE_ENV, "REMINDERS_TEST_RECIPIENT") if value is None else {**LIVE_ENV, "REMINDERS_TEST_RECIPIENT": value}
    assert refused(tmp_path, TEST_RUN, env) == 2
    assert "REMINDERS_TEST_RECIPIENT" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["MICROSOFT_TENANT_ID", "MICROSOFT_CLIENT_ID", "MICROSOFT_CLIENT_SECRET", "MICROSOFT_SENDER_EMAIL"])
@pytest.mark.parametrize("argv", [["--live"], TEST_RUN])
def test_a_missing_microsoft_setting_is_named_and_stops_either_kind_of_send(tmp_path, capsys, name, argv):
    assert refused(tmp_path, argv, without(LIVE_ENV, name)) == 2
    out = capsys.readouterr().out
    assert name in out
    for private in EVERYTHING_PRIVATE:
        assert private not in out


def test_every_problem_is_listed_at_once(tmp_path, capsys):
    refused(tmp_path, TEST_RUN, without(LIVE_ENV, "REMINDERS_TEST_RECIPIENT", "MICROSOFT_TENANT_ID"))
    out = capsys.readouterr().out
    assert "REMINDERS_TEST_RECIPIENT" in out and "MICROSOFT_TENANT_ID" in out


def test_a_bad_director_address_stops_a_real_send_but_not_a_test(tmp_path, capsys):
    env = {**LIVE_ENV, "STAFF_CC_EMAILS": f"{DIRECTOR}, not-an-address"}
    assert refused(tmp_path, ["--live"], env) == 2
    out = capsys.readouterr().out
    assert "STAFF_CC_EMAILS" in out and "not-an-address" not in out
    assert len(go(tmp_path, TEST_RUN, sheet(), env).sends) == 2


def test_today_cant_be_used_on_a_real_send_and_the_refusal_says_what_to_do(tmp_path, capsys):
    assert refused(tmp_path, ["--live", "--today", "2026-05-11"]) == 2
    out = capsys.readouterr().out
    assert "--today" in out and "--test" in out


def test_a_real_send_with_several_problems_lists_them_all(tmp_path, capsys):
    refused(tmp_path, ["--live", "--today", "2026-05-11"], without(LIVE_ENV, "REMINDERS_GO_LIVE", "MICROSOFT_TENANT_ID"))
    out = capsys.readouterr().out
    assert "--today" in out and "REMINDERS_GO_LIVE" in out and "MICROSOFT_TENANT_ID" in out


def test_today_can_be_used_with_a_test_send_and_a_preview(tmp_path):
    web = go(tmp_path, [*TEST_RUN, "--today", "2026-05-11"], sheet(), now=datetime(2026, 11, 18, tzinfo=timezone.utc))
    assert len(web.sends) == 2
    go(tmp_path, ["--today", "2026-05-11"], sheet())  # a preview


def test_test_without_live_is_not_an_option(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        go(tmp_path, ["--test"], sheet())
    assert stopped.value.code == 2


def test_a_refusal_is_logged_as_a_warning(tmp_path):
    refused(tmp_path, TEST_RUN, without(LIVE_ENV, "REMINDERS_TEST_RECIPIENT"))
    log = read(tmp_path, REMINDERS_LOG)
    assert "Reminders refused (live test)" in log and "WARNING" in log
    refused(tmp_path, ["--live"], without(LIVE_ENV, "REMINDERS_GO_LIVE"))
    assert "Reminders refused (live)" in read(tmp_path, REMINDERS_LOG)


def test_a_refusal_comes_before_reading_the_sheet_and_leaves_no_lock(tmp_path):
    refused(tmp_path, TEST_RUN, without(LIVE_ENV, "MICROSOFT_TENANT_ID"))  # Untouchable fails if it is read
    assert not os.path.exists(REMINDERS_LOCK)


def test_a_refused_run_still_waits_when_asked_to(tmp_path, typed):
    with pytest.raises(SystemExit):
        main([*TEST_RUN, "--pause"], spreadsheet=Untouchable(), config=config_for(tmp_path), environ=without(LIVE_ENV, "MICROSOFT_TENANT_ID"), post=Web(), now=NOW)
    assert typed == ["\nPress Enter to close..."]


def test_a_preview_needs_no_microsoft_settings_and_signs_in_to_nothing(tmp_path, capsys):
    web = go(tmp_path, [], sheet(), {"STAFF_CC_EMAILS": DIRECTOR})
    assert web.tokens == 0 and web.sends == []
    assert "Case 111" in capsys.readouterr().out


# --- A test run ------------------------------------------------------------------------


def test_a_test_run_sends_every_email_to_the_test_address_with_nobody_copied(tmp_path):
    web = go(tmp_path, TEST_RUN, sheet())
    assert [(s["to"], s["cc"]) for s in web.sends] == [(TESTER, []), (TESTER, [])]


def test_a_test_email_is_marked_and_says_who_it_was_for(tmp_path):
    web = go(tmp_path, TEST_RUN, sheet())
    check_in, overdue = web.sends
    assert check_in["subject"] == "[TEST] Check-in: report for DDS case 111"
    assert check_in["body"].startswith("[TEST] This reminder would have gone to Dr. Doe.")
    assert "Dr. Smith, with the Director copied." in overdue["body"]


def test_a_test_run_writes_nothing_and_creates_no_tab(tmp_path):
    book = sheet()
    go(tmp_path, TEST_RUN, book)
    assert all(tab.writes == [] for tab in book.tabs.values())
    assert book.added == [] and SENT_TAB not in book.tabs


def test_a_test_run_never_writes_to_an_existing_log_tab_either(tmp_path):
    book = sheet(sent=[("999", "DAY_1")])
    go(tmp_path, TEST_RUN, book)
    assert [row[0] for row in table(book)] == ["Case ID", "999"]


def test_a_test_run_leaves_a_real_run_still_to_do(tmp_path):
    book = sheet()
    go(tmp_path, TEST_RUN, book)
    go(tmp_path, TEST_RUN, book)  # and again: a test is never "already sent"
    web = go(tmp_path, ["--live"], book)
    assert [s["to"] for s in web.sends] == [DOE_ADDRESS, SMITH_ADDRESS]


def test_the_overdue_notice_can_be_tried_without_a_director_address_set(tmp_path):
    web = go(tmp_path, TEST_RUN, sheet(), without(LIVE_ENV, "STAFF_CC_EMAILS"))
    assert len(web.sends) == 2


def test_it_signs_in_once_however_many_emails_there_are(tmp_path):
    web = go(tmp_path, TEST_RUN, sheet())
    assert web.tokens == 1 and len(web.sends) == 2


def test_with_nothing_due_it_does_not_sign_in(tmp_path, capsys):
    web = go(tmp_path, TEST_RUN, sheet(finished=["111", "222"]))
    assert web.tokens == 0 and web.sends == []
    assert "Sent: nothing today." in capsys.readouterr().out


def test_a_case_in_finished_gets_nothing(tmp_path):
    web = go(tmp_path, TEST_RUN, sheet(finished=["111"]))
    assert [s["subject"] for s in web.sends] == ["[TEST] OVERDUE: report for DDS case 222"]


def test_a_reminder_that_cant_go_is_reported_and_the_others_still_do(tmp_path, capsys):
    web = go(tmp_path, TEST_RUN, sheet(extra_cases=[case("333", email="")]))
    assert len(web.sends) == 2
    assert "Needs attention (1):" in capsys.readouterr().out


def test_the_email_text_is_printed_only_when_asked(tmp_path, capsys):
    go(tmp_path, TEST_RUN, sheet())
    assert "| Hello Dr. Doe," not in capsys.readouterr().out
    go(tmp_path, [*TEST_RUN, "--show-text"], sheet())
    assert "| Hello Dr. Doe," in capsys.readouterr().out


# --- A real send -----------------------------------------------------------------------


def test_each_due_reminder_goes_to_its_provider_and_the_overdue_notice_copies_the_director(tmp_path):
    web = go(tmp_path, ["--live"], sheet())
    check_in, overdue = web.sends
    assert (check_in["to"], check_in["cc"]) == (DOE_ADDRESS, [])
    assert (overdue["to"], overdue["cc"]) == (SMITH_ADDRESS, [DIRECTOR])
    assert check_in["subject"] == "Check-in: report for DDS case 111"
    assert check_in["body"].startswith("Hello Dr. Doe,")
    assert "[TEST]" not in check_in["subject"] + check_in["body"]


def test_a_real_send_signs_in_once(tmp_path):
    web = go(tmp_path, ["--live"], sheet())
    assert web.tokens == 1 and len(web.sends) == 2


def test_a_missing_log_tab_is_created_and_each_email_is_written_to_it(tmp_path):
    book = sheet()
    go(tmp_path, ["--live"], book)
    assert book.added == [SENT_TAB]
    assert table(book) == [
        SENT_HEADERS,
        ["111", "DAY_5", "Dr. Doe", "2026-05-11", "08:30:05"],
        ["222", "DAY_11_OVERDUE", "Dr. Smith", "2026-05-11", "08:30:05"],
    ]


def test_what_was_written_is_what_the_next_run_reads_as_sent(tmp_path):
    book = sheet()
    go(tmp_path, ["--live"], book)
    assert read_reminder_data(book).sent == {("111", "DAY_5"), ("222", "DAY_11_OVERDUE")}


def test_running_again_the_same_day_sends_nothing_more(tmp_path, capsys):
    book = sheet()
    go(tmp_path, ["--live"], book)
    capsys.readouterr()
    web = go(tmp_path, ["--live"], book)
    assert web.sends == [] and web.tokens == 0
    out = capsys.readouterr().out
    assert "Already sent (2):" in out and "Sent: nothing today." in out
    assert len(table(book)) == 3  # no new rows


def test_an_existing_log_tab_is_added_to_not_replaced(tmp_path):
    book = sheet(sent=[("999", "DAY_1")])
    go(tmp_path, ["--live"], book)
    assert book.added == []
    assert [row[0] for row in table(book)] == ["Case ID", "999", "111", "222"]


def test_with_nothing_due_a_real_run_signs_in_to_nothing_and_creates_no_tab(tmp_path, capsys):
    book = sheet(finished=["111", "222"])
    web = go(tmp_path, ["--live"], book)
    assert web.tokens == 0 and web.sends == [] and book.added == []
    assert "Sent: nothing today." in capsys.readouterr().out


def test_a_case_in_finished_gets_nothing_on_a_real_send(tmp_path):
    web = go(tmp_path, ["--live"], sheet(finished=["111"]))
    assert [s["to"] for s in web.sends] == [SMITH_ADDRESS]


def test_a_reminder_with_no_usable_address_needs_attention_and_the_others_still_go(tmp_path, capsys):
    book = sheet(extra_cases=[case("333", email="")])
    web = go(tmp_path, ["--live"], book)
    assert [s["to"] for s in web.sends] == [DOE_ADDRESS, SMITH_ADDRESS]
    assert "Needs attention (1):" in capsys.readouterr().out
    assert [row[0] for row in table(book)][1:] == ["111", "222"]


def test_the_overdue_notice_without_a_director_address_is_held_for_attention_not_sent_bare(tmp_path, capsys):
    env = {k: v for k, v in LIVE_ENV.items() if k != "STAFF_CC_EMAILS"}
    book = sheet()
    web = go(tmp_path, ["--live"], book, env)
    assert [s["to"] for s in web.sends] == [DOE_ADDRESS]
    assert [row[0] for row in table(book)][1:] == ["111"]
    assert "no Director address to copy (set STAFF_CC_EMAILS in .env)" in capsys.readouterr().out


def test_a_preview_with_a_director_address_would_send_the_overdue_notice_copying_them(tmp_path, capsys):
    go(tmp_path, [], sheet())
    out = capsys.readouterr().out
    assert "Would send (2):" in out and "Case 222: Day 11 overdue notice to Dr. Smith, director copied" in out


def test_a_preview_says_the_same_about_the_director_address(tmp_path, capsys):
    go(tmp_path, [], sheet(), without(LIVE_ENV, "STAFF_CC_EMAILS"))
    out = capsys.readouterr().out
    assert "no Director address to copy" in out and "Would send (1):" in out


# --- A send that goes wrong ------------------------------------------------------------


def test_a_refused_email_is_reported_the_rest_go_and_the_run_exits_with_an_error(tmp_path, capsys, alerts):
    web = Web(refuse_cases=["111"])
    with pytest.raises(SystemExit) as stopped:
        go(tmp_path, TEST_RUN, sheet(), web=web)
    assert stopped.value.code == 1
    assert [s["subject"] for s in web.sends] == ["[TEST] OVERDUE: report for DDS case 222"]
    out = capsys.readouterr().out
    assert "Failed to send (1), to be tried again on the next run:" in out
    assert "Case 111: Day 5 report check-in to Dr. Doe (Microsoft refused the email (HTTP 400, ErrorInvalidRecipients))" in out


@pytest.mark.parametrize("argv", [["--live"], TEST_RUN])
def test_the_heading_never_says_something_was_sent_when_every_email_failed(tmp_path, capsys, alerts, argv):
    web = Web(refuse_cases=["111", "222"])
    with pytest.raises(SystemExit):
        go(tmp_path, argv, sheet(), web=web)
    out = capsys.readouterr().out
    assert web.sends == [] and "Sent: nothing today." in out
    heading = out.splitlines()[1]
    assert heading.startswith("LIVE") and " were sent " not in heading and " went to " not in heading


def test_only_what_went_out_is_logged_so_the_failed_one_is_tried_again_next_time(tmp_path, alerts):
    book = sheet()
    with pytest.raises(SystemExit):
        go(tmp_path, ["--live"], book, web=Web(refuse_cases=["111"]))
    assert read_reminder_data(book).sent == {("222", "DAY_11_OVERDUE")}
    web = go(tmp_path, ["--live"], book)  # Microsoft is happy this time
    assert [s["to"] for s in web.sends] == [DOE_ADDRESS]
    assert read_reminder_data(book).sent == {("111", "DAY_5"), ("222", "DAY_11_OVERDUE")}


def test_a_failed_send_makes_the_logged_report_a_warning_that_counts_it(tmp_path, alerts):
    with pytest.raises(SystemExit):
        go(tmp_path, ["--live"], sheet(), web=Web(refuse_cases=["111"]))
    log = read(tmp_path, REMINDERS_LOG)
    assert "WARNING" in log and "Reminders report (live), 1 need(s) attention" in log


def test_a_failed_send_is_alerted_and_logged_as_an_error(tmp_path, alerts):
    with pytest.raises(SystemExit):
        go(tmp_path, TEST_RUN, sheet(), web=Web(refuse_cases=["111"]))
    assert [a[0] for a in alerts] == ["Reminders failed"]
    assert "1 failed" in alerts[0][1] and "reminders_error.log" in alerts[0][1]
    error_log = read(tmp_path, REMINDERS_ERROR_LOG)
    assert "Case 111" in error_log and "was not sent" in error_log


def test_a_clean_run_exits_normally_and_sends_no_alert(tmp_path, alerts):
    go(tmp_path, TEST_RUN, sheet())
    assert alerts == []
    assert read(tmp_path, REMINDERS_ERROR_LOG) == ""


def test_a_failed_run_still_waits_for_a_key_before_exiting(tmp_path, alerts, typed):
    with pytest.raises(SystemExit) as stopped:
        main([*TEST_RUN, "--pause"], spreadsheet=sheet(), config=config_for(tmp_path), environ=LIVE_ENV, post=Web(refuse_cases=["111"]), now=NOW)
    assert stopped.value.code == 1 and typed == ["\nPress Enter to close..."]


def test_when_a_row_cant_be_written_the_run_stops_and_says_which_one_to_add(tmp_path, capsys, alerts):
    class Refuses(FakeWorksheet):
        def update(self, range_name, values):
            raise RuntimeError("the sheet could not be reached")

    book = sheet()
    book.tabs[SENT_TAB] = Refuses([list(SENT_HEADERS)])
    web = Web()
    with pytest.raises(SystemExit) as stopped:
        go(tmp_path, ["--live"], book, web=web)
    assert stopped.value.code == 1
    assert [s["to"] for s in web.sends] == [DOE_ADDRESS]  # the second was never attempted
    out = capsys.readouterr().out
    assert "STOPPED: Case 111: Day 5 report check-in was sent, but writing it to the Reminders Sent tab failed" in out
    assert "Not attempted (1):" in out and "Case 222" in out
    assert "1 reminder(s) sent, 1 not attempted" in out
    assert "stopped over the Reminders Sent tab" in alerts[0][1]
    log = read(tmp_path, REMINDERS_LOG)
    assert "WARNING" in log and "Reminders report (live), 1 need(s) attention" in log
    assert "1 sent, 1 not attempted" in log
    assert not os.path.exists(REMINDERS_LOCK)


@pytest.mark.parametrize("argv", [["--live"], TEST_RUN])
def test_a_refused_sign_in_fails_the_run_loudly_with_nothing_sent_or_logged(tmp_path, alerts, argv):
    book = sheet()
    web = Web(token_status=401)
    with pytest.raises(MailError, match="Microsoft sign-in failed"):
        go(tmp_path, argv, book, web=web)
    assert web.sends == []
    assert SENT_TAB not in book.tabs or table(book) == [SENT_HEADERS]  # at most the new, empty tab
    assert [a[0] for a in alerts] == ["Reminders failed"]
    assert "Traceback" in read(tmp_path, REMINDERS_ERROR_LOG)
    assert not os.path.exists(REMINDERS_LOCK)


# --- The lock --------------------------------------------------------------------------


def test_a_real_or_test_send_holds_reminders_lock_while_it_runs_and_gives_it_back(tmp_path):
    web = go(tmp_path, ["--live"], sheet(), web=Web(watch_lock=True))
    assert set(web.lock_seen) == {"live"}
    assert not os.path.exists(REMINDERS_LOCK)
    web = go(tmp_path, TEST_RUN, sheet(), web=Web(watch_lock=True))
    assert set(web.lock_seen) == {"live test"}
    assert not os.path.exists(REMINDERS_LOCK)


def test_the_lock_is_given_back_when_the_run_fails(tmp_path, alerts):
    with pytest.raises(WorksheetNotFound):
        go(tmp_path, TEST_RUN, Book({}))
    assert not os.path.exists(REMINDERS_LOCK)


def test_a_second_run_while_one_is_going_does_nothing_and_exits_normally(tmp_path, capsys):
    held = acquire(REMINDERS_LOCK, "live", 900)
    book = sheet()
    web = go(tmp_path, ["--live"], book)
    assert web.tokens == 0 and web.sends == [] and book.added == []
    assert "Another live reminders run is already going" in capsys.readouterr().out
    assert "Reminders skipped (live)" in read(tmp_path, REMINDERS_LOG)
    assert os.path.exists(REMINDERS_LOCK)
    with open(REMINDERS_LOCK, encoding="utf-8") as lock:
        assert json.load(lock)["token"] == held.token  # still the first run's


def test_a_lock_older_than_fifteen_minutes_is_taken_over_and_said_so(tmp_path):
    acquire(REMINDERS_LOCK, "live", 900)
    old = time.time() - 16 * 60
    os.utime(REMINDERS_LOCK, (old, old))
    web = go(tmp_path, TEST_RUN, sheet())
    assert len(web.sends) == 2
    assert "Took over a stale reminders lock" in read(tmp_path, REMINDERS_LOG)
    assert not os.path.exists(REMINDERS_LOCK)


def test_a_lock_younger_than_fifteen_minutes_is_respected(tmp_path):
    acquire(REMINDERS_LOCK, "live", 900)
    recent = time.time() - 14 * 60
    os.utime(REMINDERS_LOCK, (recent, recent))
    assert go(tmp_path, TEST_RUN, sheet()).sends == []


def test_the_age_limit_can_be_set_in_the_settings_file(tmp_path):
    acquire(REMINDERS_LOCK, "live", 900)
    old = time.time() - 2 * 60
    os.utime(REMINDERS_LOCK, (old, old))
    config = {**config_for(tmp_path), "reminders_lock_stale_minutes": 1}
    assert len(go(tmp_path, TEST_RUN, sheet(), config=config).sends) == 2


def test_a_bad_age_limit_falls_back_to_the_default_with_a_warning(tmp_path):
    config = {**config_for(tmp_path), "reminders_lock_stale_minutes": 0}
    assert len(go(tmp_path, TEST_RUN, sheet(), config=config).sends) == 2
    assert "reminders_lock_stale_minutes must be a positive number" in read(tmp_path, REMINDERS_LOG)


def test_the_sweeps_lock_does_not_block_the_reminders(tmp_path):
    acquire("sweep.lock", "live", 900)
    assert len(go(tmp_path, TEST_RUN, sheet()).sends) == 2


def test_a_preview_takes_no_lock_and_ignores_one(tmp_path, capsys):
    acquire(REMINDERS_LOCK, "live", 900)
    go(tmp_path, [], sheet())
    assert "Case 111" in capsys.readouterr().out
    assert os.path.exists(REMINDERS_LOCK)


# --- The report and the log ------------------------------------------------------------


def test_each_kind_of_run_says_which_it_was(tmp_path, capsys):
    go(tmp_path, [], sheet())
    assert "PREVIEW: nothing was sent, and nothing was written to the sheet." in capsys.readouterr().out
    go(tmp_path, ["--live"], sheet())
    assert "LIVE: real emails to the providers. Each one sent is written to the Reminders Sent tab." in capsys.readouterr().out
    go(tmp_path, TEST_RUN, sheet())
    out = capsys.readouterr().out
    assert "LIVE TEST: emails go to the test address only, marked [TEST]. Nothing is written to the Reminders Sent tab." in out
    assert "PREVIEW" not in out


def test_the_report_lists_what_was_sent_to_whom_instead_of_what_would_be(tmp_path, capsys):
    go(tmp_path, TEST_RUN, sheet())
    out = capsys.readouterr().out
    assert "Sent (2):" in out and "Would send" not in out
    assert "Case 111: Day 5 report check-in to Dr. Doe (exam Monday, May 4, 2026)" in out
    assert "2 open case(s) looked at: 2 reminder(s) sent, 0 need attention" in out


def test_the_report_of_a_real_send_says_the_director_was_copied(tmp_path, capsys):
    go(tmp_path, ["--live"], sheet())
    out = capsys.readouterr().out
    assert "Case 222: Day 11 overdue notice to Dr. Smith, director copied (exam Thursday, April 30, 2026)" in out


def test_a_test_report_does_not_claim_the_director_was_copied(tmp_path, capsys):
    go(tmp_path, TEST_RUN, sheet())
    out = capsys.readouterr().out
    assert "Case 222: Day 11 overdue notice to Dr. Smith (exam" in out and "director copied" not in out


@pytest.mark.parametrize("argv", [["--live"], TEST_RUN])
def test_a_failure_shows_in_the_summary(tmp_path, capsys, alerts, argv):
    with pytest.raises(SystemExit):
        go(tmp_path, argv, sheet(), web=Web(refuse_cases=["111"]))
    assert "1 reminder(s) sent, 1 failed" in capsys.readouterr().out


def test_the_log_has_when_it_started_each_test_email_and_the_summary_with_the_mode(tmp_path):
    go(tmp_path, TEST_RUN, sheet())
    log = read(tmp_path, REMINDERS_LOG)
    assert "Reminders started (live test, for 2026-05-11)" in log
    assert "Test email for Case 111: Day 5 report check-in to Dr. Doe" in log
    assert "Test email for Case 222: Day 11 overdue notice to Dr. Smith" in log
    assert "Reminders finished (live test) in" in log and "2 sent, 0 need attention" in log


def test_the_log_of_a_real_send_has_when_it_started_each_email_and_the_summary(tmp_path):
    go(tmp_path, ["--live"], sheet())
    log = read(tmp_path, REMINDERS_LOG)
    assert "Reminders started (live, for 2026-05-11)" in log
    assert "Sent Case 111: Day 5 report check-in to Dr. Doe" in log
    assert "Sent Case 222: Day 11 overdue notice to Dr. Smith" in log
    assert "Reminders finished (live) in" in log and "2 sent, 0 need attention" in log


# --- Nothing private reaches the screen, the logs or an alert ------------------------------


@pytest.mark.parametrize(
    "argv, web",
    [
        (TEST_RUN, Web()),
        ([*TEST_RUN, "--show-text"], Web()),
        (TEST_RUN, Web(refuse_cases=["111"])),   # Microsoft's error text names the address
        (TEST_RUN, Web(token_status=401)),
        (["--live"], Web()),
        (["--live", "--show-text"], Web()),
        (["--live"], Web(refuse_cases=["111"])),
        (["--live"], Web(token_status=401)),
    ],
)
def test_no_address_secret_or_token_is_ever_shown_logged_or_alerted(tmp_path, capsys, alerts, argv, web):
    try:
        go(tmp_path, argv, sheet(), web=web)
    except (SystemExit, MailError) as stopped:
        assert not any(private in str(stopped) for private in EVERYTHING_PRIVATE)
    seen = capsys.readouterr().out + read(tmp_path, REMINDERS_LOG) + read(tmp_path, REMINDERS_ERROR_LOG)
    seen += " ".join(a[0] + a[1] for a in alerts)
    for private in EVERYTHING_PRIVATE:
        assert private not in seen, f"{private} reached the output"