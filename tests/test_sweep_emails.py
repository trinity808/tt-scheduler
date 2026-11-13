"""
Tests for filling the email columns during a sweep (apply_comment_updates and
run_sweep), from the Contacts tab.

The addresses are made up (example.test). No real person's name or address belongs
in this file.

Run from the project root:
    python -m pytest tests/test_sweep_emails.py -v
"""

import pytest

from app.apply_comment_updates import apply_comment_updates, run_sweep
from app.contact_lookup import build_contact_lookup
from tests.fakes import FakeSpreadsheet, FakeWorksheet


HEADERS = ["Case ID", "Provider", "Psychometrist", "Provider Email", "Psychometrist Email", "Comments"]
CONTACTS = [
    ["Name", "Email", "Role"],
    ["Dr. Jane Smith", "jane.smith@example.test", "Provider"],
    ["Dr. Raj Singh", "raj.singh@example.test", "Provider"],
    ["John Smith", "john.smith@example.test", "Psychometrist"],
    ["John Doe", "john.doe@example.test", "Psychometrist"],
    ["Jane Doe", "jane.doe@example.test", "Psychometrist"],
]
JANE = "jane.smith@example.test"
JOHN_DOE = "john.doe@example.test"


def lookup():
    return build_contact_lookup(CONTACTS)


def case(comment, provider="", psychometrist="", provider_email="", psychometrist_email="", case_id="111"):
    return [case_id, provider, psychometrist, provider_email, psychometrist_email, comment]


def ongoing(*rows, headers=HEADERS):
    return FakeWorksheet([headers, *rows])


def assert_no_address(results, ws=None):
    """Reports, logs and notes carry names and statuses, never an address."""
    texts = [str(r.get("emails", "")) for r in results] + [f for r in results for f in r["flags"]]
    if ws is not None:
        texts += list(ws.notes.values())
    assert not any("@" in text for text in texts)


def sweep(ws, **kwargs):
    kwargs.setdefault("contacts", lookup())
    return apply_comment_updates(ws, **kwargs)


# --- Filling the columns ------------------------------------------------


def test_both_emails_are_filled_from_the_names_in_the_comment():
    ws = ongoing(case("Provider: Dr Smith\nPsychometrist: John Doe"))
    sweep(ws)
    assert ws.value(2, "Provider") == "Dr Smith" and ws.value(2, "Psychometrist") == "John Doe"
    assert ws.value(2, "Provider Email") == JANE
    assert ws.value(2, "Psychometrist Email") == JOHN_DOE


def test_the_outcome_names_the_contact_and_never_holds_an_address():
    ws = ongoing(case("Provider: Dr Smith\nPsychometrist: John Doe"))
    [result] = sweep(ws)
    assert result["emails"] == {
        "Provider Email": {"status": "set", "contact": "Dr. Jane Smith"},
        "Psychometrist Email": {"status": "set", "contact": "John Doe"},
    }
    assert "@" not in str(result["emails"]) and not any("@" in f for f in result["flags"])


def test_running_it_again_changes_nothing():
    ws = ongoing(case("Provider: Dr Smith\nPsychometrist: John Doe"))
    sweep(ws)
    writes = list(ws.writes)
    [result] = sweep(ws)
    assert ws.writes == writes
    assert {h: o["status"] for h, o in result["emails"].items()} == {
        "Provider Email": "unchanged", "Psychometrist Email": "unchanged",
    }
    assert result["emails"]["Provider Email"]["contact"] == "Dr. Jane Smith"
    assert_no_address([result])


def test_a_preview_writes_nothing_but_says_what_it_would_do():
    ws = ongoing(case("Provider: Dr Smith"))
    [result] = sweep(ws, dry_run=True)
    assert ws.writes == [] and ws.value(2, "Provider Email") == ""
    assert result["emails"]["Provider Email"]["status"] == "set"
    assert_no_address([result])


def test_a_corrected_address_in_the_tab_reaches_the_row_on_the_next_sweep():
    ws = ongoing(case("Provider: Dr Smith"))
    sweep(ws)
    corrected = build_contact_lookup([CONTACTS[0], ["Dr. Jane Smith", "new.address@example.test", "Provider"]])
    [result] = apply_comment_updates(ws, contacts=corrected)
    assert ws.value(2, "Provider Email") == "new.address@example.test"
    assert result["emails"]["Provider Email"]["status"] == "updated"
    assert_no_address([result])


def test_a_wrong_address_typed_into_the_cell_is_replaced_by_the_tabs():
    ws = ongoing(case("Provider: Dr Smith", provider="Dr Smith", provider_email="typo@example.test"))
    [result] = sweep(ws)
    assert ws.value(2, "Provider Email") == JANE
    assert result["emails"]["Provider Email"]["status"] == "updated"
    assert_no_address([result])


def test_only_the_person_the_comment_names_is_looked_up():
    ws = ongoing(case("Provider: Dr Smith"))
    [result] = sweep(ws)
    assert set(result["emails"]) == {"Provider Email"}
    assert ws.value(2, "Psychometrist Email") == ""


