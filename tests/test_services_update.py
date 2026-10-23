"""
Tests for app/services_update.py: what a case's Services Requested cell should
say, and whether it is safe to change.

Pure tests: cell text and changes in, an update out. No Google Sheets involved.

Run from the project root:
    python -m pytest tests/test_services_update.py -v
"""

import pytest

from app.comment_parser import ServiceChange
from app.service_lookup import build_lookup
from app.services_cell import ServiceLine, parse_services_cell, render_services_cell
from app.services_update import build_final_services


WISC = "96101-WISC-V"
PSYCH = "90791"
AUTISM = "96130-AUTISM"
WIAT = "96101-WIAT-III"
WAIS = "96101-WAIS-IV"
WPPSI = "96101-WPPSI-IV"
CARS = "96130-CARS-2"
CTONI = "96130-CTONI2"
ORIGINAL = [WISC, PSYCH, AUTISM]

WISC_PDF = "Wechsler Intelligence Scale for Children - Ages 6 to 16 years"
PSYCH_PDF = "Psychiatric Exam"
AUTISM_PDF = "Appropriate testing to evaluate Autism Spectrum Disorders"

LOOKUP = build_lookup([
    ["Code", "Aliases", "Name"],
    [WISC, "WISC", "Wechsler Intelligence Scale for Children"],
    [WAIS, "WAIS", "Wechsler Adult Intelligence Scale"],
    [CARS, "CARS", "Childhood Autism Rating Scale"],
    [WIAT, "WIAT", "Wechsler Individual Achievement Test"],
    [CTONI, "CTONI", "Comprehensive Test of Nonverbal Intelligence"],
    [WPPSI, "WPPSI", "Wechsler Preschool and Primary Scale"],
])

# The cell as the extractor writes it.
PDF_CELL = render_services_cell([
    ServiceLine(WISC, WISC_PDF),
    ServiceLine(PSYCH, PSYCH_PDF),
    ServiceLine(AUTISM, AUTISM_PDF),
])


def add(code):
    return ServiceChange("add", code)


def change(old, new):
    return ServiceChange("change", new, replaces=old)


def run(*changes, original=ORIGINAL, cell=PDF_CELL, lookup=LOOKUP):
    return build_final_services(original, list(changes), cell, lookup)


def shown(update):
    return [(l.code, l.marker, l.changed_from) for l in update.lines]


def cell_after(update):
    """The cell text after applying an update (unchanged keeps what it had)."""
    return update.text


# --- Nothing to do --------------------------------------------------


def test_no_changes_and_a_matching_cell_is_left_alone():
    update = run()
    assert update.status == "unchanged"
    assert update.text is None
    assert update.flags == []
    assert update.original_to_store is None


def test_a_cell_that_differs_only_in_spacing_is_left_alone():
    spaced = PDF_CELL.replace(" - ", "  -   ")
    assert run(cell=spaced).status == "unchanged"


# --- Applying changes to a stored original --------------------------


def test_an_added_test_is_written_at_the_end_with_its_marker_and_name():
    update = run(add(CARS))
    assert update.status == "update"
    assert parse_services_cell(update.text).lines[-1] == ServiceLine(
        CARS, "Childhood Autism Rating Scale", "added"
    )
    assert update.flags == []


def test_the_pdfs_own_lines_keep_their_exact_text():
    update = run(add(CARS))
    parsed = parse_services_cell(update.text)
    assert [l.description for l in parsed.lines[:3]] == [WISC_PDF, PSYCH_PDF, AUTISM_PDF]


def test_a_swap_keeps_its_place_and_says_what_it_replaced():
    update = run(change(WISC, WAIS))
    assert shown(update) == [
        (WAIS, "changed", WISC), (PSYCH, "", ""), (AUTISM, "", ""),
    ]
    assert update.lines[0].description == "Wechsler Adult Intelligence Scale"


def test_the_result_is_written_numbered_with_separators():
    update = run(add(CARS))
    assert update.text.startswith(f"1. {WISC} - {WISC_PDF}\n")
    assert update.text.endswith("4. 96130-CARS-2 - Childhood Autism Rating Scale (added)")


# --- Reruns and appended comments -----------------------------------


def test_running_again_on_its_own_output_changes_nothing():
    first = run(add(CARS), change(WISC, WAIS))
    again = run(add(CARS), change(WISC, WAIS), cell=first.text)
    assert again.status == "unchanged"
    assert again.flags == []


def test_a_comment_that_grows_updates_the_cell_without_flagging_earlier_lines():
    monday = [add(WIAT), change(WISC, WPPSI)]
    first = run(*monday)
    assert first.flags == []

    tuesday = monday + [change(AUTISM, CTONI)]
    second = run(*tuesday, cell=first.text)
    assert second.status == "update"
    assert second.flags == []
    assert [l.code for l in second.lines] == [WPPSI, PSYCH, CTONI, WIAT]

    wednesday = tuesday + [add(CARS)]
    third = run(*wednesday, cell=second.text)
    assert third.status == "update"
    assert third.flags == []
    assert [l.code for l in third.lines] == [WPPSI, PSYCH, CTONI, WIAT, CARS]


