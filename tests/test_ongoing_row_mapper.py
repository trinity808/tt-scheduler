"""
Tests for app/ongoing_row_mapper.py: mapping extracted data to a row,
where new cases land, and how duplicates are handled.

Run from the project root:
    python -m pytest tests/test_ongoing_row_mapper.py -v
"""

import pytest

from app.case_metadata import decode_case_tests, read_all_case_tests
from app.ongoing_row_mapper import (
    DUPLICATE_HIGHLIGHT_COLOR,
    MetadataWriteError,
    add_or_update_case,
    build_ongoing_row,
)
from tests.fake_metadata import FakeMetadataClient
from tests.fakes import FakeWorksheet


HEADERS = [
    "Case ID", "Patient Name", "Phone", "DOB", "Adult", "Child",
    "Date & Time", "Services Requested", "Allegations",
    "Psychometrist", "Provider", "Price", "Comments",
]
TEMPLATE = "Provider:\nPsychometrist:\n+Service:\nPrice:"


def extracted(case_id, name="Test Patient", total="484.00"):
    """Dictionary shaped like pdf_extractor.extract_schedule_data()'s output."""
    return {
        "case_id": case_id,
        "patient_name": name,
        "phone": "(602) 555-0100",
        "dob": "January 1, 2000",
        "adult": "X",
        "child": "",
        "schedule_date": "May 4th, 2026",
        "schedule_time": "08:00 AM MST",
        "services_requested": "1. 90791 - Psychiatric Exam",
        "allegations": "test allegations",
        "total": total,
    }


def case_row(case_id, provider="", psychometrist="", comments="", price="484.00"):
    row = [""] * len(HEADERS)
    row[HEADERS.index("Case ID")] = case_id
    row[HEADERS.index("Patient Name")] = "Existing Patient"
    row[HEADERS.index("Provider")] = provider
    row[HEADERS.index("Psychometrist")] = psychometrist
    row[HEADERS.index("Comments")] = comments
    row[HEADERS.index("Price")] = price
    return row


def template_row():
    row = [""] * len(HEADERS)
    row[HEADERS.index("Comments")] = TEMPLATE
    return row


def blank_row():
    return [""] * len(HEADERS)


# --- Mapping extracted data to a row --------------------------------


def test_build_row_maps_fields_by_header():
    row = dict(zip(HEADERS, build_ongoing_row(extracted("15419897"), HEADERS)))
    assert row["Case ID"] == "15419897"
    assert row["Price"] == "484.00"
    assert row["Date & Time"] == "May 4th, 2026 08:00 AM MST"
    assert row["Provider"] == ""
    assert row["Psychometrist"] == ""
    assert row["Comments"] == ""


def test_build_row_leaves_unknown_headers_blank():
    row = build_ongoing_row(extracted("15419897"), ["Case ID", "Some New Column"])
    assert row == ["15419897", ""]


# --- Where new cases land -------------------------------------------


def test_first_case_goes_into_row_2():
    ws = FakeWorksheet([HEADERS])
    assert add_or_update_case(ws, extracted("15419897")) == ("added", 2)
    assert ws.value(2, "Case ID") == "15419897"


def test_new_case_goes_after_last_case():
    ws = FakeWorksheet([HEADERS, case_row("111"), case_row("222")])
    assert add_or_update_case(ws, extracted("333")) == ("added", 4)
    assert ws.value(4, "Case ID") == "333"


def test_templates_below_data_do_not_push_new_case_down():
    ws = FakeWorksheet([
        HEADERS, case_row("111"), case_row("222"),
        template_row(), template_row(), template_row(),
    ])
    assert add_or_update_case(ws, extracted("333")) == ("added", 4)
    assert ws.value(4, "Case ID") == "333"


def test_template_is_kept_on_the_row_the_case_lands_in():
    ws = FakeWorksheet([HEADERS, case_row("111"), template_row(), template_row()])
    add_or_update_case(ws, extracted("222"))
    assert ws.value(3, "Comments") == TEMPLATE
    assert ws.value(3, "Price") == "484.00"
    assert ws.value(4, "Case ID") == ""
    assert ws.value(4, "Comments") == TEMPLATE


