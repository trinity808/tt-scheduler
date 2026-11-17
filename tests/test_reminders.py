"""
Tests for the reminders preview: planning which reminders are due (app/reminders.py),
the report it prints (app/reminders_report.py), and the command line, main().

The command only previews, so every test also checks that nothing was written. The
logger is shared by the whole process, so each test starts and ends with it clean
(see tests/log_cleanup.py). It doesn't pass records up to pytest, so the logging tests
read the log files.

The calendar used is May 2026, where May 4 is a Monday. Test addresses are made up and
unique, so a test can check that none of them ever reaches the report or the log.

Run from the project root:
    python -m pytest tests/test_reminders.py -v
"""

from datetime import date, datetime, timezone

import pytest
from gspread.exceptions import WorksheetNotFound

from app.reminders import REMINDERS_ERROR_LOG, REMINDERS_LOG, main, plan_reminders, run_reminders
from app.reminders_report import format_report, summary_counts, summary_line
from app.reminders_sheet import FINISHED_TAB, ONGOING_TAB, SENT_HEADERS, SENT_TAB, read_reminder_data
from tests.fakes import FakeSpreadsheet, FakeWorksheet
from tests.log_cleanup import clean_logger  # noqa: F401


DOE_ADDRESS = "doe.provider.4471@example.test"
SMITH_ADDRESS = "smith.provider.9902@example.test"
ADDRESSES = (DOE_ADDRESS, SMITH_ADDRESS)

HEADERS = ["Case ID", "Patient Name", "Date & Time", "Psychometrist", "Psychometrist Email",
           "Provider", "Provider Email", "Comments"]


def d(day):
    return date(2026, 5, day)


def case(case_id, when="May 4th, 2026 08:00 AM MST", provider="Dr. Doe", email=DOE_ADDRESS):
    return [case_id, "A Patient", when, "John Doe", "john.psych.1@example.test", provider, email, ""]


def world(cases, finished=(), sent=None):
    """A sheet with these case rows, these Finished case IDs, and a Reminders Sent tab if `sent` is given."""
    tabs = {
        ONGOING_TAB: FakeWorksheet([HEADERS] + cases),
        FINISHED_TAB: FakeWorksheet([["Case ID", "Date Completed"]] + [[c, "5/8/2026"] for c in finished]),
    }
    if sent is not None:
        tabs[SENT_TAB] = FakeWorksheet([SENT_HEADERS] + [[c, k, "Provider", "", ""] for c, k in sent])
    return FakeSpreadsheet(tabs), tabs


def plan_for(today, cases, **kwargs):
    book, _ = world(cases, **kwargs)
    return run_reminders(book, today)


def assert_no_address(text):
    for address in ADDRESSES + ("john.psych.1@example.test",):
        assert address not in text, f"an email address reached the output: {address}"


# --- Which reminders are due -------------------------------------------------
# Exam Monday May 4: Day 1 Tue 5, Day 3 Thu 7, Day 5 Mon 11 (Saturday moved on),
# Day 8 Tue 12, Day 10 Thu 14, Day 11 Fri 15.


def test_a_case_with_a_reminder_due_would_be_sent_one_with_the_right_text():
    plan = plan_for(d(11), [case("111")])
    (planned,) = plan.to_send
    assert (planned.case.case_id, planned.reminder.key) == ("111", "DAY_5")
    assert planned.subject == "Check-in: report for DDS case 111"
    assert planned.body.startswith("Hello Dr. Doe,")
    assert (plan.looked_at, plan.nothing_due, plan.problems) == (1, 0, [])


def test_the_text_is_built_for_the_day_it_goes_out():
    plan = plan_for(d(12), [case("111")])  # Day 8, on its own day
    assert "due in 2 days" in plan.to_send[0].subject


def test_a_case_with_nothing_due_today_is_only_counted():
    plan = plan_for(d(6), [case("111")])
    assert plan.to_send == [] and plan.nothing_due == 1 and plan.looked_at == 1


