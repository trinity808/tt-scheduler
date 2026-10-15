"""
Tests for app/sweep_report.py: the plain-text report printed after a
sweep. Most tests build a SweepReport by hand so each wording rule is
checked on its own. One runs a whole sweep on the in-memory fakes and
formats the result.

Run from the project root:
    python -m pytest tests/test_sweep_report.py -v
"""

import pytest

from app.apply_comment_updates import ONGOING_TAB, SweepReport, run_sweep
from app.comment_parser import ParsedComment, ServiceChange
from app.services_tab import SERVICES_TAB, ServicesTab
from app.sweep_report import format_report
from tests.fakes import FakeSpreadsheet, FakeWorksheet


def report(results=(), problems=(), flag="unchanged"):
    return SweepReport(
        results=list(results),
        services=ServicesTab(problems=list(problems)),
        services_flag=flag,
    )


def result(case_id, fields=None, **parsed):
    return {"case_id": case_id, "parsed": ParsedComment(**parsed), "fields": fields or {}}


CARS = "96130-CARS-2"
WISC = "96101-WISC-V"
WAIS = "96101-WAIS-IV"


# --- Nothing to report ----------------------------------------------


def test_no_comments_says_so():
    assert format_report(report()) == "No rows had a comment to process."


# --- Fields ---------------------------------------------------------


@pytest.mark.parametrize(
    "outcome, expected",
    [
        (("updated", "", "Dr. Shelton"), "Provider: set to Dr. Shelton"),
        (("updated", "Dr. Smith", "Dr. Shelton"), "Provider: updated from Dr. Smith to Dr. Shelton"),
        (("unchanged", "Dr. Shelton", "Dr. Shelton"), "Provider: already Dr. Shelton, no change"),
    ],
)
def test_field_outcomes_are_described_by_what_actually_happened(outcome, expected):
    text = format_report(report([result("111", fields={"Provider": outcome})]))
    assert expected in text


def test_comment_without_names_or_problems_says_so():
    text = format_report(report([result("111")]))
    assert "No Provider/Psychometrist in comment" in text


# --- Flagged comments -----------------------------------------------


def test_flagged_comment_lists_each_problem_on_its_own_line():
    text = format_report(report([
        result("111", unrecognized_lines=["Add: wisk  (skipped: x)", "Notes: hi  (skipped: y)"]),
    ]))
    assert "NEEDS ATTENTION:\n    Add: wisk  (skipped: x)\n    Notes: hi  (skipped: y)" in text


def test_flagged_comment_does_not_also_claim_there_were_no_names():
    text = format_report(report([result("111", unrecognized_lines=["Add: wisk  (skipped: x)"])]))
    assert "No Provider/Psychometrist" not in text


# --- Test changes ---------------------------------------------------


def test_test_changes_are_listed_in_plain_words_and_marked_not_applied():
    text = format_report(report([
        result("111", service_changes=[
            ServiceChange("add", CARS),
            ServiceChange("change", WAIS, replaces=WISC),
        ]),
    ]))
    assert "Test changes found (not applied to the sheet yet):" in text
    assert f"    add {CARS}" in text
    assert f"    change {WISC} to {WAIS}" in text


# --- Summary --------------------------------------------------------


def test_summary_counts_comments_updates_flags_and_test_changes():
    text = format_report(report([
        result("111",
               fields={"Provider": ("updated", "", "Dr. Shelton"),
                       "Psychometrist": ("unchanged", "J. Smith", "J. Smith")},
               service_changes=[ServiceChange("add", CARS)]),
        result("222", unrecognized_lines=["Add: wisk  (skipped: x)"]),
    ]))
    assert "2 comment(s) processed, 1 field(s) updated, 1 flagged for attention." in text
    assert "1 test change(s) found, not applied to the sheet yet." in text
    assert "Flagged cases: 222" in text


def test_summary_omits_flagged_list_and_test_change_count_when_there_are_none():
    text = format_report(report([result("111", fields={"Provider": ("updated", "", "X")})]))
    assert "Flagged cases" not in text
    assert "test change(s)" not in text


# --- The Services tab section ---------------------------------------


PROBLEM = "Row 5: 'Beery VMI' isn't a valid service code, so it was skipped"


@pytest.mark.parametrize(
    "flag, expected_note",
    [
        ("flagged", "(noted on the Services tab)"),
        ("unchanged", "(already noted on the Services tab)"),
        ("skipped", "cell A1 already has someone else's note"),
    ],
)
def test_services_problems_are_listed_with_what_happened_to_the_tab_note(flag, expected_note):
    text = format_report(report(problems=[PROBLEM], flag=flag))
    assert "Services tab needs attention:" in text
    assert f"  - {PROBLEM}" in text
    assert expected_note in text


def test_missing_tab_lists_the_problem_without_a_tab_note_line():
    text = format_report(report(problems=["No tab named 'Services' was found"], flag="no_tab"))
    assert "No tab named 'Services' was found" in text
    assert "noted on the Services tab" not in text
    assert "someone else's note" not in text


def test_clean_services_tab_adds_nothing_to_the_report():
    text = format_report(report([result("111")], flag="unchanged"))
    assert "Services tab" not in text


def test_cleared_note_is_mentioned():
    text = format_report(report([result("111")], flag="cleared"))
    assert "Services tab problems are fixed, so its note was cleared." in text


def test_services_section_comes_before_the_cases():
    text = format_report(report([result("111")], problems=[PROBLEM], flag="flagged"))
    assert text.index("Services tab needs attention") < text.index("Case 111")


# --- End to end -----------------------------------------------------


def test_whole_sweep_formatted():
    ongoing = FakeWorksheet([
        ["Case ID", "Provider", "Psychometrist", "Comments"],
        ["15419897", "", "", "Provider: Dr. Shelton\nAdd: cars"],
        ["15419898", "", "", "Add: wisk"],
    ])
    services = FakeWorksheet([
        ["Code", "Aliases", "Name"],
        ["96101-WISC-V", "WISC", ""],
        ["96130-CARS-2", "CARS", ""],
        ["Beery VMI", "", ""],
    ])
    sheet = FakeSpreadsheet({ONGOING_TAB: ongoing, SERVICES_TAB: services})

    text = format_report(run_sweep(sheet))

    assert "Services tab needs attention:" in text
    assert "Beery VMI" in text
    assert "Case 15419897:" in text
    assert "Provider: set to Dr. Shelton" in text
    assert f"add {CARS}" in text
    assert "unknown test 'wisk', did you mean WISC?" in text
    assert "Flagged cases: 15419898" in text