def test_a_comment_that_names_no_one_has_no_emails_key_and_touches_no_email_cell():
    ws = ongoing(case("Add: wms"))
    [result] = sweep(ws)
    assert "emails" not in result and ws.writes == []


def test_the_provider_is_looked_up_among_providers_and_the_student_among_students():
    ws = ongoing(case("Provider: Dr Smith\nPsychometrist: Smith"))
    sweep(ws)
    assert ws.value(2, "Provider Email") == JANE
    assert ws.value(2, "Psychometrist Email") == "john.smith@example.test"


# --- A name that can't be resolved ------------------------------------


def test_an_unknown_name_is_flagged_and_nothing_is_filled():
    ws = ongoing(case("Provider: Dr Patel"))
    [result] = sweep(ws)
    assert ws.value(2, "Provider Email") == ""
    assert result["flags"] == [
        "Provider Email  (not filled in: 'Dr Patel' isn't in the Contacts tab. Add them there, or check the spelling)"
    ]
    assert result["emails"]["Provider Email"]["status"] == "unknown"


def test_the_flag_reaches_the_comments_note_so_oa_sees_it():
    ws = ongoing(case("Provider: Dr Patel"))
    sweep(ws)
    assert "Provider Email  (not filled in:" in ws.get_note("F2")


def test_an_initial_alone_is_flagged_with_what_to_write_instead():
    ws = ongoing(case("Provider: Dr S"))
    [result] = sweep(ws)
    assert result["flags"] == [
        "Provider Email  (not filled in: 'Dr S' is too short to match. Write the name in full, for example 'Dr Smith')"
    ]


def test_a_name_that_fits_two_people_is_flagged_with_both_and_never_guessed():
    ws = ongoing(case("Psychometrist: Doe"))
    [result] = sweep(ws)
    assert ws.value(2, "Psychometrist Email") == ""
    assert result["flags"] == [
        "Psychometrist Email  (not filled in: 'Doe' could be John Doe, Jane Doe. "
        "Write more of the name, for example 'John Doe')"
    ]


def test_the_example_for_a_provider_carries_the_title():
    contacts = build_contact_lookup([CONTACTS[0], ["Jane Smith", "a@example.test", "Provider"],
                                     ["John Smith", "b@example.test", "Provider"]])
    ws = ongoing(case("Provider: Dr Smith"))
    [result] = apply_comment_updates(ws, contacts=contacts)
    assert "for example 'Dr Jane Smith'" in result["flags"][0]


def test_fixing_the_comment_clears_the_flag_and_fills_the_cell():
    ws = ongoing(case("Provider: Dr S"))
    sweep(ws)
    ws.rows[1][HEADERS.index("Comments")] = "Provider: Dr Smith"
    [result] = sweep(ws)
    assert result["flags"] == [] and ws.value(2, "Provider Email") == JANE
    assert ws.get_note("F2") == ""


def test_a_new_provider_who_cannot_be_found_clears_the_old_providers_address():
    # The address would now belong to someone else, and a reminder to the wrong
    # person is worse than none.
    ws = ongoing(case("Provider: Dr Patel", provider="Dr Smith", provider_email=JANE))
    [result] = sweep(ws)
    assert ws.value(2, "Provider Email") == ""
    assert result["emails"]["Provider Email"]["status"] == "cleared"
    assert result["flags"][0].endswith("The old address was cleared)")
    assert_no_address([result], ws)


def test_the_same_provider_whose_name_cannot_be_found_keeps_a_hand_entered_address():
    ws = ongoing(case("Provider: Dr Patel", provider="Dr Patel", provider_email="hand@example.test"))
    [result] = sweep(ws)
    assert ws.value(2, "Provider Email") == "hand@example.test"
    assert result["emails"]["Provider Email"]["status"] == "unknown"


def test_a_preview_clears_nothing_either():
    ws = ongoing(case("Provider: Dr Patel", provider="Dr Smith", provider_email=JANE))
    [result] = sweep(ws, dry_run=True)
    assert ws.value(2, "Provider Email") == JANE
    assert result["emails"]["Provider Email"]["status"] == "cleared"


def test_no_flag_note_or_outcome_ever_contains_an_address():
    ws = ongoing(
        case("Provider: Dr Patel\nPsychometrist: Doe", provider="Dr Smith", provider_email=JANE, case_id="1"),
        case("Provider: Dr S", case_id="2"),
    )
    results = sweep(ws)
    everything = [str(r["emails"]) for r in results] + [f for r in results for f in r["flags"]]
    everything += list(ws.notes.values())
    assert everything and not any("@" in text for text in everything)


# --- When the feature is not switched on ------------------------------


def test_without_the_email_columns_nothing_about_emails_happens():
    plain = ["Case ID", "Provider", "Psychometrist", "Comments"]
    ws = FakeWorksheet([plain, ["111", "", "", "Provider: Dr Smith\nPsychometrist: Doe"]])
    [result] = sweep(ws)
    assert set(result) == {"case_id", "parsed", "fields", "flags", "services"}
    assert result["flags"] == []