def test_a_case_whose_exam_is_still_ahead_gets_nothing():
    plan = plan_for(d(1), [case("111")])
    assert plan.to_send == [] and plan.nothing_due == 1


def test_a_case_long_past_its_overdue_notice_gets_nothing():
    plan = plan_for(date(2026, 8, 1), [case("111")])
    assert plan.to_send == [] and plan.problems == [] and plan.nothing_due == 1


def test_the_overdue_notice_would_copy_the_director():
    plan = plan_for(d(15), [case("111")])
    assert plan.to_send[0].reminder.key == "DAY_11_OVERDUE" and plan.to_send[0].reminder.cc_director


def test_a_case_in_finished_gets_nothing_even_on_a_day_one_would_be_due():
    plan = plan_for(d(15), [case("111")], finished=["111"])
    assert plan.to_send == [] and plan.finished == 1 and plan.nothing_due == 0 and plan.looked_at == 1


def test_only_the_cases_in_finished_are_skipped():
    plan = plan_for(d(11), [case("111"), case("222"), case("333")], finished=["222"])
    assert [p.case.case_id for p in plan.to_send] == ["111", "333"] and plan.finished == 1


def test_exams_at_different_times_of_day_get_the_same_reminder_on_the_same_day():
    plan = plan_for(d(11), [case("111", "May 4th, 2026 08:00 AM MST"), case("222", "May 4, 2026\n 1:00 PM MST")])
    assert [p.reminder.key for p in plan.to_send] == ["DAY_5", "DAY_5"]


def test_each_case_is_worked_out_from_its_own_exam_date():
    plan = plan_for(d(11), [case("111", "May 4th, 2026 8:00 AM MST"), case("222", "May 6th, 2026 8:00 AM MST")])
    # Wednesday May 6: Day 3 is Saturday, moved on to Monday May 11, with Day 5 the same day
    assert [(p.case.case_id, p.reminder.key) for p in plan.to_send] == [("111", "DAY_5"), ("222", "DAY_5")]


def test_a_case_with_no_reminder_and_one_held_back_is_not_counted_as_nothing_due():
    plan = plan_for(d(7), [case("111")])  # Day 3, which isn't sent while materials aren't tracked
    assert plan.to_send == [] and plan.nothing_due == 0
    assert [(cid, r.key) for cid, r, _ in plan.not_sent] == [("111", "DAY_3")]


def test_day_3_is_held_back_with_the_reason():
    plan = plan_for(d(7), [case("111")])
    assert "materials status isn't tracked yet" in plan.not_sent[0][2]


# --- Already sent --------------------------------------------------------------


def test_a_reminder_in_the_log_is_not_planned_again_and_is_listed_as_already_sent():
    plan = plan_for(d(11), [case("111")], sent=[("111", "DAY_5")])
    assert plan.to_send == [] and [(c, r.key) for c, r in plan.already_sent] == [("111", "DAY_5")]


def test_the_log_is_per_case_and_per_reminder():
    plan = plan_for(d(11), [case("111"), case("222")], sent=[("111", "DAY_5"), ("222", "DAY_1")])
    assert [p.case.case_id for p in plan.to_send] == ["222"]


def test_when_the_later_of_two_reminders_on_one_day_was_sent_the_earlier_is_not_sent_instead():
    # Exam Thursday May 7: Day 8 and Day 10 both go out Friday May 15.
    plan = plan_for(d(15), [case("111", "May 7th, 2026 8:00 AM MST")], sent=[("111", "DAY_10")])
    assert plan.to_send == [] and [r.key for _, r in plan.already_sent] == ["DAY_10"]


def test_a_log_row_typed_by_hand_in_other_capitals_still_counts_as_sent():
    plan = plan_for(d(11), [case("111")], sent=[("111", "Day_5")])
    assert plan.to_send == [] and [(c, r.key) for c, r in plan.already_sent] == [("111", "DAY_5")]


def test_a_log_row_with_an_unknown_name_is_shown_in_the_report_and_does_not_hide_a_reminder():
    book, _ = world([case("111")], sent=[("111", "Day5")])
    plan = plan_reminders(read_reminder_data(book), d(11))
    assert [p.reminder.key for p in plan.to_send] == ["DAY_5"]
    assert "'Day5' isn't a reminder name" in format_report(plan)


