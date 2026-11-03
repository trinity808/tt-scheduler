"""
Tests for applying test changes to the Services Requested cell, in
app/apply_comment_updates.py.

What a case's cell should become is decided in services_update.py and has its
own tests. These check the wiring: when a case is looked at, where the result
is written, what is saved and in what order, how problems reach the Comments
cell, and that nothing happens without a metadata client.

An in-memory worksheet and metadata client stand in for Google Sheets, as in
the other sweep tests.

Run from the project root:
    python -m pytest tests/test_apply_services.py -v
"""

from app.apply_comment_updates import (
    CLEAR_COLOR,
    COMMENT_ISSUE_COLOR,
    apply_comment_updates,
    run_sweep,
)
from app.case_metadata import (
    original_tests_for_row,
    read_all_case_tests,
    set_case_tests,
)
from app.service_lookup import build_lookup
from app.services_cell import ServiceLine, parse_services_cell, render_services_cell
from tests.fake_metadata import FakeMetadataClient
from tests.fakes import FakeSpreadsheet, FakeWorksheet


WISC = "96101-WISC-V"
PSYCH = "90791"
AUTISM = "96130-AUTISM"
WIAT = "96101-WIAT-III"
WAIS = "96101-WAIS-IV"
WPPSI = "96101-WPPSI-IV"
CARS = "96130-CARS-2"
CTONI = "96130-CTONI2"
ORIGINAL = [WISC, PSYCH, AUTISM]

LOOKUP_ROWS = [
    ["Code", "Aliases", "Name"],
    [WISC, "WISC", "Wechsler Intelligence Scale for Children"],
    [PSYCH, "Psychiatric Exam", "Psychiatric Exam"],
    [AUTISM, "Autism", "Appropriate testing to evaluate Autism Spectrum Disorders"],
    [WAIS, "WAIS", "Wechsler Adult Intelligence Scale"],
    [CARS, "CARS", "Childhood Autism Rating Scale"],
    [WIAT, "WIAT", "Wechsler Individual Achievement Test"],
    [CTONI, "CTONI", "Comprehensive Test of Nonverbal Intelligence"],
    [WPPSI, "WPPSI", "Wechsler Preschool and Primary Scale"],
]
LOOKUP = build_lookup(LOOKUP_ROWS)

# Services Requested is column D, Price E, Comments F.
HEADERS = ["Case ID", "Provider", "Psychometrist", "Services Requested", "Price", "Comments"]
SERVICES_COL, PRICE_COL = 4, 5
COMMENTS_CELL = "F2"

PDF_CELL = render_services_cell([
    ServiceLine(WISC, "Wechsler Intelligence Scale for Children - Ages 6 to 16 years"),
    ServiceLine(PSYCH, "Psychiatric Exam"),
    ServiceLine(AUTISM, "Appropriate testing to evaluate Autism Spectrum Disorders"),
])


def case_row(case_id, comment="", cell=PDF_CELL, provider="", psychometrist="", price="484.00"):
    return [case_id, provider, psychometrist, cell, price, comment]


def sheet(*rows):
    return FakeWorksheet([HEADERS, *rows])


def client_with(*stored):
    """A metadata client holding (row, case_id, tests) originals."""
    client = FakeMetadataClient()
    for row_number, case_id, tests in stored:
        set_case_tests(client, row_number, case_id, tests)
    client.searches = 0
    return client


def sweep(ws, client, lookup=LOOKUP):
    return apply_comment_updates(ws, lookup, client)


def codes_in(ws, row_number=2):
    return parse_services_cell(ws.value(row_number, "Services Requested")).codes()


def set_comment(ws, row_number, text):
    ws.rows[row_number - 1][HEADERS.index("Comments")] = text


# --- Applying changes -----------------------------------------------


def test_an_added_test_is_written_into_the_cell():
    ws = sheet(case_row("111", "Add: cars"))
    results = sweep(ws, client_with((2, "111", ORIGINAL)))
    assert codes_in(ws) == ORIGINAL + [CARS]
    assert "(added)" in ws.value(2, "Services Requested")
    assert results[0]["services"]["status"] == "updated"
    assert COMMENTS_CELL not in ws.notes