# --- Descriptions ---------------------------------------------------


def test_a_description_already_in_the_cell_wins_over_the_services_tab():
    cell = render_services_cell([
        ServiceLine(WISC, WISC_PDF), ServiceLine(PSYCH, PSYCH_PDF), ServiceLine(AUTISM, AUTISM_PDF),
        ServiceLine(CARS, "Wording from an earlier run", "added"),
    ])
    update = run(add(CARS), cell=cell)
    assert update.lines[-1].description == "Wording from an earlier run"
    assert update.status == "unchanged"


def test_a_blank_description_in_the_cell_falls_back_to_the_services_tab():
    cell = render_services_cell([
        ServiceLine(WISC, WISC_PDF), ServiceLine(PSYCH, PSYCH_PDF), ServiceLine(AUTISM, AUTISM_PDF),
        ServiceLine(CARS, "", "added"),
    ])
    update = run(add(CARS), cell=cell)
    assert update.lines[-1].description == "Childhood Autism Rating Scale"
    assert update.status == "update"


def test_a_restored_original_line_uses_the_services_tab_name():
    # WISC was swapped to WAIS earlier, so its line is no longer in the cell.
    cell = run(change(WISC, WAIS)).text
    update = run(change(WISC, WAIS), change(WAIS, WISC), cell=cell)
    assert shown(update)[0] == (WISC, "", "")
    assert update.lines[0].description == "Wechsler Intelligence Scale for Children"


def test_a_code_with_no_name_anywhere_shows_just_the_code():
    update = run(add("96130-NEWTEST"))
    assert update.lines[-1].description == ""
    assert update.text.endswith("4. 96130-NEWTEST (added)")


def test_it_works_with_no_lookup_at_all():
    update = run(add(CARS), lookup=None)
    assert update.text.endswith("4. 96130-CARS-2 (added)")
    assert parse_services_cell(update.text).lines[0].description == WISC_PDF


# --- Changes that don't fit -----------------------------------------


def test_a_change_that_does_not_fit_is_flagged_and_the_rest_apply():
    update = run(change(CTONI, WAIS), add(CARS))
    assert [l.code for l in update.lines] == ORIGINAL + [CARS]
    assert update.flags == [f"Change: {CTONI} to {WAIS}  (skipped: {CTONI} isn't on this case)"]
    assert update.status == "update"


def test_when_every_change_is_skipped_nothing_is_written_but_the_flags_remain():
    update = run(add(AUTISM))
    assert update.status == "unchanged"
    assert update.text is None
    assert update.flags == [f"Add: {AUTISM}  (skipped: {AUTISM} is already on this case)"]


# --- Lines nothing explains: kept and flagged -----------------------


def test_an_added_test_the_comment_no_longer_mentions_is_kept_and_flagged():
    # Monday's comment added CTONI. Tuesday OA rewrote it and only added WIAT.
    cell = run(add(CTONI)).text
    update = run(add(WIAT), cell=cell)
    assert [l.code for l in update.lines] == ORIGINAL + [WIAT, CTONI]
    assert shown(update)[-1] == (CTONI, "added", "")
    assert len(update.flags) == 1
    assert update.flags[0].startswith(f"Add: {CTONI}  (kept:")
    assert "no longer includes it" in update.flags[0]


def test_a_swap_the_comment_no_longer_mentions_stays_swapped_in_place():
    # Monday's comment swapped WISC for WAIS. Tuesday OA rewrote it without that.
    cell = run(change(WISC, WAIS)).text
    update = run(cell=cell)
    assert shown(update) == [
        (WAIS, "changed", WISC), (PSYCH, "", ""), (AUTISM, "", ""),
    ]
    assert update.status == "unchanged"
    assert update.flags[0].startswith(f"Change: {WISC} to {WAIS}  (kept:")


def test_a_line_typed_into_the_cell_by_hand_is_kept_and_flagged():
    cell = PDF_CELL + "\n____________________\n4. 96130-CARS-2 - Childhood Autism Rating Scale"
    update = run(cell=cell)
    assert update.lines[-1].code == CARS
    assert update.flags[0].startswith(f"Add: {CARS}  (kept:")


def test_a_kept_line_keeps_its_own_description():
    cell = PDF_CELL + "\n____________________\n4. 96130-CARS-2 - Typed by hand"
    assert run(cell=cell).lines[-1].description == "Typed by hand"


def test_an_old_swap_that_conflicts_with_the_new_comment_is_dropped_and_flagged():
    # The cell has WAIS in WISC's place, but the comment now swaps WISC for WPPSI.
    cell = run(change(WISC, WAIS)).text
    update = run(change(WISC, WPPSI), cell=cell)
    assert [l.code for l in update.lines] == [WPPSI, PSYCH, AUTISM]
    assert len(update.flags) == 1
    assert update.flags[0].startswith(f"Change: {WISC} to {WAIS}  (dropped:")
    assert f"{WISC} isn't on this case" in update.flags[0]