def test_a_collision_is_reported_with_the_one_that_wins():
    plan = plan_for(d(15), [case("111", "May 7th, 2026 8:00 AM MST")])
    assert [p.reminder.key for p in plan.to_send] == ["DAY_10"]
    assert [(r.key, "Day 10" in reason) for _, r, reason in plan.not_sent] == [("DAY_8", True)]


# --- Needs attention -----------------------------------------------------------


def test_an_unreadable_exam_date_needs_attention_and_names_the_case():
    plan = plan_for(d(11), [case("111", "garbage")])
    assert plan.to_send == []
    assert plan.problems == ["Case 111: the exam date can't be read from Date & Time (no date found in 'garbage')"]


def test_an_empty_exam_date_needs_attention():
    plan = plan_for(d(11), [case("111", "")])
    assert plan.problems == ["Case 111: the exam date can't be read from Date & Time (it is empty)"]


def test_a_provider_without_an_address_needs_attention_and_says_where_to_look():
    plan = plan_for(d(11), [case("111", email="")])
    assert plan.to_send == []
    assert plan.problems == [
        "Case 111: Day 5 report check-in is due today but can't be sent: Dr. Doe has no email "
        "address (check the Contacts tab, and the note on the Comments cell)"
    ]


def test_a_case_with_no_provider_named_says_so():
    plan = plan_for(d(11), [case("111", provider="", email="")])
    assert "no provider is named for this case in the comments" in plan.problems[0]


@pytest.mark.parametrize("bad", ["not an address", "a@b", "a@@b.test", "two words@example.test", "@example.test x"])
def test_an_address_that_is_not_valid_needs_attention_without_repeating_it(bad):
    plan = plan_for(d(11), [case("111", email=bad)])
    assert plan.to_send == []
    assert plan.problems == [
        "Case 111: Day 5 report check-in is due today but can't be sent: the Provider Email isn't a valid address"
    ]
    assert bad not in plan.problems[0]


def test_a_problem_is_raised_only_on_the_day_a_reminder_is_due():
    assert plan_for(d(6), [case("111", email="")]).problems == []


def test_a_case_with_a_problem_does_not_stop_the_others():
    plan = plan_for(d(11), [case("111", email=""), case("222", provider="Dr. Smith", email=SMITH_ADDRESS)])
    assert [p.case.case_id for p in plan.to_send] == ["222"] and len(plan.problems) == 1


def test_things_about_the_sheet_are_carried_into_the_plan():
    book, _ = world([case("111"), case("111")])
    plan = plan_reminders(read_reminder_data(book), d(11))
    assert plan.notes and "listed more than once" in plan.notes[0]


# --- The report ----------------------------------------------------------------


def test_the_report_says_nothing_was_sent_and_names_the_day():
    text = format_report(plan_for(d(11), [case("111")]))
    assert text.startswith("Reminders for Monday, May 11, 2026 (Arizona time)\n")
    assert "PREVIEW: nothing was sent, and nothing was written to the sheet." in text


def test_a_date_given_with_today_is_labelled_so_it_cant_be_mistaken_for_the_real_day():
    plan = plan_for(d(11), [case("111")])
    assert "the date given with --today" in format_report(plan, today_given=True)
    assert "--today" not in format_report(plan, today_given=False)


def test_the_report_lists_each_reminder_with_the_provider_and_the_subject_but_not_the_address():
    text = format_report(plan_for(d(11), [case("111")]))
    assert "Would send (1):" in text
    assert "Case 111: Day 5 report check-in to Dr. Doe (exam Monday, May 4, 2026)" in text
    assert "Subject: Check-in: report for DDS case 111" in text
    assert_no_address(text)


def test_the_overdue_notice_is_shown_with_the_director_copied():
    text = format_report(plan_for(d(15), [case("111")]))
    assert "Day 11 overdue notice to Dr. Doe, director copied" in text


