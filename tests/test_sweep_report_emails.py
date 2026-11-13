"""
Tests for how the sweep report and its log line talk about emails and the Contacts
tab (app/sweep_report.py), using a real sweep over in-memory sheets.

The addresses are made up (example.test). No real person's name or address belongs
in this file.

Run from the project root:
    python -m pytest tests/test_sweep_report_emails.py -v
"""

from types import SimpleNamespace

from app.apply_comment_updates import SweepReport, run_sweep
from app.services_tab import ServicesTab
from app.sweep_report import format_report, summary_counts, summary_line
from tests.fakes import FakeSpreadsheet, FakeWorksheet


HEADERS = ["Case ID", "Provider", "Psychometrist", "Provider Email", "Psychometrist Email", "Comments"]
PLAIN_HEADERS = ["Case ID", "Provider", "Psychometrist", "Comments"]
CONTACTS = [
    ["Name", "Email", "Role"],
    ["Dr. Jane Smith", "jane.smith@example.test", "Provider"],
    ["John Doe", "john.doe@example.test", "Psychometrist"],
    ["Jane Doe", "jane.doe@example.test", "Psychometrist"],
]
BOTH = "Provider: Dr Smith\nPsychometrist: John Doe"


def sheet(*rows, headers=HEADERS, contacts=CONTACTS):
    ongoing = FakeWorksheet([headers, *rows])
    tabs = {"Ongoing": ongoing}
    contacts_tab = None
    if contacts is not None:
        contacts_tab = FakeWorksheet([list(r) for r in contacts])
        tabs["Contacts"] = contacts_tab
    return FakeSpreadsheet(tabs), ongoing, contacts_tab


def row(comment, provider="", provider_email="", case_id="111"):
    return [case_id, provider, "", provider_email, "", comment]


def report_text(spreadsheet, dry_run=False):
    return format_report(run_sweep(spreadsheet, dry_run=dry_run))


def case_block(text, case_id="111"):
    """The lines of one case, from its heading to the next blank line."""
    lines = text.split("\n\n")
    return next(block for block in lines if block.startswith(f"Case {case_id}:"))


# --- What the report says about each email ------------------------------


def test_a_filled_email_is_reported_by_name_right_after_its_field():
    spreadsheet, _, _ = sheet(row(BOTH))
    block = case_block(report_text(spreadsheet))
    assert block == (
        "Case 111:\n"
        "  Provider: set to Dr Smith\n"
        "  Provider email: set for Dr. Jane Smith\n"
        "  Psychometrist: set to John Doe\n"
        "  Psychometrist email: set for John Doe"
    )


def test_a_preview_says_would_be_set():
    spreadsheet, _, _ = sheet(row(BOTH))
    block = case_block(report_text(spreadsheet, dry_run=True))
    assert "  Provider email: would be set for Dr. Jane Smith" in block
    assert "  Psychometrist email: would be set for John Doe" in block


def test_an_email_already_there_says_so():
    spreadsheet, _, _ = sheet(row(BOTH))
    report_text(spreadsheet)
    block = case_block(report_text(spreadsheet))
    assert "  Provider email: already filled in for Dr. Jane Smith, no change" in block
    assert "  Psychometrist email: already filled in for John Doe, no change" in block


def test_a_corrected_address_in_the_tab_is_reported_as_updated():
    spreadsheet, _, contacts_tab = sheet(row(BOTH))
    report_text(spreadsheet)
    contacts_tab.rows[1][1] = "new.address@example.test"
    block = case_block(report_text(spreadsheet))
    assert "  Provider email: updated for Dr. Jane Smith" in block
    assert "new.address" not in block


def test_a_name_that_cannot_be_found_clears_the_old_address_and_says_so():
    spreadsheet, _, _ = sheet(row("Provider: Dr Patel", provider="Dr Smith", provider_email="old@example.test"))
    block = case_block(report_text(spreadsheet))
    assert "  Provider email: old address cleared, as the new provider couldn't be found" in block
    assert "NEEDS ATTENTION:" in block and "isn't in the Contacts tab" in block