def test_gap_from_a_cleared_row_is_not_filled():
    ws = FakeWorksheet([HEADERS, case_row("111"), blank_row(), case_row("333")])
    assert add_or_update_case(ws, extracted("444")) == ("added", 5)
    assert ws.value(3, "Case ID") == ""


def test_new_case_after_a_gap_starts_at_column_a():
    ws = FakeWorksheet([HEADERS, case_row("111"), blank_row(), case_row("333")])
    add_or_update_case(ws, extracted("444", name="New Patient"))
    assert ws.rows[4][0] == "444"
    assert ws.rows[4][1] == "New Patient"


def test_sheet_grows_when_out_of_rows():
    ws = FakeWorksheet([HEADERS, case_row("111"), case_row("222")], row_count=3)
    assert add_or_update_case(ws, extracted("333")) == ("added", 4)
    assert ws.rows_added == 1
    assert ws.value(4, "Case ID") == "333"


# --- Duplicates -----------------------------------------------------


def test_duplicate_updates_in_place_instead_of_adding():
    ws = FakeWorksheet([HEADERS, case_row("111"), case_row("222", price="250.00")])
    assert add_or_update_case(ws, extracted("222", total="484.00")) == ("updated", 3)
    assert ws.value(3, "Price") == "484.00"
    assert len(ws.get_all_values()) == 3


def test_duplicate_keeps_manual_entry_fields():
    ws = FakeWorksheet([
        HEADERS,
        case_row("222", provider="Dr. Shelton", psychometrist="J. Smith",
                 comments="Provider: Dr. Shelton"),
    ])
    add_or_update_case(ws, extracted("222"))
    assert ws.value(2, "Provider") == "Dr. Shelton"
    assert ws.value(2, "Psychometrist") == "J. Smith"
    assert ws.value(2, "Comments") == "Provider: Dr. Shelton"


def test_duplicate_row_is_highlighted_and_noted():
    ws = FakeWorksheet([HEADERS, case_row("222")])
    add_or_update_case(ws, extracted("222"))
    assert ws.formats["A2:M2"]["backgroundColor"] == DUPLICATE_HIGHLIGHT_COLOR
    assert "Duplicate entry received" in ws.notes["A2"]


def test_duplicate_match_ignores_surrounding_whitespace():
    ws = FakeWorksheet([HEADERS, case_row(" 222 ")])
    assert add_or_update_case(ws, extracted("222")) == ("updated", 2)


# --- Storing a case's original tests --------------------------------


def with_services(data, *codes):
    """The same extraction, with the services list pdf_extractor produces."""
    return {**data, "services": [{"code": c, "desc": f"{c} description"} for c in codes]}


def stored_on(client, row_number):
    values = client.values_on(row_number)
    assert len(values) == 1, f"expected one entry on row {row_number}, found {len(values)}"
    return decode_case_tests(values[0])


def test_new_case_stores_its_original_tests_on_the_row_it_lands_in():
    ws = FakeWorksheet([HEADERS, case_row("111")])
    client = FakeMetadataClient()
    data = with_services(extracted("222"), "96101-WISC-V", "90791")

    assert add_or_update_case(ws, data, metadata=client) == ("added", 3)

    stored = stored_on(client, 3)
    assert stored.case_id == "222"
    assert stored.tests == ["96101-WISC-V", "90791"]


def test_only_codes_are_stored_not_descriptions():
    ws = FakeWorksheet([HEADERS])
    client = FakeMetadataClient()
    add_or_update_case(ws, with_services(extracted("111"), "90791"), metadata=client)
    assert "description" not in client.values_on(2)[0]


def test_entry_follows_the_row_when_the_case_lands_on_a_template_row():
    ws = FakeWorksheet([HEADERS, case_row("111"), template_row(), template_row()])
    client = FakeMetadataClient()
    add_or_update_case(ws, with_services(extracted("222"), "90791"), metadata=client)
    assert stored_on(client, 3).case_id == "222"
    assert client.values_on(4) == []