def test_an_explicit_change_back_is_not_undone_by_what_the_cell_remembers():
    # The cell still shows WAIS from the earlier swap, but the comment swaps
    # WISC to WAIS and then back, so WAIS is accounted for and not kept.
    cell = run(change(WISC, WAIS)).text
    update = run(change(WISC, WAIS), change(WAIS, WISC), cell=cell)
    assert shown(update) == [(WISC, "", ""), (PSYCH, "", ""), (AUTISM, "", "")]
    assert update.flags == []


def test_a_test_the_comment_adds_and_then_swaps_away_is_not_kept():
    cell = run(add(CARS)).text
    update = run(add(CARS), change(CARS, CTONI), cell=cell)
    assert [l.code for l in update.lines] == ORIGINAL + [CTONI]
    assert update.flags == []


def test_a_line_the_comment_explains_is_not_flagged():
    cell = run(add(CARS)).text
    assert run(add(CARS), cell=cell).flags == []


def test_a_line_from_the_pdf_deleted_from_the_cell_is_restored():
    cell = render_services_cell([ServiceLine(WISC, WISC_PDF), ServiceLine(AUTISM, AUTISM_PDF)])
    update = run(add(CARS), cell=cell)
    assert [l.code for l in update.lines] == ORIGINAL + [CARS]
    assert update.flags == []


def test_several_kept_lines_are_flagged_in_the_order_they_are_in_the_cell():
    cell = run(add(CARS), add(CTONI)).text
    update = run(add(WIAT), cell=cell)
    assert [l.code for l in update.lines] == ORIGINAL + [WIAT, CARS, CTONI]
    assert update.flags[0].startswith(f"Add: {CARS}  (kept:")
    assert update.flags[1].startswith(f"Add: {CTONI}  (kept:")


def test_comment_flags_come_before_flags_about_kept_lines():
    cell = run(add(CTONI)).text
    update = run(add(AUTISM), add(WIAT), cell=cell)
    assert update.flags[0].startswith(f"Add: {AUTISM}  (skipped:")
    assert update.flags[1].startswith(f"Add: {CTONI}  (kept:")


# --- Refusing to touch a cell it can't read -------------------------


def test_a_line_that_cannot_be_read_stops_everything():
    cell = PDF_CELL + "\nadd wms"
    update = run(add(CARS), cell=cell)
    assert update.status == "refused"
    assert update.text is None
    assert update.original_to_store is None
    assert len(update.flags) == 1
    assert "can't be read" in update.flags[0]
    assert "add wms" in update.flags[0]


def test_several_unreadable_lines_are_counted():
    cell = PDF_CELL + "\nadd wms\nsomething else"
    assert "(and 1 more)" in run(cell=cell).flags[0]


# --- Nothing stored: adopting the cell ------------------------------


def test_a_clean_cell_is_adopted_as_the_original_and_the_changes_apply():
    update = run(add(CARS), original=None)
    assert update.status == "update"
    assert update.original_to_store == ORIGINAL
    assert shown(update)[-1] == (CARS, "added", "")


def test_the_adopted_original_is_stored_even_when_every_change_is_skipped():
    update = run(add(AUTISM), original=None)
    assert update.status == "unchanged"
    assert update.original_to_store == ORIGINAL
    assert len(update.flags) == 1


def test_adopting_follows_the_cells_order():
    cell = render_services_cell([ServiceLine(AUTISM, AUTISM_PDF), ServiceLine(WISC, WISC_PDF)])
    assert run(original=None, cell=cell).original_to_store == [AUTISM, WISC]


def test_a_cell_that_already_shows_changes_is_not_adopted():
    cell = run(add(CARS)).text
    update = run(add(WIAT), original=None, cell=cell)
    assert update.status == "refused"
    assert update.original_to_store is None
    assert update.text is None
    assert "can't be updated safely" in update.flags[0]


def test_a_cell_with_a_swap_marker_is_not_adopted_either():
    cell = run(change(WISC, WAIS)).text
    assert run(original=None, cell=cell).status == "refused"


def test_an_unreadable_cell_is_not_adopted():
    update = run(original=None, cell=PDF_CELL + "\nadd wms")
    assert update.status == "refused"
    assert update.original_to_store is None


def test_an_empty_cell_with_nothing_stored_cannot_take_changes():
    update = run(add(CARS), original=None, cell="")
    assert update.status == "refused"
    assert "no tests recorded" in update.flags[0]


def test_an_empty_cell_with_nothing_stored_and_nothing_to_do_is_fine():
    update = run(original=None, cell="")
    assert update.status == "unchanged"
    assert update.flags == []


def test_a_stored_empty_original_is_still_a_stored_original():
    update = run(add(CARS), original=[], cell="")
    assert update.status == "update"
    assert update.original_to_store is None
    assert update.text == f"1. {CARS} - Childhood Autism Rating Scale (added)"


# --- Inputs ---------------------------------------------------------


def test_the_inputs_are_not_modified():
    original = list(ORIGINAL)
    changes = [add(CARS), change(WISC, WAIS)]
    build_final_services(original, changes, PDF_CELL, LOOKUP)
    assert original == ORIGINAL
    assert changes == [add(CARS), change(WISC, WAIS)]