def test_a_swap_is_written_in_place_with_its_marker():
    ws = sheet(case_row("111", "Change: wisc to wais"))
    sweep(ws, client_with((2, "111", ORIGINAL)))
    assert codes_in(ws) == [WAIS, PSYCH, AUTISM]
    assert f"(changed from {WISC})" in ws.value(2, "Services Requested")


def test_price_is_never_written():
    ws = sheet(case_row("111", "Provider: Dr. Doe\nAdd: cars\nChange: wisc to wais"))
    sweep(ws, client_with((2, "111", ORIGINAL)))
    assert ws.value(2, "Price") == "484.00"
    assert all(col != PRICE_COL for _, col, _ in ws.writes)


def test_provider_and_tests_are_applied_together():
    ws = sheet(case_row("111", "Provider: Dr. Doe\nAdd: cars"))
    sweep(ws, client_with((2, "111", ORIGINAL)))
    assert ws.value(2, "Provider") == "Dr. Doe"
    assert CARS in codes_in(ws)


# --- Reruns and appended comments -----------------------------------


def test_running_again_writes_nothing():
    ws = sheet(case_row("111", "Add: cars\nChange: wisc to wais"))
    client = client_with((2, "111", ORIGINAL))
    sweep(ws, client)
    writes_after_first = len(ws.writes)

    second = sweep(ws, client)

    assert len(ws.writes) == writes_after_first
    assert second[0]["services"]["status"] == "unchanged"
    assert COMMENTS_CELL not in ws.notes


def test_an_appended_comment_grows_the_cell_and_flags_nothing():
    ws = sheet(case_row("111", "Add: wiat\nChange: wisc to wppsi"))
    client = client_with((2, "111", ORIGINAL))

    sweep(ws, client)
    assert codes_in(ws) == [WPPSI, PSYCH, AUTISM, WIAT]
    assert COMMENTS_CELL not in ws.notes

    set_comment(ws, 2, "Add: wiat\nChange: wisc to wppsi\nChange: autism to ctoni")
    sweep(ws, client)
    assert codes_in(ws) == [WPPSI, PSYCH, CTONI, WIAT]
    assert COMMENTS_CELL not in ws.notes

    set_comment(ws, 2, "Add: wiat\nChange: wisc to wppsi\nChange: autism to ctoni\nAdd: cars")
    sweep(ws, client)
    assert codes_in(ws) == [WPPSI, PSYCH, CTONI, WIAT, CARS]
    assert COMMENTS_CELL not in ws.notes


# --- Problems reach the Comments cell -------------------------------


def test_a_change_that_does_not_fit_is_flagged_and_the_rest_apply():
    ws = sheet(case_row("111", "Add: cars\nChange: ctoni to wais"))
    sweep(ws, client_with((2, "111", ORIGINAL)))
    assert codes_in(ws) == ORIGINAL + [CARS]
    assert ws.formats[COMMENTS_CELL] == {"backgroundColor": COMMENT_ISSUE_COLOR}
    assert f"Change: {CTONI} to {WAIS}  (skipped: {CTONI} isn't on this case)" in ws.notes[COMMENTS_CELL]


def test_parser_problems_and_test_problems_share_one_note_parser_first():
    ws = sheet(case_row("111", "oops\nChange: ctoni to wais"))
    sweep(ws, client_with((2, "111", ORIGINAL)))
    note = ws.notes[COMMENTS_CELL]
    assert note.startswith("Comment needs attention:\n")
    assert note.index("oops") < note.index("Change:")


def test_the_flag_clears_once_the_comment_is_fixed():
    ws = sheet(case_row("111", "Change: ctoni to wais"))
    client = client_with((2, "111", ORIGINAL))
    sweep(ws, client)
    assert COMMENTS_CELL in ws.notes

    set_comment(ws, 2, "Add: cars")
    sweep(ws, client)
    assert COMMENTS_CELL not in ws.notes
    assert ws.formats[COMMENTS_CELL] == {"backgroundColor": CLEAR_COLOR}


# --- Without what it needs ------------------------------------------


def test_without_a_metadata_client_test_changes_are_only_reported():
    ws = sheet(case_row("111", "Provider: Dr. Doe\nAdd: cars"))
    results = apply_comment_updates(ws, LOOKUP)
    assert ws.value(2, "Services Requested") == PDF_CELL
    assert ws.value(2, "Provider") == "Dr. Doe"
    assert results[0]["services"]["status"] == "not_applied"
    assert [c.code for c in results[0]["parsed"].service_changes] == [CARS]


