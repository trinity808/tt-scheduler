"""Scratch copy: the report tests you pasted, verbatim."""

import pytest

from app.apply_comment_updates import ONGOING_TAB, SweepReport, run_sweep
from app.comment_parser import ParsedComment, ServiceChange
from app.services_cell import ServiceLine
from app.services_tab import SERVICES_TAB, ServicesTab
from app.services_update import ServicesUpdate
from app.sweep_report import APPLY_COMMAND, format_report
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


def test_no_comments_says_so():
    assert format_report(report()) == "No rows had a comment to process."


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


def test_flagged_comment_lists_each_problem_on_its_own_line():
    text = format_report(report([
        result("111", unrecognized_lines=["Add: wisk  (skipped: x)", "Notes: hi  (skipped: y)"]),
    ]))
    assert "NEEDS ATTENTION:\n    Add: wisk  (skipped: x)\n    Notes: hi  (skipped: y)" in text


def test_flagged_comment_does_not_also_claim_there_were_no_names():
    text = format_report(report([result("111", unrecognized_lines=["Add: wisk  (skipped: x)"])]))
    assert "No Provider/Psychometrist" not in text


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



# --- Tests applied to the sheet -------------------------------------

PSYCH = "90791"
AUTISM = "96130-AUTISM"
WPPSI = "96101-WPPSI-IV"
ORIGINAL_LINES = [ServiceLine(WISC), ServiceLine(PSYCH), ServiceLine(AUTISM)]


def line(code, marker="", changed_from=""):
    return ServiceLine(code, marker=marker, changed_from=changed_from)


def applied_result(case_id, status, before=(), after=(), original_to_store=None,
                 flags=(), fields=None, **parsed):
    """A result as the sweep builds it when it has a metadata client."""
    update = ServicesUpdate(
        status="update" if status == "updated" else "unchanged",
        lines=list(after),
        original_to_store=original_to_store,
        flags=list(flags),
    )
    return {
        "case_id": case_id,
        "parsed": ParsedComment(**parsed),
        "fields": fields or {},
        "flags": list(flags),
        "services": {"status": status, "update": update, "detail": "", "before": list(before)},
    }


def outcome_only(case_id, status, **parsed):
    """A result whose tests were never looked at (no update object)."""
    return {
        "case_id": case_id,
        "parsed": ParsedComment(**parsed),
        "fields": {},
        "flags": [],
        "services": {"status": status, "update": None, "detail": "", "before": None},
    }


def test_an_added_test_is_described_as_this_runs_change():
    text = format_report(report([applied_result(
        "111", "updated", ORIGINAL_LINES, ORIGINAL_LINES + [line(CARS, "added")],
    )]))
    assert "  Tests updated:\n    add 96130-CARS-2" in text
    assert "not applied" not in text


def test_a_swap_is_described_as_a_change_from_one_test_to_the_other():
    after = [line(WAIS, "changed", WISC), line(PSYCH), line(AUTISM)]
    text = format_report(report([applied_result("111", "updated", ORIGINAL_LINES, after)]))
    assert f"    change {WISC} to {WAIS}" in text


def test_a_swap_of_a_swap_is_described_against_the_cell_not_the_original():
    before = [line(WAIS, "changed", WISC), line(PSYCH), line(AUTISM)]
    after = [line(WPPSI, "changed", WISC), line(PSYCH), line(AUTISM)]
    text = format_report(report([applied_result("111", "updated", before, after)]))
    assert f"    change {WAIS} to {WPPSI}" in text
    assert "restore" not in text and "remove" not in text


def test_an_original_test_coming_back_is_a_restore():
    before = [line(WISC), line(AUTISM)]
    text = format_report(report([applied_result("111", "updated", before, ORIGINAL_LINES)]))
    assert f"    restore {PSYCH}" in text


def test_a_test_that_goes_is_a_removal():
    before = ORIGINAL_LINES + [line(CARS, "added")]
    text = format_report(report([applied_result("111", "updated", before, ORIGINAL_LINES)]))
    assert f"    remove {CARS}" in text


def test_an_add_and_a_swap_in_one_run_are_both_listed():
    after = [line(WAIS, "changed", WISC), line(PSYCH), line(AUTISM), line(CARS, "added")]
    text = format_report(report([applied_result("111", "updated", ORIGINAL_LINES, after)]))
    assert f"change {WISC} to {WAIS}" in text and f"add {CARS}" in text


