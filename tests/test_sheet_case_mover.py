"""
Tests for app/sheet_case_mover.py: building a Finished row, where it
lands in the Finished tab, and what happens to the Ongoing row.

Uses the shared in-memory FakeWorksheet from tests/fakes.py. That fake
deliberately has no append_row(), so any code that goes back to using
it fails here.

What a fake can't catch: real Sheets behavior beyond what's imitated.
The append_row() column shift came from how Sheets detects tables,
which no fake reproduces. That's what the occasional live check is for.

Run from the project root:
    python -m pytest tests/test_sheet_case_mover.py -v
"""

import logging

import pytest

from app.sheet_case_mover import (
    build_finished_row,
    find_rows_in_finished,
    move_case_to_finished,
)
from tests.fakes import FakeWorksheet


ONGOING_HEADERS = [
    "Case ID", "Patient Name", "Phone", "Psychometrist", "Provider", "Price", "Comments",
]
FINISHED_HEADERS = [
    "Case ID", "Patient Name", "Phone", "Psychometrist", "Provider", "Price",
    "Payment Status", "Date Completed",
]


def ongoing_row(case_id, name="Test Patient", provider="Dr. Shelton",
                psychometrist="J. Smith", price="484.00", comments="Provider: Dr. Shelton"):
    return [case_id, name, "(602) 555-0100", psychometrist, provider, price, comments]


def finished_row(case_id):
    return [case_id, "Earlier Patient", "", "", "", "484.00", "Paid", "5/1/2026"]


def blank_finished_row():
    return [""] * len(FINISHED_HEADERS)


def stray_note_row(note):
    """A row with something in it but no Case ID."""
    row = [""] * len(FINISHED_HEADERS)
    row[FINISHED_HEADERS.index("Payment Status")] = note
    return row


def make_sheets(ongoing_rows, finished_rows=(), **finished_kwargs):
    ongoing = FakeWorksheet([ONGOING_HEADERS, *ongoing_rows])
    finished = FakeWorksheet([FINISHED_HEADERS, *finished_rows], **finished_kwargs)
    return ongoing, finished


# --- Building the Finished row --------------------------------------


def test_build_row_carries_shared_columns_and_drops_ongoing_only_ones():
    row = build_finished_row(
        dict(zip(ONGOING_HEADERS, ongoing_row("111"))), FINISHED_HEADERS
    )
    built = dict(zip(FINISHED_HEADERS, row))
    assert built["Case ID"] == "111"
    assert built["Provider"] == "Dr. Shelton"
    assert built["Price"] == "484.00"
    assert "Comments" not in built


def test_build_row_fills_finished_only_columns_from_extra_fields():
    row = build_finished_row(
        dict(zip(ONGOING_HEADERS, ongoing_row("111"))),
        FINISHED_HEADERS,
        {"Payment Status": "Pending", "Date Completed": "10/6/26"},
    )
    built = dict(zip(FINISHED_HEADERS, row))
    assert built["Payment Status"] == "Pending"
    assert built["Date Completed"] == "10/6/26"


def test_build_row_leaves_unfilled_finished_columns_blank():
    row = build_finished_row(
        dict(zip(ONGOING_HEADERS, ongoing_row("111"))), FINISHED_HEADERS
    )
    built = dict(zip(FINISHED_HEADERS, row))
    assert built["Payment Status"] == ""
    assert built["Date Completed"] == ""


# --- Where the case lands in Finished -------------------------------


def test_first_case_goes_into_row_2():
    ongoing, finished = make_sheets([ongoing_row("111")])
    assert move_case_to_finished(ongoing, finished, "111") == 2
    assert finished.value(2, "Case ID") == "111"


def test_new_case_goes_after_the_last_case():
    ongoing, finished = make_sheets(
        [ongoing_row("333")], [finished_row("111"), finished_row("222")]
    )
    assert move_case_to_finished(ongoing, finished, "333") == 4
    assert finished.value(4, "Case ID") == "333"


def test_existing_finished_rows_are_untouched():
    ongoing, finished = make_sheets([ongoing_row("333")], [finished_row("111")])
    move_case_to_finished(ongoing, finished, "333")
    assert finished.value(2, "Case ID") == "111"
    assert finished.value(2, "Payment Status") == "Paid"