def test_without_a_metadata_client_an_empty_comment_is_still_skipped():
    marked = render_services_cell([ServiceLine(WISC), ServiceLine(CARS, "", "added")])
    ws = sheet(case_row("111", "", cell=marked))
    assert apply_comment_updates(ws, LOOKUP) == []


def test_a_sheet_with_no_services_column_still_gets_its_other_updates():
    headers = ["Case ID", "Provider", "Psychometrist", "Comments"]
    ws = FakeWorksheet([headers, ["111", "", "", "Provider: Dr. Doe\nAdd: cars"]])
    results = sweep(ws, FakeMetadataClient())
    assert ws.value(2, "Provider") == "Dr. Doe"
    assert results[0]["services"]["status"] == "no_column"


def test_a_comment_with_no_test_changes_leaves_the_cell_alone():
    hand_edited = PDF_CELL + "\n____________________\n4. 96130-CARS-2 - typed by hand"
    ws = sheet(case_row("111", "Provider: Dr. Doe", cell=hand_edited))
    results = sweep(ws, client_with((2, "111", ORIGINAL)))
    assert ws.value(2, "Services Requested") == hand_edited
    assert results[0]["services"]["status"] == "none"
    assert COMMENTS_CELL not in ws.notes


def test_rows_without_a_case_id_are_skipped_even_with_changes_in_the_comment():
    ws = sheet(["", "", "", "", "", "Add: cars"])
    assert sweep(ws, client_with()) == []


# --- The stored original --------------------------------------------


def test_each_row_uses_its_own_stored_original():
    ws = sheet(
        case_row("111", "Add: cars"),
        case_row("222", "Add: cars", cell=render_services_cell([ServiceLine(WISC)])),
    )
    client = client_with((2, "111", ORIGINAL), (3, "222", [WISC]))
    sweep(ws, client)
    assert codes_in(ws, 2) == ORIGINAL + [CARS]
    assert codes_in(ws, 3) == [WISC, CARS]


def test_the_stored_originals_are_read_once_per_sweep():
    ws = sheet(case_row("111", "Add: cars"), case_row("222", "Add: wiat"))
    client = client_with((2, "111", ORIGINAL), (3, "222", ORIGINAL))
    sweep(ws, client)
    assert client.searches == 1


def test_a_stored_original_that_cannot_be_trusted_stops_that_case_only():
    ws = sheet(case_row("111", "Provider: Dr. Doe\nAdd: cars"))
    client = client_with((2, "SOME-OTHER-CASE", ORIGINAL))
    results = sweep(ws, client)

    _, problem = original_tests_for_row(read_all_case_tests(client), 2, "111")
    assert problem
    assert ws.value(2, "Services Requested") == PDF_CELL
    assert ws.value(2, "Provider") == "Dr. Doe"
    assert results[0]["services"]["status"] == "unusable_original"
    assert problem in ws.notes[COMMENTS_CELL]


# --- Nothing stored: adopting the cell ------------------------------


def test_a_clean_cell_is_adopted_as_the_original_and_changes_apply():
    ws = sheet(case_row("111", "Add: cars"))
    client = FakeMetadataClient()
    results = sweep(ws, client)

    assert read_all_case_tests(client).by_row[2].tests == ORIGINAL
    assert read_all_case_tests(client).by_row[2].case_id == "111"
    assert codes_in(ws) == ORIGINAL + [CARS]
    assert results[0]["services"]["status"] == "updated"


def test_the_original_is_saved_before_the_cell_is_written():
    ws = sheet(case_row("111", "Add: cars"))
    client = FakeMetadataClient()
    writes_when_saved = []
    client.on_create = lambda row: writes_when_saved.append(len(ws.writes))

    sweep(ws, client)

    assert writes_when_saved == [0]
    assert len(ws.writes) == 1