def test_a_preview_says_the_old_address_would_be_cleared():
    spreadsheet, _, _ = sheet(row("Provider: Dr Patel", provider="Dr Smith", provider_email="old@example.test"))
    block = case_block(report_text(spreadsheet, dry_run=True))
    assert "  Provider email: old address would be cleared, as the new provider couldn't be found" in block


def test_an_email_that_could_not_be_filled_has_no_line_of_its_own_only_the_flag():
    spreadsheet, _, _ = sheet(row("Provider: Dr S"))
    block = case_block(report_text(spreadsheet))
    assert "Provider email:" not in block
    assert block.endswith(
        "  NEEDS ATTENTION:\n"
        "    Provider Email  (not filled in: 'Dr S' is too short to match. "
        "Write the name in full, for example 'Dr Smith')"
    )


def test_a_sheet_without_the_email_columns_reports_exactly_what_it_always_did():
    spreadsheet, _, _ = sheet(["111", "", "", "Provider: Dr Smith"], headers=PLAIN_HEADERS)
    assert report_text(spreadsheet) == (
        "Services tab needs attention:\n"
        "  - No tab named 'Services' was found, so test names in comments can't be looked up\n"
        "\n"
        "Case 111:\n"
        "  Provider: set to Dr Smith\n"
        "\n"
        "1 comment(s) processed, 1 field(s) updated, 0 flagged for attention."
    )


def test_no_address_appears_anywhere_in_a_report():
    spreadsheet, _, _ = sheet(
        row(BOTH, case_id="1"),
        row("Provider: Dr Patel", provider="Dr Smith", provider_email="old@example.test", case_id="2"),
        row("Provider: Dr S", case_id="3"),
    )
    for dry_run in (False, True):
        assert "@" not in report_text(spreadsheet, dry_run=dry_run)


# --- The summary and the log line ---------------------------------------


def test_the_summary_counts_the_emails_filled():
    spreadsheet, _, _ = sheet(row(BOTH))
    text = report_text(spreadsheet)
    assert "\n2 email address(es) filled in.\n" in text + "\n"


def test_a_preview_summary_says_would_be_filled_in():
    spreadsheet, _, _ = sheet(row(BOTH))
    assert "2 email address(es) would be filled in." in report_text(spreadsheet, dry_run=True)


def test_the_summary_counts_cleared_addresses_apart_from_filled_ones():
    spreadsheet, _, _ = sheet(
        row(BOTH, case_id="1"),
        row("Provider: Dr Patel", provider="Dr Smith", provider_email="old@example.test", case_id="2"),
    )
    text = report_text(spreadsheet)
    assert "2 email address(es) filled in." in text
    assert "1 old email address(es) cleared." in text


def test_the_summary_says_nothing_about_emails_when_there_were_none():
    spreadsheet, _, _ = sheet(row(BOTH))
    report_text(spreadsheet)
    text = report_text(spreadsheet)                 # nothing left to fill
    assert "email address(es)" not in text


def test_an_unresolved_name_still_counts_as_flagged():
    spreadsheet, _, _ = sheet(row("Provider: Dr S", case_id="7"))
    text = report_text(spreadsheet)
    assert "1 flagged for attention." in text and "Flagged cases: 7" in text


def test_the_counts_are_the_same_numbers_the_log_line_uses():
    report = run_sweep(sheet(row(BOTH))[0])
    counts = summary_counts(report)
    assert (counts.emails_filled, counts.emails_cleared) == (2, 0)
    assert "2 email(s) filled in" in summary_line(report, 1.0)


def test_a_preview_log_line_says_that_would_be_filled_in():
    report = run_sweep(sheet(row(BOTH))[0], dry_run=True)
    assert "2 email(s) that would be filled in" in summary_line(report, 1.0)


def test_the_log_line_has_no_email_part_when_there_were_none():
    spreadsheet, _, _ = sheet(["111", "", "", "Provider: Dr Smith"], headers=PLAIN_HEADERS)
    assert "email" not in summary_line(run_sweep(spreadsheet), 1.0)