def test_with_only_the_provider_column_only_the_provider_is_looked_up():
    headers = ["Case ID", "Provider", "Psychometrist", "Provider Email", "Comments"]
    ws = FakeWorksheet([headers, ["111", "", "", "", "Provider: Dr Smith\nPsychometrist: Doe"]])
    [result] = sweep(ws)
    assert set(result["emails"]) == {"Provider Email"} and result["flags"] == []
    assert ws.value(2, "Provider Email") == JANE


def test_with_the_columns_but_no_contacts_a_name_is_flagged_as_the_tab_being_unusable():
    ws = ongoing(case("Provider: Dr Smith"))
    [result] = apply_comment_updates(ws, contacts=None)
    assert result["flags"] == [
        "Provider Email  (not filled in: the Contacts tab couldn't be used, so emails can't be looked up)"
    ]
    assert result["emails"]["Provider Email"]["status"] == "no_tab"


def test_a_row_with_no_comment_is_still_skipped():
    ws = ongoing(case(""))
    assert sweep(ws) == [] and ws.writes == []


# --- A whole sweep, with the Contacts tab -----------------------------


def sheet(contacts_rows=CONTACTS, *case_rows, with_contacts_tab=True, headers=HEADERS):
    ws = ongoing(*case_rows, headers=headers)
    tabs = {"Ongoing": ws}
    contacts_tab = None
    if with_contacts_tab:
        contacts_tab = FakeWorksheet(contacts_rows)
        tabs["Contacts"] = contacts_tab
    return FakeSpreadsheet(tabs), ws, contacts_tab


def test_a_whole_sweep_fills_the_emails_from_the_contacts_tab():
    spreadsheet, ws, _ = sheet(CONTACTS, case("Provider: Dr Smith\nPsychometrist: John Doe"))
    report = run_sweep(spreadsheet)
    assert ws.value(2, "Provider Email") == JANE and ws.value(2, "Psychometrist Email") == JOHN_DOE
    assert report.contacts.problems == [] and report.contacts_flag == "unchanged"


def test_a_bad_contacts_row_is_noted_on_the_tab_and_the_sweep_carries_on():
    rows = CONTACTS + [["Dr. Mina Rao", "no address here", "Provider"]]
    spreadsheet, ws, contacts_tab = sheet(rows, case("Provider: Dr Smith"))
    report = run_sweep(spreadsheet)
    assert ws.value(2, "Provider Email") == JANE
    assert report.contacts_flag == "flagged"
    assert "Row 7: 'Dr. Mina Rao' has no valid email address" in contacts_tab.get_note("A1")
    assert "@" not in contacts_tab.get_note("A1")


def test_fixing_the_contacts_tab_clears_its_note_on_the_next_sweep():
    rows = CONTACTS + [["Dr. Mina Rao", "no address here", "Provider"]]
    spreadsheet, _, contacts_tab = sheet(rows, case("Provider: Dr Smith"))
    run_sweep(spreadsheet)
    contacts_tab.rows[-1][1] = "mina.rao@example.test"
    report = run_sweep(spreadsheet)
    assert report.contacts_flag == "cleared" and contacts_tab.get_note("A1") == ""


def test_a_missing_contacts_tab_never_stops_the_sweep_and_the_name_is_flagged():
    spreadsheet, ws, _ = sheet(CONTACTS, case("Provider: Dr Smith"), with_contacts_tab=False)
    report = run_sweep(spreadsheet)
    assert report.contacts_flag == "no_tab" and report.contacts.lookup is None
    assert ws.value(2, "Provider") == "Dr Smith"
    assert "the Contacts tab couldn't be used" in report.results[0]["flags"][0]


def test_a_missing_contacts_tab_is_silent_when_the_email_columns_have_not_been_added():
    plain = ["Case ID", "Provider", "Psychometrist", "Comments"]
    spreadsheet, ws, _ = sheet(CONTACTS, ["111", "", "", "Provider: Dr Smith"], with_contacts_tab=False, headers=plain)
    report = run_sweep(spreadsheet)
    assert report.results[0]["flags"] == [] and ws.value(2, "Provider") == "Dr Smith"


def test_a_preview_notes_nothing_on_the_contacts_tab_and_fills_nothing():
    rows = CONTACTS + [["Dr. Mina Rao", "no address here", "Provider"]]
    spreadsheet, ws, contacts_tab = sheet(rows, case("Provider: Dr Smith"))
    report = run_sweep(spreadsheet, dry_run=True)
    assert report.contacts_flag == "dry_run"
    assert contacts_tab.get_note("A1") == "" and ws.value(2, "Provider Email") == ""


def test_the_report_records_that_the_contacts_tab_was_loaded():
    spreadsheet, _, contacts_tab = sheet(CONTACTS, case("Provider: Dr Smith"))
    report = run_sweep(spreadsheet)
    assert report.contacts.worksheet is contacts_tab