def test_a_rewrite_that_changes_no_test_says_so():
    after = [ServiceLine(WISC, "Wechsler"), line(PSYCH), line(AUTISM)]
    text = format_report(report([applied_result("111", "updated", ORIGINAL_LINES, after)]))
    assert "Tests: cell rewritten (wording, markers or layout), no tests changed" in text
    assert "Tests updated:" not in text


def test_a_preview_of_a_rewrite_that_changes_no_test_says_so():
    after = [ServiceLine(WISC, "Wechsler"), line(PSYCH), line(AUTISM)]
    text = format_report(dry_report([applied_result("111", "updated", ORIGINAL_LINES, after)]))
    assert "Tests: cell would be rewritten (wording, markers or layout), no tests change" in text


def test_a_case_that_is_already_up_to_date_says_so():
    text = format_report(report([applied_result(
        "111", "unchanged", ORIGINAL_LINES, ORIGINAL_LINES,
        service_changes=[ServiceChange("add", WISC)],
    )]))
    assert "Tests: already up to date, no change" in text
    assert "No Provider/Psychometrist" not in text


def test_a_case_whose_changes_were_all_skipped_is_not_called_up_to_date():
    text = format_report(report([applied_result(
        "111", "unchanged", ORIGINAL_LINES, ORIGINAL_LINES,
        flags=[f"Add: {AUTISM}  (skipped: {AUTISM} is already on this case)"],
        service_changes=[ServiceChange("add", AUTISM)],
    )]))
    assert "already up to date" not in text
    assert "NEEDS ATTENTION:" in text


def test_a_case_with_no_test_changes_reads_as_before():
    text = format_report(report([outcome_only("111", "none")]))
    assert "No Provider/Psychometrist in comment" in text


def test_without_a_metadata_client_the_changes_are_still_listed_as_not_applied():
    text = format_report(report([outcome_only(
        "111", "not_applied", service_changes=[ServiceChange("add", CARS)],
    )]))
    assert "Test changes found (not applied to the sheet yet):" in text
    assert f"add {CARS}" in text
    assert "1 test change(s) found, not applied to the sheet yet." in text


def test_a_saved_original_is_mentioned():
    text = format_report(report([applied_result(
        "111", "updated", ORIGINAL_LINES, ORIGINAL_LINES + [line(CARS, "added")],
        original_to_store=[WISC, PSYCH, AUTISM],
    )]))
    assert f"Original tests saved from the sheet: {WISC}, {PSYCH}, {AUTISM}" in text
    assert "1 original list(s) saved." in text


def test_an_original_that_could_not_be_saved_is_not_reported_as_saved():
    result_ = applied_result("111", "store_failed", ORIGINAL_LINES, ORIGINAL_LINES,
                           original_to_store=[WISC], flags=["Services Requested  (not changed: x)"])
    text = format_report(report([result_]))
    assert "saved" not in text
    assert "NEEDS ATTENTION:" in text


def test_refused_cases_show_their_flag_and_no_test_lines():
    text = format_report(report([applied_result(
        "111", "refused", flags=["Services Requested  (not changed: it has a line that can't be read)"],
    )]))
    assert "NEEDS ATTENTION:" in text
    assert "Tests updated" not in text and "up to date" not in text


def test_the_flag_list_uses_every_flag_not_just_the_parsers():
    text = format_report(report([
        applied_result("111", "unchanged", flags=["Change: x to y  (skipped: x isn't on this case)"]),
        applied_result("222", "unchanged"),
    ]))
    assert "Flagged cases: 111" in text
    assert "    Change: x to y  (skipped: x isn't on this case)" in text


def test_the_missing_column_is_said_once_not_once_per_case():
    text = format_report(report([
        outcome_only("111", "no_column", service_changes=[ServiceChange("add", CARS)]),
        outcome_only("222", "no_column", service_changes=[ServiceChange("add", CARS)]),
    ]))
    assert text.count("no 'Services Requested' column") == 1
    assert text.index("no 'Services Requested' column") < text.index("Case 111")


def test_summary_counts_cases_with_tests_updated():
    after = ORIGINAL_LINES + [line(CARS, "added")]
    text = format_report(report([
        applied_result("111", "updated", ORIGINAL_LINES, after),
        applied_result("222", "updated", ORIGINAL_LINES, after),
        applied_result("333", "unchanged", ORIGINAL_LINES, ORIGINAL_LINES),
    ]))
    assert "2 case(s) had their tests updated." in text


