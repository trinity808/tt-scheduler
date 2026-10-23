"""
Tests for app/service_changes.py: working out a case's tests from its
original list and the comment's changes.

Pure tests: codes in, lines out. No Google Sheets involved.

Run from the project root:
    python -m pytest tests/test_service_changes.py -v
"""

import pytest

from app.comment_parser import ServiceChange
from app.service_changes import SkippedChange, compute_services


WISC = "96101-WISC-V"
PSYCH = "90791"
AUTISM = "96130-AUTISM"
WIAT = "96101-WIAT-III"
WAIS = "96101-WAIS-IV"
WPPSI = "96101-WPPSI-IV"
CARS = "96130-CARS-2"
CTONI = "96130-CTONI2"
ORIGINAL = [WISC, PSYCH, AUTISM]


def add(code):
    return ServiceChange("add", code)


def change(old, new):
    return ServiceChange("change", new, replaces=old)


def result(original, *changes):
    return compute_services(original, list(changes))


def shown(computed):
    """Each line as (code, marker, changed_from), for easy comparison."""
    return [(line.code, line.marker, line.changed_from) for line in computed.lines]


# --- The basics -----------------------------------------------------


def test_no_changes_returns_the_original_with_no_markers():
    computed = result(ORIGINAL)
    assert shown(computed) == [(WISC, "", ""), (PSYCH, "", ""), (AUTISM, "", "")]
    assert computed.skipped == []


def test_an_added_test_goes_at_the_end_marked_added():
    assert shown(result(ORIGINAL, add(CARS))) == [
        (WISC, "", ""), (PSYCH, "", ""), (AUTISM, "", ""), (CARS, "added", ""),
    ]


def test_several_adds_keep_the_order_written():
    assert result(ORIGINAL, add(WIAT), add(CARS)).codes() == [WISC, PSYCH, AUTISM, WIAT, CARS]


def test_a_changed_test_keeps_its_position_and_says_what_it_replaced():
    assert shown(result(ORIGINAL, change(WISC, WAIS))) == [
        (WAIS, "changed", WISC), (PSYCH, "", ""), (AUTISM, "", ""),
    ]


def test_a_change_in_the_middle_keeps_the_others_where_they_were():
    assert result(ORIGINAL, change(PSYCH, CTONI)).codes() == [WISC, CTONI, AUTISM]


def test_descriptions_are_left_empty_for_a_later_step():
    computed = result(ORIGINAL, add(CARS), change(WISC, WAIS))
    assert all(line.description == "" for line in computed.lines)


def test_a_case_with_no_original_tests():
    assert shown(result([], add(CARS))) == [(CARS, "added", "")]
    assert result([]).lines == []


# --- Rerunning and appending never re-flags earlier changes ----------


def test_the_same_inputs_always_give_the_same_result():
    changes = [add(WIAT), change(WISC, WPPSI)]
    assert shown(compute_services(ORIGINAL, changes)) == shown(compute_services(ORIGINAL, changes))


def test_a_comment_that_grows_replays_the_earlier_changes_without_flagging_them():
    # Monday: OA adds WIAT and swaps WISC for WPPSI.
    monday = [add(WIAT), change(WISC, WPPSI)]
    first = compute_services(ORIGINAL, monday)
    assert shown(first) == [
        (WPPSI, "changed", WISC), (PSYCH, "", ""), (AUTISM, "", ""), (WIAT, "added", ""),
    ]
    assert first.skipped == []

    # Tuesday: she appends a swap of AUTISM for CTONI. The Monday lines are
    # still in the comment, and replaying them from the original is fine.
    tuesday = monday + [change(AUTISM, CTONI)]
    second = compute_services(ORIGINAL, tuesday)
    assert shown(second) == [
        (WPPSI, "changed", WISC), (PSYCH, "", ""), (CTONI, "changed", AUTISM), (WIAT, "added", ""),
    ]
    assert second.skipped == []

    # Wednesday: the psychometrist adds another test, and OA appends it.
    wednesday = tuesday + [add(CARS)]
    third = compute_services(ORIGINAL, wednesday)
    assert third.codes() == [WPPSI, PSYCH, CTONI, WIAT, CARS]
    assert third.skipped == []


# --- Markers show the net effect against the original ---------------


def test_a_swap_of_a_swap_still_points_at_the_original():
    computed = result(ORIGINAL, change(WISC, WAIS), change(WAIS, WPPSI))
    assert shown(computed)[0] == (WPPSI, "changed", WISC)
    assert computed.skipped == []