def test_if_saving_the_original_fails_the_cell_is_left_alone():
    ws = sheet(case_row("111", "Provider: Dr. Doe\nAdd: cars"))
    client = FakeMetadataClient()
    client.fail_create = True

    results = sweep(ws, client)

    assert ws.value(2, "Services Requested") == PDF_CELL
    assert ws.value(2, "Provider") == "Dr. Doe"
    assert results[0]["services"]["status"] == "store_failed"
    assert "couldn't be saved" in ws.notes[COMMENTS_CELL]


def test_a_cell_that_already_shows_changes_is_not_adopted():
    marked = render_services_cell([ServiceLine(WISC), ServiceLine(CARS, "", "added")])
    ws = sheet(case_row("111", "Add: wiat", cell=marked))
    client = FakeMetadataClient()

    results = sweep(ws, client)

    assert client.entries == []
    assert ws.value(2, "Services Requested") == marked
    assert results[0]["services"]["status"] == "refused"
    assert "can't be updated safely" in ws.notes[COMMENTS_CELL]


def test_the_original_is_still_saved_when_every_change_is_skipped():
    ws = sheet(case_row("111", "Add: autism"))
    client = FakeMetadataClient()
    results = sweep(ws, client)
    assert read_all_case_tests(client).by_row[2].tests == ORIGINAL
    assert ws.value(2, "Services Requested") == PDF_CELL
    assert results[0]["services"]["status"] == "unchanged"
    assert "(skipped:" in ws.notes[COMMENTS_CELL]


def test_a_cell_with_a_line_that_cannot_be_read_is_not_touched():
    ws = sheet(case_row("111", "Add: cars", cell=PDF_CELL + "\nadd wms"))
    client = client_with((2, "111", ORIGINAL))
    results = sweep(ws, client)
    assert ws.value(2, "Services Requested") == PDF_CELL + "\nadd wms"
    assert results[0]["services"]["status"] == "refused"
    assert "can't be read" in ws.notes[COMMENTS_CELL]


# --- A comment that has gone away -----------------------------------


def test_an_empty_comment_with_changes_still_in_the_cell_is_flagged_and_kept():
    applied = PDF_CELL + "\n____________________\n4. 96130-CARS-2 - Childhood Autism Rating Scale (added)"
    ws = sheet(case_row("111", "", cell=applied))
    results = sweep(ws, client_with((2, "111", ORIGINAL)))

    assert len(results) == 1
    assert codes_in(ws) == ORIGINAL + [CARS]
    assert results[0]["services"]["status"] == "unchanged"
    assert ws.formats[COMMENTS_CELL] == {"backgroundColor": COMMENT_ISSUE_COLOR}
    assert f"Add: {CARS}  (kept:" in ws.notes[COMMENTS_CELL]


def test_an_empty_comment_with_a_clean_cell_is_skipped():
    ws = sheet(case_row("111", ""))
    assert sweep(ws, client_with((2, "111", ORIGINAL))) == []
    assert ws.writes == []


# --- run_sweep ------------------------------------------------------


def make_spreadsheet(*rows):
    ongoing = sheet(*rows)
    services = FakeWorksheet(LOOKUP_ROWS)
    return FakeSpreadsheet({"Ongoing": ongoing, "Services": services}), ongoing


def test_run_sweep_applies_test_changes_when_given_a_metadata_client():
    spreadsheet, ongoing = make_spreadsheet(case_row("111", "Add: cars"))
    report = run_sweep(spreadsheet, client_with((2, "111", ORIGINAL)))
    assert codes_in(ongoing) == ORIGINAL + [CARS]
    assert report.results[0]["services"]["status"] == "updated"


def test_run_sweep_without_a_metadata_client_does_not_apply_them():
    spreadsheet, ongoing = make_spreadsheet(case_row("111", "Add: cars"))
    report = run_sweep(spreadsheet)
    assert ongoing.value(2, "Services Requested") == PDF_CELL
    assert report.results[0]["services"]["status"] == "not_applied"


# --- Dry run --------------------------------------------------------


def test_a_dry_run_writes_nothing_at_all():
    ws = sheet(case_row("111", "Provider: Dr. Doe\nAdd: cars\nChange: ctoni to wais"))
    client = client_with((2, "111", ORIGINAL))
    entries_before = [(e.row_number, e.value) for e in client.entries]

    apply_comment_updates(ws, LOOKUP, client, dry_run=True)

    assert ws.writes == []
    assert ws.notes == {}
    assert ws.formats == {}
    assert [(e.row_number, e.value) for e in client.entries] == entries_before