def test_gap_from_a_cleared_row_is_not_filled():
    ongoing, finished = make_sheets(
        [ongoing_row("444")],
        [finished_row("111"), blank_finished_row(), finished_row("333")],
    )
    assert move_case_to_finished(ongoing, finished, "444") == 5
    assert finished.value(3, "Case ID") == ""


def test_case_after_a_gap_starts_at_column_a():
    # Regression test for the append_row() bug: a blank row in the
    # data once shifted a new case so it started at the wrong column.
    ongoing, finished = make_sheets(
        [ongoing_row("444", name="New Patient")],
        [finished_row("111"), blank_finished_row(), finished_row("333")],
    )
    move_case_to_finished(ongoing, finished, "444")
    assert finished.rows[4][0] == "444"
    assert finished.rows[4][1] == "New Patient"


def test_stray_note_below_the_cases_is_not_overwritten():
    ongoing, finished = make_sheets(
        [ongoing_row("222")],
        [finished_row("111"), stray_note_row("call about payment")],
    )
    assert move_case_to_finished(ongoing, finished, "222") == 4
    assert finished.value(3, "Payment Status") == "call about payment"
    assert finished.value(4, "Case ID") == "222"


def test_sheet_grows_when_out_of_rows():
    ongoing, finished = make_sheets(
        [ongoing_row("333")], [finished_row("111"), finished_row("222")], row_count=3
    )
    assert move_case_to_finished(ongoing, finished, "333") == 4
    assert finished.rows_added == 1
    assert finished.value(4, "Case ID") == "333"


def test_values_follow_the_finished_tabs_own_column_order():
    shuffled = ["Date Completed", "Case ID", "Payment Status", "Provider",
                "Price", "Phone", "Patient Name", "Psychometrist"]
    ongoing = FakeWorksheet([ONGOING_HEADERS, ongoing_row("111")])
    finished = FakeWorksheet([shuffled])
    move_case_to_finished(
        ongoing, finished, "111", extra_fields={"Date Completed": "10/6/26"}
    )
    assert finished.value(2, "Case ID") == "111"
    assert finished.value(2, "Date Completed") == "10/6/26"
    assert finished.value(2, "Provider") == "Dr. Shelton"


def test_extra_fields_land_in_finished():
    ongoing, finished = make_sheets([ongoing_row("111")])
    move_case_to_finished(
        ongoing, finished, "111",
        extra_fields={"Payment Status": "Pending", "Date Completed": "10/6/26"},
    )
    assert finished.value(2, "Payment Status") == "Pending"
    assert finished.value(2, "Date Completed") == "10/6/26"


# --- What happens to Ongoing ----------------------------------------


def test_case_is_removed_from_ongoing_and_rows_below_shift_up():
    ongoing, finished = make_sheets(
        [ongoing_row("111"), ongoing_row("222"), ongoing_row("333")]
    )
    move_case_to_finished(ongoing, finished, "222")
    assert ongoing.value(2, "Case ID") == "111"
    assert ongoing.value(3, "Case ID") == "333"
    assert len(ongoing.get_all_values()) == 3  # header plus two cases


def test_ongoing_is_only_read_and_deleted_from_never_written_to():
    ongoing, finished = make_sheets([ongoing_row("111"), ongoing_row("222")])
    move_case_to_finished(ongoing, finished, "111")
    assert ongoing.writes == []


# --- Failures leave nothing lost ------------------------------------


def test_unknown_case_raises_and_changes_nothing():
    ongoing, finished = make_sheets([ongoing_row("111")], [finished_row("999")])
    with pytest.raises(ValueError, match="not found in Ongoing"):
        move_case_to_finished(ongoing, finished, "222")
    assert ongoing.value(2, "Case ID") == "111"
    assert finished.writes == []


def test_moving_the_same_case_twice_raises_the_second_time():
    ongoing, finished = make_sheets([ongoing_row("111")])
    move_case_to_finished(ongoing, finished, "111")
    with pytest.raises(ValueError, match="not found in Ongoing"):
        move_case_to_finished(ongoing, finished, "111")