def test_a_provider_with_no_name_is_called_the_provider():
    text = format_report(plan_for(d(11), [case("111", provider="")]))
    assert "to the provider" in text


def test_the_email_text_is_shown_only_when_asked():
    plan = plan_for(d(11), [case("111")])
    assert "Hello Dr. Doe," not in format_report(plan)
    shown = format_report(plan, show_text=True)
    assert "    | Hello Dr. Doe," in shown and "    | Thank you," in shown
    assert_no_address(shown)


def test_with_nothing_to_send_the_report_says_so():
    assert "Would send: nothing today." in format_report(plan_for(d(6), [case("111")]))


def test_the_report_has_a_section_for_each_kind_of_thing_in_a_fixed_order():
    cases = [case("111"), case("222", email=""), case("333")]
    plan = plan_for(d(11), cases, sent=[("333", "DAY_5")])
    text = format_report(plan)
    order = [text.index(s) for s in ("Would send (1):", "Needs attention (1):", "Already sent (1):")]
    assert order == sorted(order)


def test_held_back_reminders_are_listed_with_their_reason():
    text = format_report(plan_for(d(7), [case("111")]))
    assert "Due but not sent (1):" in text
    assert "- Case 111: Day 3 materials overdue notice (materials status isn't tracked yet, so it isn't sent)" in text


def test_a_missing_log_tab_is_mentioned_and_a_present_one_is_not():
    assert "no 'Reminders Sent' tab yet" in format_report(plan_for(d(11), [case("111")]))
    assert "no 'Reminders Sent' tab" not in format_report(plan_for(d(11), [case("111")], sent=[]))


def test_notes_about_the_sheet_are_in_the_report():
    book, _ = world([case("111"), case("111")])
    text = format_report(plan_reminders(read_reminder_data(book), d(11)))
    assert "About the sheet:" in text and "listed more than once" in text


def test_the_summary_counts_every_kind():
    cases = [case("111"), case("222", email=""), case("333"), case("444"), case("555", "May 11th, 2026 8:00 AM MST")]
    plan = plan_for(d(11), cases, finished=["444"], sent=[("333", "DAY_5")])
    counts = summary_counts(plan)
    assert (counts.looked_at, counts.to_send, counts.problems, counts.already_sent, counts.finished,
            counts.nothing_due) == (5, 1, 1, 1, 1, 1)
    assert format_report(plan).splitlines()[-1] == (
        "5 open case(s) looked at: 1 reminder(s) would be sent, 1 need attention, 1 already sent, "
        "0 held back, 1 in Finished (skipped), 1 with nothing due."
    )


def test_held_back_reminders_are_counted_in_the_summary_and_the_log_line():
    plan = plan_for(d(7), [case("111")])
    assert summary_counts(plan).not_sent == 1
    assert "1 held back" in format_report(plan).splitlines()[-1]
    assert "1 held back" in summary_line(plan, 0.0)


def test_the_log_line_uses_the_same_numbers():
    plan = plan_for(d(11), [case("111")])
    line = summary_line(plan, 1.25)
    assert line.startswith("Reminders finished (preview) in 1.2s: 1 open case(s), 1 would be sent, 0 need attention")


# --- The command ----------------------------------------------------------------


@pytest.fixture(autouse=True)
def scratch_folder(tmp_path, monkeypatch):
    """Run in an empty folder, so a test never leaves log files in the real project."""
    monkeypatch.chdir(tmp_path)


def config_for(folder):
    return {"health_check": {"log_folder": str(folder)}}


def run(tmp_path, argv, book, **kwargs):
    return main(argv, spreadsheet=book, config=config_for(tmp_path), pause=False, **kwargs)


def test_it_prints_the_report_for_the_date_given(tmp_path, capsys):
    book, _ = world([case("111")])
    run(tmp_path, ["--today", "2026-05-11"], book)
    out = capsys.readouterr().out
    assert "Reminders for Monday, May 11, 2026" in out and "Case 111: Day 5 report check-in" in out