def test_a_dry_run_still_reports_what_would_happen():
    ws = sheet(case_row("111", "Provider: Dr. Doe\nAdd: cars"))
    results = apply_comment_updates(ws, LOOKUP, client_with((2, "111", ORIGINAL)), dry_run=True)

    assert results[0]["fields"]["Provider"] == ("updated", "", "Dr. Doe")
    assert results[0]["services"]["status"] == "updated"
    assert "(added)" in results[0]["services"]["update"].text
    assert results[0]["flags"] == []


def test_a_dry_run_works_out_the_flags_without_writing_them():
    ws = sheet(case_row("111", "Change: ctoni to wais"))
    results = apply_comment_updates(ws, LOOKUP, client_with((2, "111", ORIGINAL)), dry_run=True)

    assert results[0]["flags"] == [
        f"Change: {CTONI} to {WAIS}  (skipped: {CTONI} isn't on this case)"
    ]
    assert ws.notes == {}
    assert ws.formats == {}


def test_a_dry_run_does_not_save_an_adopted_original():
    ws = sheet(case_row("111", "Add: cars"))
    client = FakeMetadataClient()

    results = apply_comment_updates(ws, LOOKUP, client, dry_run=True)

    assert client.entries == []
    assert results[0]["services"]["update"].original_to_store == ORIGINAL
    assert results[0]["services"]["status"] == "updated"


def test_a_dry_run_predicts_exactly_what_a_live_run_then_does():
    comment = "Provider: Dr. Doe\nAdd: cars\nChange: wisc to wais\nChange: ctoni to wiat"
    dry_ws, live_ws = sheet(case_row("111", comment)), sheet(case_row("111", comment))

    dry = apply_comment_updates(dry_ws, LOOKUP, client_with((2, "111", ORIGINAL)), dry_run=True)
    live = apply_comment_updates(live_ws, LOOKUP, client_with((2, "111", ORIGINAL)))

    assert dry[0]["services"]["status"] == live[0]["services"]["status"] == "updated"
    assert dry[0]["flags"] == live[0]["flags"]
    assert dry[0]["fields"] == live[0]["fields"]
    assert dry[0]["services"]["update"].text == live_ws.value(2, "Services Requested")


def test_run_sweep_dry_run_notes_nothing_on_the_services_tab():
    bad_rows = LOOKUP_ROWS + [["Beery VMI", "", ""]]
    ongoing = sheet(case_row("111", "Add: cars"))
    services = FakeWorksheet(bad_rows)
    spreadsheet = FakeSpreadsheet({"Ongoing": ongoing, "Services": services})

    report = run_sweep(spreadsheet, client_with((2, "111", ORIGINAL)), dry_run=True)

    assert report.dry_run is True
    assert report.services_flag == "dry_run"
    assert report.services.problems
    assert services.notes == {}
    assert ongoing.writes == []


def test_run_sweep_dry_run_with_no_services_tab_says_so():
    ongoing = sheet(case_row("111", "Add: cars"))
    report = run_sweep(FakeSpreadsheet({"Ongoing": ongoing}), None, dry_run=True)
    assert report.services_flag == "no_tab"
    assert report.dry_run is True


def test_a_normal_run_is_not_marked_as_a_dry_run():
    spreadsheet, _ = make_spreadsheet(case_row("111", "Add: cars"))
    assert run_sweep(spreadsheet, client_with((2, "111", ORIGINAL))).dry_run is False


def test_each_result_carries_what_the_cell_was_before():
    ws = sheet(case_row("111", "Add: cars"))
    results = sweep(ws, client_with((2, "111", ORIGINAL)))
    assert [line.code for line in results[0]["services"]["before"]] == ORIGINAL


def test_a_hand_typed_line_is_explained_in_the_comments_note():
    hand = PDF_CELL + "\n____________________\n4. 96130-CARS-2 - typed by hand"
    ws = sheet(case_row("111", "Add: wiat", cell=hand))
    sweep(ws, client_with((2, "111", ORIGINAL)))
    note = ws.notes[COMMENTS_CELL]
    assert "isn't in the PDF's list or this comment" in note
    assert "earlier change" not in note