def test_a_swap_of_an_added_test_is_still_just_added():
    computed = result(ORIGINAL, add(CARS), change(CARS, CTONI))
    assert shown(computed)[-1] == (CTONI, "added", "")
    assert len(computed.lines) == 4


def test_changing_back_to_the_original_leaves_no_marker():
    computed = result(ORIGINAL, change(WISC, WAIS), change(WAIS, WISC))
    assert shown(computed) == [(WISC, "", ""), (PSYCH, "", ""), (AUTISM, "", "")]


def test_adding_back_an_original_test_that_was_swapped_away_has_no_marker():
    computed = result(ORIGINAL, change(WISC, WAIS), add(WISC))
    assert shown(computed) == [
        (WAIS, "changed", WISC), (PSYCH, "", ""), (AUTISM, "", ""), (WISC, "", ""),
    ]
    assert computed.skipped == []


# --- Changes that don't fit are skipped and explained ---------------


def test_adding_a_test_that_is_already_there_is_skipped():
    computed = result(ORIGINAL, add(AUTISM))
    assert computed.codes() == ORIGINAL
    assert len(computed.skipped) == 1
    assert computed.skipped[0].reason == f"{AUTISM} is already on this case"


def test_adding_the_same_test_twice_skips_the_second():
    computed = result(ORIGINAL, add(CARS), add(CARS))
    assert computed.codes() == ORIGINAL + [CARS]
    assert [s.change for s in computed.skipped] == [add(CARS)]


def test_changing_a_test_the_case_does_not_have_is_skipped():
    computed = result(ORIGINAL, change(CTONI, WAIS))
    assert computed.codes() == ORIGINAL
    assert computed.skipped[0].reason == f"{CTONI} isn't on this case"


def test_changing_to_a_test_that_is_already_there_is_skipped():
    computed = result(ORIGINAL, change(WISC, AUTISM))
    assert computed.codes() == ORIGINAL
    assert computed.skipped[0].reason == f"{AUTISM} is already on this case"


def test_a_change_with_both_problems_names_both():
    computed = result(ORIGINAL, change(CTONI, AUTISM))
    assert computed.skipped[0].reason == (
        f"{CTONI} isn't on this case; {AUTISM} is already on this case"
    )


def test_a_skipped_change_does_not_stop_the_others():
    computed = result(ORIGINAL, change(CTONI, WAIS), add(CARS), change(WISC, WPPSI))
    assert computed.codes() == [WPPSI, PSYCH, AUTISM, CARS]
    assert len(computed.skipped) == 1


def test_skipped_changes_come_back_in_comment_order():
    computed = result(ORIGINAL, add(WISC), change(CTONI, WAIS), add(PSYCH))
    assert [s.change for s in computed.skipped] == [add(WISC), change(CTONI, WAIS), add(PSYCH)]


def test_order_matters_and_follows_the_comment():
    # Adding a test and then changing it works. The other way round, the
    # change refers to something that isn't there yet.
    assert result(ORIGINAL, add(CARS), change(CARS, CTONI)).skipped == []
    assert len(result(ORIGINAL, change(CARS, CTONI), add(CARS)).skipped) == 1


def test_a_swapped_away_test_cannot_be_changed_again():
    computed = result(ORIGINAL, change(WISC, WAIS), change(WISC, WPPSI))
    assert computed.codes()[0] == WAIS
    assert computed.skipped[0].reason == f"{WISC} isn't on this case"


# --- The skipped message --------------------------------------------


def test_skipped_message_for_an_add():
    skipped = SkippedChange(add(CARS), f"{CARS} is already on this case")
    assert skipped.message == f"Add: {CARS}  (skipped: {CARS} is already on this case)"


def test_skipped_message_for_a_change():
    skipped = SkippedChange(change(CTONI, WAIS), f"{CTONI} isn't on this case")
    assert skipped.message == f"Change: {CTONI} to {WAIS}  (skipped: {CTONI} isn't on this case)"


# --- Inputs and errors ----------------------------------------------


def test_the_inputs_are_not_modified():
    original = list(ORIGINAL)
    changes = [add(CARS), change(WISC, WAIS)]
    compute_services(original, changes)
    assert original == ORIGINAL
    assert changes == [add(CARS), change(WISC, WAIS)]


def test_an_unknown_action_is_an_error():
    with pytest.raises(ValueError, match="unknown change action"):
        result(ORIGINAL, ServiceChange("remove", WISC))


def test_a_change_with_no_test_to_replace_is_an_error():
    with pytest.raises(ValueError, match="needs the test it replaces"):
        result(ORIGINAL, ServiceChange("change", WAIS))