def test_without_a_date_it_uses_todays_date_in_arizona(tmp_path, capsys):
    book, _ = world([case("111")])
    # 6:00 am Monday May 11 in Arizona is 1:00 pm UTC; 11 pm Sunday May 10 there is 6:00 am UTC on the 11th
    run(tmp_path, [], book, now=datetime(2026, 5, 11, 13, 0, tzinfo=timezone.utc))
    assert "Reminders for Monday, May 11, 2026" in capsys.readouterr().out
    run(tmp_path, [], book, now=datetime(2026, 5, 11, 6, 0, tzinfo=timezone.utc))
    assert "Reminders for Sunday, May 10, 2026" in capsys.readouterr().out


def test_it_writes_nothing_to_any_tab(tmp_path, capsys):
    book, tabs = world([case("111"), case("222", email="")], sent=[])
    run(tmp_path, ["--today", "2026-05-11"], book)
    for tab in tabs.values():
        assert tab.writes == [] and tab.notes == {} and tab.formats == {}


def test_it_never_prints_an_address(tmp_path, capsys):
    book, _ = world([case("111"), case("222", provider="Dr. Smith", email=SMITH_ADDRESS)])
    run(tmp_path, ["--today", "2026-05-11", "--show-text"], book)
    assert_no_address(capsys.readouterr().out)


def test_running_it_twice_gives_the_same_report(tmp_path, capsys):
    book, _ = world([case("111")])
    run(tmp_path, ["--today", "2026-05-11"], book)
    first = capsys.readouterr().out
    run(tmp_path, ["--today", "2026-05-11"], book)
    assert capsys.readouterr().out == first


def test_the_email_text_is_not_printed_unless_asked(tmp_path, capsys):
    book, _ = world([case("111")])
    run(tmp_path, ["--today", "2026-05-11"], book)
    assert "Hello Dr. Doe," not in capsys.readouterr().out


def test_show_text_prints_the_emails(tmp_path, capsys):
    book, _ = world([case("111")])
    run(tmp_path, ["--today", "2026-05-11", "--show-text"], book)
    assert "| Hello Dr. Doe," in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--live"], ["--send"], ["--lve"], ["--today"], ["--today", "May 11"], ["--today", "2026-13-01"]])
def test_there_is_no_way_to_send_yet_and_a_bad_option_or_date_is_refused(tmp_path, argv):
    book, tabs = world([case("111")])
    with pytest.raises(SystemExit):
        run(tmp_path, argv, book)
    assert all(tab.writes == [] for tab in tabs.values())


def test_without_a_sheet_given_it_opens_the_real_one(monkeypatch, tmp_path, capsys):
    book, _ = world([case("111")])
    monkeypatch.setattr("app.sheet_client.get_client", lambda: book)
    main(["--today", "2026-05-11"], config=config_for(tmp_path), pause=False)
    assert "Case 111" in capsys.readouterr().out


# --- The pause -------------------------------------------------------------------


@pytest.fixture
def typed(monkeypatch):
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt))
    return prompts


def test_it_never_waits_unless_asked(tmp_path, typed):
    book, _ = world([case("111")])
    main(["--today", "2026-05-11"], spreadsheet=book, config=config_for(tmp_path))
    assert typed == []


def test_pause_waits_for_a_key(tmp_path, typed):
    book, _ = world([case("111")])
    main(["--today", "2026-05-11", "--pause"], spreadsheet=book, config=config_for(tmp_path))
    assert len(typed) == 1 and "Press Enter to close" in typed[0]


def test_pause_with_nothing_to_read_from_just_finishes(tmp_path, monkeypatch, capsys):
    def no_input(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_input)
    book, _ = world([case("111")])
    main(["--today", "2026-05-11", "--pause"], spreadsheet=book, config=config_for(tmp_path))
    assert "Case 111" in capsys.readouterr().out


def test_the_pause_can_be_forced_either_way(tmp_path, typed):
    book, _ = world([case("111")])
    main(["--today", "2026-05-11"], spreadsheet=book, config=config_for(tmp_path), pause=True)
    assert len(typed) == 1
    main(["--today", "2026-05-11", "--pause"], spreadsheet=book, config=config_for(tmp_path), pause=False)
    assert len(typed) == 1


