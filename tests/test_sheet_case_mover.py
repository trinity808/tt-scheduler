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

import pytest

from app.sheet_case_mover import build_finished_row, move_case_to_finished
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