# --- Preview (dry run) ----------------------------------------------


def dry_report(results=(), problems=(), flag="dry_run"):
    return SweepReport(
        results=list(results),
        services=ServicesTab(problems=list(problems)),
        services_flag=flag,
        dry_run=True,
    )


def test_a_preview_says_nothing_was_written_before_anything_else():
    text = format_report(dry_report([result("111")]))
    assert text.startswith("DRY RUN: nothing was written to the sheet.")


def test_a_live_report_has_no_banner_and_no_apply_command():
    text = format_report(report([result("111")]))
    assert "DRY RUN" not in text
    assert APPLY_COMMAND not in text


def test_a_preview_uses_the_conditional_for_fields():
    text = format_report(dry_report([result("111", fields={
        "Provider": ("updated", "Dr. Smith", "Dr. Doe"),
        "Psychometrist": ("updated", "", "J. Smith"),
    })]))
    assert "Provider: would be updated from Dr. Smith to Dr. Doe" in text
    assert "Psychometrist: would be set to J. Smith" in text


def test_a_preview_uses_the_conditional_for_tests():
    after = ORIGINAL_LINES + [line(CARS, "added")]
    text = format_report(dry_report([applied_result(
        "111", "updated", ORIGINAL_LINES, after, original_to_store=[WISC, PSYCH, AUTISM],
    )]))
    assert "Tests that would be updated:\n    add 96130-CARS-2" in text
    assert "Original tests would be saved from the sheet:" in text


def test_a_preview_summary_and_closing_line():
    after = ORIGINAL_LINES + [line(CARS, "added")]
    text = format_report(dry_report([
        applied_result("111", "updated", ORIGINAL_LINES, after,
                     original_to_store=[WISC], fields={"Provider": ("updated", "", "Dr. Doe")}),
    ]))
    assert "1 field(s) would be updated" in text
    assert "1 case(s) would have their tests updated." in text
    assert "1 original list(s) would be saved." in text
    assert text.rstrip().endswith(f"To apply this for real, run: {APPLY_COMMAND}")


def test_a_preview_says_it_did_not_note_the_services_tab():
    text = format_report(dry_report(problems=[PROBLEM]))
    assert "(dry run: not noted on the Services tab)" in text


def test_a_preview_with_nothing_to_process_has_no_apply_command():
    text = format_report(dry_report())
    assert text.startswith("DRY RUN")
    assert "No rows had a comment to process." in text
    assert APPLY_COMMAND not in text


# --- The summary reads one fact per line ---------------------------


def summary_report(dry_run):
    after = ORIGINAL_LINES + [line(CARS, "added")]
    results = [
        applied_result("111", "updated", ORIGINAL_LINES, after, original_to_store=[WISC],
                       fields={"Provider": ("updated", "", "Dr. Doe")}),
        applied_result("222", "unchanged", flags=["Add: x  (skipped: y)"]),
        outcome_only("333", "not_applied", service_changes=[ServiceChange("add", CARS)]),
    ]
    flag = "dry_run" if dry_run else "unchanged"
    return SweepReport(
        results=results, services=ServicesTab(problems=[]), services_flag=flag, dry_run=dry_run
    )


def test_each_part_of_the_summary_is_on_its_own_line():
    lines = format_report(summary_report(dry_run=False)).splitlines()
    assert "3 comment(s) processed, 1 field(s) updated, 1 flagged for attention." in lines
    assert "1 test change(s) found, not applied to the sheet yet." in lines
    assert "1 case(s) had their tests updated." in lines
    assert "1 original list(s) saved." in lines
    assert "Flagged cases: 222" in lines


def test_a_preview_summary_also_reads_one_fact_per_line():
    lines = format_report(summary_report(dry_run=True)).splitlines()
    assert "3 comment(s) processed, 1 field(s) would be updated, 1 flagged for attention." in lines
    assert "1 case(s) would have their tests updated." in lines
    assert "1 original list(s) would be saved." in lines
    assert f"To apply this for real, run: {APPLY_COMMAND}" in lines


def test_the_summary_lines_are_together_and_in_order():
    lines = format_report(summary_report(dry_run=False)).splitlines()
    start = next(i for i, text in enumerate(lines) if "comment(s) processed" in text)
    assert lines[start + 1:start + 5] == [
        "1 test change(s) found, not applied to the sheet yet.",
        "1 case(s) had their tests updated.",
        "1 original list(s) saved.",
        "Flagged cases: 222",
    ]