# --- The log ---------------------------------------------------------------------


def read(tmp_path, name):
    return (tmp_path / name).read_text(encoding="utf-8")


def test_a_run_logs_when_it_started_the_whole_report_and_when_it_finished(tmp_path):
    book, _ = world([case("111")])
    run(tmp_path, ["--today", "2026-05-11"], book)
    log = read(tmp_path, REMINDERS_LOG)
    assert "Reminders started (preview, for 2026-05-11)" in log
    assert "Reminders report (preview):" in log and "Case 111: Day 5 report check-in" in log
    assert "Reminders finished (preview)" in log and "1 would be sent" in log


def test_the_log_never_holds_an_address(tmp_path):
    book, _ = world([case("111"), case("222", provider="Dr. Smith", email=SMITH_ADDRESS)])
    run(tmp_path, ["--today", "2026-05-11", "--show-text"], book)
    assert_no_address(read(tmp_path, REMINDERS_LOG))


def test_a_problem_is_logged_as_a_warning_and_a_clean_run_is_not(tmp_path):
    book, _ = world([case("111", email="")])
    run(tmp_path, ["--today", "2026-05-11"], book)
    lines = read(tmp_path, REMINDERS_LOG).splitlines()
    assert any("WARNING" in line and "Reminders report (preview), 1 need(s) attention" in line for line in lines)


def test_a_clean_run_logs_no_warning(tmp_path):
    book, _ = world([case("111")])
    run(tmp_path, ["--today", "2026-05-11"], book)
    assert "WARNING" not in read(tmp_path, REMINDERS_LOG)
    assert read(tmp_path, REMINDERS_ERROR_LOG) == ""


def test_it_has_its_own_log_files_apart_from_the_sweeps_and_the_pipelines(tmp_path):
    book, _ = world([case("111")])
    run(tmp_path, ["--today", "2026-05-11"], book)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([REMINDERS_LOG, REMINDERS_ERROR_LOG])


def test_a_failure_is_logged_with_its_traceback_alerted_and_raised_again(tmp_path, monkeypatch):
    alerts = []
    monkeypatch.setattr("app.reminders.notify_error", lambda subject, message, config, exc_info: alerts.append((subject, message)))
    book = FakeSpreadsheet({FINISHED_TAB: FakeWorksheet([["Case ID"]])})  # no Ongoing tab
    with pytest.raises(WorksheetNotFound):
        run(tmp_path, ["--today", "2026-05-11"], book)
    assert "Reminders failed (preview)" in read(tmp_path, REMINDERS_ERROR_LOG)
    assert "Traceback" in read(tmp_path, REMINDERS_ERROR_LOG)
    assert len(alerts) == 1
    assert alerts[0][0] == "Reminders failed" and alerts[0][1].startswith("The reminders failed (preview): ")


def test_a_tab_missing_a_column_fails_loudly_and_is_logged(tmp_path, monkeypatch):
    monkeypatch.setattr("app.reminders.notify_error", lambda *a, **k: None)
    book, _ = world([case("111")])
    book.worksheet(ONGOING_TAB).rows[0] = [h for h in HEADERS if h != "Provider Email"]
    with pytest.raises(ValueError, match="no 'Provider Email' column"):
        run(tmp_path, ["--today", "2026-05-11"], book)
    assert "no 'Provider Email' column" in read(tmp_path, REMINDERS_ERROR_LOG)


def test_an_unreadable_settings_file_does_not_stop_the_run(monkeypatch, capsys):
    def broken():
        raise FileNotFoundError("config/settings.json")

    monkeypatch.setattr("app.reminders.load_config", broken)
    book, _ = world([case("111")])
    main(["--today", "2026-05-11"], spreadsheet=book, pause=False)
    assert "Case 111" in capsys.readouterr().out
    with open("logs/reminders.log", encoding="utf-8") as log:
        assert "Could not read config/settings.json" in log.read()