class FinishedThatFailsToWrite(FakeWorksheet):
    def update(self, range_name, values):
        raise RuntimeError("API error")


def test_failed_write_to_finished_leaves_the_case_in_ongoing():
    ongoing = FakeWorksheet([ONGOING_HEADERS, ongoing_row("111")])
    finished = FinishedThatFailsToWrite([FINISHED_HEADERS])
    with pytest.raises(RuntimeError):
        move_case_to_finished(ongoing, finished, "111")
    assert ongoing.value(2, "Case ID") == "111"


def test_finished_tab_without_a_header_row_raises():
    ongoing = FakeWorksheet([ONGOING_HEADERS, ongoing_row("111")])

    class EmptyFinished(FakeWorksheet):
        def get_all_values(self):
            return []

    finished = EmptyFinished([FINISHED_HEADERS])
    with pytest.raises(ValueError, match="no header row"):
        move_case_to_finished(ongoing, finished, "111")
    assert ongoing.value(2, "Case ID") == "111"


# --- A move that was interrupted can be run again -------------------


class OngoingThatFailsToDeleteOnce(FakeWorksheet):
    """Deleting fails the first time: the case has been written to Finished but not removed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delete_attempts = 0

    def delete_rows(self, start_index, end_index=None):
        self.delete_attempts += 1
        if self.delete_attempts == 1:
            raise RuntimeError("API error")
        super().delete_rows(start_index, end_index)


def interrupted_move(extra_fields=None, finished_rows=()):
    """Run a move whose delete fails. The case is now in both tabs."""
    ongoing = OngoingThatFailsToDeleteOnce([ONGOING_HEADERS, ongoing_row("111")])
    finished = FakeWorksheet([FINISHED_HEADERS, *finished_rows])
    with pytest.raises(RuntimeError, match="API error"):
        move_case_to_finished(ongoing, finished, "111", extra_fields=extra_fields)
    return ongoing, finished


def case_ids_in(worksheet):
    return [row[0] for row in worksheet.get_all_values()[1:]]


def test_an_interrupted_move_leaves_the_case_in_both_tabs():
    ongoing, finished = interrupted_move()
    assert case_ids_in(ongoing) == ["111"]
    assert case_ids_in(finished) == ["111"]


def test_running_it_again_adds_no_second_row_to_finished():
    ongoing, finished = interrupted_move(extra_fields={"Date Completed": "10/19/2026 03:35 PM"})
    move_case_to_finished(ongoing, finished, "111", extra_fields={"Date Completed": "10/19/2026 03:35 PM"})
    assert case_ids_in(finished) == ["111"]


def test_running_it_again_finishes_the_move_by_removing_the_case_from_ongoing():
    ongoing, finished = interrupted_move()
    move_case_to_finished(ongoing, finished, "111")
    assert case_ids_in(ongoing) == []


def test_the_rerun_returns_the_row_the_case_is_already_on():
    ongoing, finished = interrupted_move(finished_rows=[finished_row("222"), finished_row("333")])
    assert case_ids_in(finished) == ["222", "333", "111"]
    assert move_case_to_finished(ongoing, finished, "111") == 4


def test_a_rerun_that_agrees_with_finished_writes_nothing_to_it():
    ongoing, finished = interrupted_move(extra_fields={"Date Completed": "10/19/2026 03:35 PM"})
    writes_before = list(finished.writes)
    move_case_to_finished(ongoing, finished, "111", extra_fields={"Date Completed": "10/19/2026 03:35 PM"})
    assert finished.writes == writes_before


def test_other_cases_in_finished_are_untouched_by_the_rerun():
    ongoing, finished = interrupted_move(finished_rows=[finished_row("222")])
    move_case_to_finished(ongoing, finished, "111")
    assert finished.value(2, "Case ID") == "222"
    assert finished.value(2, "Payment Status") == "Paid"


# --- Blank cells are filled, nothing else is written ----------------


def existing_row_for_111(**changes):
    values = dict(zip(FINISHED_HEADERS, ["111", "Test Patient", "(602) 555-0100", "J. Smith",
                                         "Dr. Shelton", "484.00", "", ""]))
    values.update(changes)
    return [values[h] for h in FINISHED_HEADERS]


def test_a_blank_cell_with_a_value_to_write_is_filled_and_only_that_cell_is_written():
    ongoing, finished = make_sheets([ongoing_row("111")], [existing_row_for_111()])
    move_case_to_finished(ongoing, finished, "111", extra_fields={"Date Completed": "10/6/26"})

    date_col = FINISHED_HEADERS.index("Date Completed") + 1
    assert finished.value(2, "Date Completed") == "10/6/26"
    assert finished.writes == [(2, date_col, "10/6/26")]


def test_a_value_someone_entered_is_kept_when_the_move_has_nothing_for_that_column():
    ongoing, finished = make_sheets(
        [ongoing_row("111")], [existing_row_for_111(**{"Payment Status": "Paid"})]
    )
    move_case_to_finished(ongoing, finished, "111")
    assert finished.value(2, "Payment Status") == "Paid"
    assert finished.writes == []
    assert case_ids_in(ongoing) == []


class FinishedThatParsesCellWrites(FakeWorksheet):
    """
    In real Sheets, update_cell() makes Google read the value as if it were typed
    into the cell: "11/12/2026 01:00 PM" becomes a date shown as "11/12/2026 1:00 PM",
    and "484.00" becomes the number 484. update() stores the text as given, which is
    what a new row gets. A fake can't imitate the parsing, so this refuses update_cell().
    """

    def update_cell(self, row, col, value):
        raise AssertionError("update_cell() parses the value as typed input; use update()")


def test_a_blank_cell_is_filled_with_a_write_that_stores_the_text_as_given():
    ongoing = FakeWorksheet([ONGOING_HEADERS, ongoing_row("111")])
    finished = FinishedThatParsesCellWrites([FINISHED_HEADERS, existing_row_for_111()])
    move_case_to_finished(
        ongoing, finished, "111", extra_fields={"Date Completed": "11/12/2026 01:00 PM"}
    )
    assert finished.value(2, "Date Completed") == "11/12/2026 01:00 PM"


def test_the_fill_happens_before_the_case_leaves_ongoing():
    steps = []

    class RecordingFinished(FakeWorksheet):
        def update(self, range_name, values):
            steps.append("fill")
            super().update(range_name, values)

    class RecordingOngoing(FakeWorksheet):
        def delete_rows(self, start_index, end_index=None):
            steps.append("delete")
            super().delete_rows(start_index, end_index)

    ongoing = RecordingOngoing([ONGOING_HEADERS, ongoing_row("111")])
    finished = RecordingFinished([FINISHED_HEADERS, existing_row_for_111()])
    move_case_to_finished(ongoing, finished, "111", extra_fields={"Date Completed": "10/6/26"})
    assert steps == ["fill", "delete"]


# --- A Finished row that disagrees stops the move -------------------


def test_a_different_value_in_finished_stops_the_move_and_names_the_column():
    ongoing, finished = make_sheets(
        [ongoing_row("111", price="484.00")], [existing_row_for_111(Price="250.00")]
    )
    with pytest.raises(ValueError, match="different values for: Price"):
        move_case_to_finished(ongoing, finished, "111")


def test_a_different_extra_value_also_stops_the_move():
    ongoing, finished = make_sheets(
        [ongoing_row("111")], [existing_row_for_111(**{"Date Completed": "5/1/2026"})]
    )
    with pytest.raises(ValueError, match="different values for: Date Completed"):
        move_case_to_finished(ongoing, finished, "111", extra_fields={"Date Completed": "10/6/26"})


def test_every_disagreeing_column_is_named():
    ongoing, finished = make_sheets(
        [ongoing_row("111", price="484.00", provider="Dr. Shelton")],
        [existing_row_for_111(Price="250.00", Provider="Dr. Nguyen")],
    )
    with pytest.raises(ValueError) as caught:
        move_case_to_finished(ongoing, finished, "111")
    assert "Provider" in str(caught.value) and "Price" in str(caught.value)


def test_a_refusal_changes_nothing_in_either_tab():
    ongoing, finished = make_sheets(
        [ongoing_row("111"), ongoing_row("222")], [existing_row_for_111(Price="250.00")]
    )
    finished_before = finished.get_all_values()
    with pytest.raises(ValueError):
        move_case_to_finished(ongoing, finished, "111", extra_fields={"Date Completed": "10/6/26"})
    assert finished.writes == []
    assert finished.get_all_values() == finished_before
    assert case_ids_in(ongoing) == ["111", "222"]


def test_the_refusal_says_what_to_do():
    ongoing, finished = make_sheets([ongoing_row("111")], [existing_row_for_111(Price="250.00")])
    with pytest.raises(ValueError, match="delete the case's row from Ongoing by hand"):
        move_case_to_finished(ongoing, finished, "111")


def test_a_case_in_finished_more_than_once_stops_the_move_and_names_the_rows():
    ongoing, finished = make_sheets(
        [ongoing_row("111")],
        [existing_row_for_111(), finished_row("222"), existing_row_for_111()],
    )
    with pytest.raises(ValueError, match="appears 2 times in Finished \\(rows 2, 4\\)"):
        move_case_to_finished(ongoing, finished, "111")
    assert finished.writes == []
    assert case_ids_in(ongoing) == ["111"]


# --- Finding the case in Finished -----------------------------------


def test_spaces_around_the_case_id_in_finished_are_ignored():
    ongoing, finished = make_sheets([ongoing_row("111")], [existing_row_for_111(**{"Case ID": " 111 "})])
    assert move_case_to_finished(ongoing, finished, "111") == 2
    assert len(finished.get_all_values()) == 2


def test_a_similar_case_id_is_not_mistaken_for_it():
    ongoing, finished = make_sheets([ongoing_row("111")], [finished_row("1111"), finished_row("11")])
    assert move_case_to_finished(ongoing, finished, "111") == 4
    assert case_ids_in(finished) == ["1111", "11", "111"]


def test_finding_rows_returns_every_match_in_order():
    values = [FINISHED_HEADERS, finished_row("111"), finished_row("222"), finished_row("111")]
    assert find_rows_in_finished(values, "111") == [2, 4]
    assert find_rows_in_finished(values, "999") == []


def test_finding_rows_without_a_case_id_header_raises():
    with pytest.raises(ValueError, match="not found in the Finished tab's headers"):
        find_rows_in_finished([["Patient Name"], ["x"]], "111")


def test_a_finished_tab_without_a_case_id_header_raises_before_anything_changes():
    ongoing = FakeWorksheet([ONGOING_HEADERS, ongoing_row("111")])
    finished = FakeWorksheet([["Patient Name", "Price"]])
    with pytest.raises(ValueError, match="not found in the Finished tab's headers"):
        move_case_to_finished(ongoing, finished, "111")
    assert case_ids_in(ongoing) == ["111"]
    assert finished.writes == []


# --- It says when it completes an interrupted move ------------------


def test_completing_an_interrupted_move_logs_a_warning(caplog):
    ongoing, finished = interrupted_move()
    with caplog.at_level(logging.WARNING, logger="app.sheet_case_mover"):
        move_case_to_finished(ongoing, finished, "111")
    assert len(caplog.records) == 1
    assert caplog.records[0].levelno == logging.WARNING
    assert "Case ID 111 was already in Finished (row 2), so no row was added" in caplog.text


def test_the_warning_lists_the_columns_it_filled_in(caplog):
    ongoing, finished = make_sheets([ongoing_row("111")], [existing_row_for_111()])
    with caplog.at_level(logging.WARNING, logger="app.sheet_case_mover"):
        move_case_to_finished(ongoing, finished, "111", extra_fields={"Date Completed": "10/6/26"})
    assert "Filled in: Date Completed." in caplog.text


def test_a_normal_move_logs_nothing(caplog):
    ongoing, finished = make_sheets([ongoing_row("111")])
    with caplog.at_level(logging.WARNING, logger="app.sheet_case_mover"):
        move_case_to_finished(ongoing, finished, "111")
    assert caplog.records == []