def test_the_log_line_never_carries_an_address():
    report = run_sweep(sheet(row(BOTH))[0])
    assert "@" not in summary_line(report, 1.0)


# --- Problems with the Contacts tab ------------------------------------

BAD_ROW = ["Dr. Mina Rao", "no address here", "Provider"]


def test_a_problem_with_the_tab_is_shown_and_says_it_was_noted_on_the_tab():
    spreadsheet, _, _ = sheet(row(BOTH), contacts=CONTACTS + [BAD_ROW])
    text = report_text(spreadsheet)
    assert (
        "Contacts tab needs attention:\n"
        "  - Row 5: 'Dr. Mina Rao' has no valid email address, so it was skipped\n"
        "  (noted on the Contacts tab)"
    ) in text


def test_the_same_problem_again_says_it_was_already_noted():
    spreadsheet, _, _ = sheet(row(BOTH), contacts=CONTACTS + [BAD_ROW])
    report_text(spreadsheet)
    assert "(already noted on the Contacts tab)" in report_text(spreadsheet)


def test_a_preview_says_nothing_was_noted_on_the_tab():
    spreadsheet, _, _ = sheet(row(BOTH), contacts=CONTACTS + [BAD_ROW])
    assert "(dry run: not noted on the Contacts tab)" in report_text(spreadsheet, dry_run=True)


def test_a_fixed_tab_says_its_note_was_cleared():
    spreadsheet, _, contacts_tab = sheet(row(BOTH), contacts=CONTACTS + [BAD_ROW])
    report_text(spreadsheet)
    contacts_tab.rows[-1][1] = "mina.rao@example.test"
    assert "Contacts tab problems are fixed, so its note was cleared." in report_text(spreadsheet)


def test_someone_elses_note_on_the_tab_is_reported_as_not_replaced():
    spreadsheet, _, contacts_tab = sheet(row(BOTH), contacts=CONTACTS + [BAD_ROW])
    contacts_tab.update_note("A1", "a note a person wrote")
    assert "(couldn't note this on the Contacts tab: cell A1 already has someone else's note)" in report_text(spreadsheet)


def test_a_missing_tab_is_reported_when_the_email_columns_are_in_use():
    spreadsheet, _, _ = sheet(row(BOTH), contacts=None)
    text = report_text(spreadsheet)
    assert (
        "Contacts tab needs attention:\n"
        "  - No tab named 'Contacts' was found, so provider and psychometrist emails can't be filled in"
    ) in text


def test_a_missing_tab_is_not_mentioned_when_the_email_columns_have_not_been_added():
    spreadsheet, _, _ = sheet(["111", "", "", "Provider: Dr Smith"], headers=PLAIN_HEADERS, contacts=None)
    assert "Contacts" not in report_text(spreadsheet)


def test_a_tab_with_problems_is_shown_even_before_the_columns_are_added():
    spreadsheet, _, _ = sheet(["111", "", "", "Provider: Dr Smith"], headers=PLAIN_HEADERS, contacts=CONTACTS + [BAD_ROW])
    assert "Contacts tab needs attention:" in report_text(spreadsheet)


def test_the_section_comes_after_the_services_tab_and_before_the_cases():
    spreadsheet, _, _ = sheet(row(BOTH), contacts=CONTACTS + [BAD_ROW])
    text = report_text(spreadsheet)
    assert text.index("Services tab needs attention:") < text.index("Contacts tab needs attention:") < text.index("Case 111:")


def test_a_clean_tab_adds_nothing():
    spreadsheet, _, _ = sheet(row(BOTH))
    assert "Contacts" not in report_text(spreadsheet)


# --- Reports built without the new parts still format ----------------------


def test_a_report_built_before_contacts_existed_still_formats():
    report = SweepReport(results=[], services=ServicesTab(), services_flag="unchanged")
    assert format_report(report) == "No rows had a comment to process."


def test_a_report_with_no_contacts_attributes_at_all_still_formats():
    report = SimpleNamespace(results=[], services=ServicesTab(), services_flag="unchanged")
    assert format_report(report) == "No rows had a comment to process."