def test_repeated_pdf_stores_the_tests_again_and_keeps_one_entry():
    ws = FakeWorksheet([HEADERS])
    client = FakeMetadataClient()
    add_or_update_case(ws, with_services(extracted("111"), "90791"), metadata=client)

    result = add_or_update_case(
        ws, with_services(extracted("111"), "90791", "96130-AUTISM"), metadata=client
    )

    assert result == ("updated", 2)
    assert stored_on(client, 2).tests == ["90791", "96130-AUTISM"]


def test_stale_entry_on_a_hand_cleared_row_is_replaced():
    ws = FakeWorksheet([HEADERS, case_row("111"), case_row("222")])
    client = FakeMetadataClient()
    add_or_update_case(ws, with_services(extracted("333"), "90791"), metadata=client)
    # Row 4 is cleared by hand. Its entry stays behind, as real Sheets keeps it.
    ws.rows[3] = blank_row()

    add_or_update_case(ws, with_services(extracted("444"), "96130-AUTISM"), metadata=client)

    assert stored_on(client, 4).case_id == "444"


def test_other_cases_entries_are_left_alone():
    ws = FakeWorksheet([HEADERS])
    client = FakeMetadataClient()
    add_or_update_case(ws, with_services(extracted("111"), "90791"), metadata=client)
    add_or_update_case(ws, with_services(extracted("222"), "96130-AUTISM"), metadata=client)

    stored = read_all_case_tests(client)
    assert stored.by_row[2].case_id == "111"
    assert stored.by_row[3].case_id == "222"


def test_case_with_no_services_stores_an_empty_list():
    ws = FakeWorksheet([HEADERS])
    client = FakeMetadataClient()
    add_or_update_case(ws, extracted("111"), metadata=client)  # no "services" key
    assert stored_on(client, 2).tests == []


def test_without_a_metadata_client_nothing_is_stored_and_nothing_changes():
    ws = FakeWorksheet([HEADERS, case_row("111")])
    assert add_or_update_case(ws, extracted("222")) == ("added", 3)
    assert ws.value(3, "Case ID") == "222"


# --- When storing fails ---------------------------------------------


def test_failed_store_on_a_new_case_raises_but_the_row_is_written():
    ws = FakeWorksheet([HEADERS])
    client = FakeMetadataClient()
    client.fail_create = True

    with pytest.raises(MetadataWriteError) as caught:
        add_or_update_case(ws, with_services(extracted("111"), "90791"), metadata=client)

    assert ws.value(2, "Case ID") == "111"
    assert caught.value.case_id == "111"
    assert caught.value.action == "added"
    assert caught.value.row_number == 2
    assert "original tests couldn't be stored" in str(caught.value)
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_failed_store_on_a_repeated_case_raises_but_the_row_is_still_updated():
    ws = FakeWorksheet([HEADERS, case_row("111", price="250.00")])
    client = FakeMetadataClient()
    client.fail_create = True

    with pytest.raises(MetadataWriteError) as caught:
        add_or_update_case(
            ws, with_services(extracted("111", total="484.00"), "90791"), metadata=client
        )

    assert caught.value.action == "updated"
    assert ws.value(2, "Price") == "484.00"


def test_a_retry_after_a_failed_store_leaves_one_entry():
    ws = FakeWorksheet([HEADERS])
    client = FakeMetadataClient()
    data = with_services(extracted("111"), "90791")

    client.fail_create = True
    with pytest.raises(MetadataWriteError):
        add_or_update_case(ws, data, metadata=client)

    client.fail_create = False
    assert add_or_update_case(ws, data, metadata=client) == ("updated", 2)
    assert stored_on(client, 2).case_id == "111"


@pytest.mark.parametrize("case_id", ["", "   "])
def test_blank_case_id_with_metadata_raises_before_writing_anything(case_id):
    ws = FakeWorksheet([HEADERS, case_row("111")])
    client = FakeMetadataClient()

    with pytest.raises(ValueError, match="Case ID is needed"):
        add_or_update_case(ws, with_services(extracted(case_id), "90791"), metadata=client)

    assert ws.writes == []
    assert client.entries == []