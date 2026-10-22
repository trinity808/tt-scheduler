"""
Tests for app/sheets_metadata_client.py: the real client behind the case
metadata logic.

No network is involved. A stub worksheet hands the client a recording HTTP
object, and a simulated API answers the way the real one was seen to on the
sandbox sheet, including leaving out a zero sheetId. These tests show the
client builds the right requests and reads responses correctly; that the
real API agrees with them is what scripts/check_case_metadata.py is for.

Run from the project root:
    python -m pytest tests/test_sheets_metadata_client.py -v
"""

import pytest

from app.case_metadata import (
    CASE_TESTS_KEY,
    decode_case_tests,
    original_tests_for_row,
    read_all_case_tests,
    set_case_tests,
)
from app.sheets_metadata_client import VISIBILITY, SheetsMetadataClient


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class SimulatedAPI:
    """
    Records every call, and keeps metadata in the shape the real API uses:
    created entries get an id, and a zero sheetId is left out (a zero
    startIndex is not: the real API sends it).
    """

    def __init__(self):
        self.metadata = []
        self.next_id = 100
        self.batch_calls = []    # (spreadsheet_id, body)
        self.requests = []       # (method, url, json)

    def batch_update(self, spreadsheet_id, body):
        self.batch_calls.append((spreadsheet_id, body))
        for request in body["requests"]:
            if "createDeveloperMetadata" in request:
                entry = dict(request["createDeveloperMetadata"]["developerMetadata"])
                entry["metadataId"] = self.next_id
                self.next_id += 1
                span = dict(entry["location"]["dimensionRange"])
                if span.get("sheetId") == 0:
                    del span["sheetId"]
                entry["location"] = {"locationType": "ROW", "dimensionRange": span}
                self.metadata.append(entry)
            elif "deleteDeveloperMetadata" in request:
                lookup = request["deleteDeveloperMetadata"]["dataFilter"]["developerMetadataLookup"]
                self.metadata = [
                    m for m in self.metadata if m["metadataId"] != lookup["metadataId"]
                ]
        return {"spreadsheetId": spreadsheet_id}

    def request(self, method, url, json=None):
        self.requests.append((method, url, json))
        key = json["dataFilters"][0]["developerMetadataLookup"]["metadataKey"]
        matches = [
            {"developerMetadata": m} for m in self.metadata if m["metadataKey"] == key
        ]
        return FakeResponse({"matchedDeveloperMetadata": matches} if matches else {})


class StubWorksheet:
    def __init__(self, api, sheet_id=7, spreadsheet_id="SHEET123"):
        self.client = api
        self.spreadsheet_id = spreadsheet_id
        self.id = sheet_id


def make_client(sheet_id=7):
    api = SimulatedAPI()
    return SheetsMetadataClient(StubWorksheet(api, sheet_id=sheet_id)), api


# --- The requests it sends ------------------------------------------


def test_create_sends_a_single_row_entry_with_zero_based_indexes():
    client, api = make_client(sheet_id=7)
    client.create(5, CASE_TESTS_KEY, "value")

    spreadsheet_id, body = api.batch_calls[0]
    assert spreadsheet_id == "SHEET123"
    metadata = body["requests"][0]["createDeveloperMetadata"]["developerMetadata"]
    assert metadata["metadataKey"] == CASE_TESTS_KEY
    assert metadata["metadataValue"] == "value"
    assert metadata["location"]["dimensionRange"] == {
        "sheetId": 7, "dimension": "ROWS", "startIndex": 4, "endIndex": 5,
    }


def test_create_uses_document_visibility():
    client, api = make_client()
    client.create(2, CASE_TESTS_KEY, "value")
    metadata = api.batch_calls[0][1]["requests"][0]["createDeveloperMetadata"]["developerMetadata"]
    assert metadata["visibility"] == VISIBILITY == "DOCUMENT"


def test_delete_looks_the_entry_up_by_its_id():
    client, api = make_client()
    client.delete(123)
    request = api.batch_calls[0][1]["requests"][0]
    assert request == {
        "deleteDeveloperMetadata": {
            "dataFilter": {"developerMetadataLookup": {"metadataId": 123}}
        }
    }


def test_search_asks_for_row_entries_with_the_key_on_this_spreadsheet():
    client, api = make_client()
    client.search(CASE_TESTS_KEY)

    method, url, body = api.requests[0]
    assert method == "post"
    assert url == "https://sheets.googleapis.com/v4/spreadsheets/SHEET123/developerMetadata:search"
    assert body == {
        "dataFilters": [{
            "developerMetadataLookup": {
                "metadataKey": CASE_TESTS_KEY,
                "locationType": "ROW",
            }
        }]
    }


# --- Reading what comes back ----------------------------------------


def test_search_returns_one_based_rows_with_ids_and_values():
    client, api = make_client()
    client.create(2, CASE_TESTS_KEY, "first")
    client.create(9, CASE_TESTS_KEY, "second")

    entries = client.search(CASE_TESTS_KEY)
    assert [(e.row_number, e.value) for e in entries] == [(2, "first"), (9, "second")]
    assert all(isinstance(e.metadata_id, int) for e in entries)


def test_search_with_no_matches_returns_an_empty_list():
    client, _ = make_client()
    assert client.search(CASE_TESTS_KEY) == []


def test_search_ignores_entries_on_other_tabs():
    api = SimulatedAPI()
    SheetsMetadataClient(StubWorksheet(api, sheet_id=3)).create(4, CASE_TESTS_KEY, "other tab")
    mine = SheetsMetadataClient(StubWorksheet(api, sheet_id=7))
    mine.create(4, CASE_TESTS_KEY, "my tab")

    assert [e.value for e in mine.search(CASE_TESTS_KEY)] == ["my tab"]


def test_search_ignores_entries_spanning_more_than_one_row():
    client, api = make_client(sheet_id=7)
    api.metadata.append({
        "metadataId": 1, "metadataKey": CASE_TESTS_KEY, "metadataValue": "range",
        "location": {"dimensionRange": {"sheetId": 7, "dimension": "ROWS",
                                         "startIndex": 2, "endIndex": 5}},
    })
    assert client.search(CASE_TESTS_KEY) == []


def test_search_ignores_column_entries():
    client, api = make_client(sheet_id=7)
    api.metadata.append({
        "metadataId": 1, "metadataKey": CASE_TESTS_KEY, "metadataValue": "column",
        "location": {"dimensionRange": {"sheetId": 7, "dimension": "COLUMNS",
                                         "startIndex": 2, "endIndex": 3}},
    })
    assert client.search(CASE_TESTS_KEY) == []


# --- Zero values ----------------------------------------------------


def test_row_one_is_read_back_as_row_one():
    client, api = make_client()
    client.create(1, CASE_TESTS_KEY, "header row")
    # The real API sends startIndex even when it is 0.
    assert api.metadata[0]["location"]["dimensionRange"]["startIndex"] == 0
    assert client.search(CASE_TESTS_KEY)[0].row_number == 1


def test_a_missing_start_index_is_still_read_as_zero():
    # Not seen from the real API, but cheap to tolerate.
    client, api = make_client(sheet_id=7)
    api.metadata.append({
        "metadataId": 1, "metadataKey": CASE_TESTS_KEY, "metadataValue": "header row",
        "location": {"dimensionRange": {"sheetId": 7, "dimension": "ROWS", "endIndex": 1}},
    })
    assert client.search(CASE_TESTS_KEY)[0].row_number == 1


def test_first_tab_has_sheet_id_zero_which_comes_back_missing():
    client, api = make_client(sheet_id=0)
    client.create(3, CASE_TESTS_KEY, "first tab")
    assert "sheetId" not in api.metadata[0]["location"]["dimensionRange"]
    assert [e.value for e in client.search(CASE_TESTS_KEY)] == ["first tab"]


def test_a_missing_sheet_id_is_not_mistaken_for_another_tab():
    api = SimulatedAPI()
    SheetsMetadataClient(StubWorksheet(api, sheet_id=0)).create(3, CASE_TESTS_KEY, "tab zero")
    on_tab_five = SheetsMetadataClient(StubWorksheet(api, sheet_id=5))
    assert on_tab_five.search(CASE_TESTS_KEY) == []


# --- Together with the logic ----------------------------------------


def test_logic_round_trips_through_the_client():
    client, _ = make_client()
    set_case_tests(client, 2, "111", ["90791"])
    set_case_tests(client, 6, "222", ["96101-WISC-V", "96130-AUTISM"])

    stored = read_all_case_tests(client)
    entry, problem = original_tests_for_row(stored, 6, "222")
    assert entry.tests == ["96101-WISC-V", "96130-AUTISM"]
    assert problem is None


def test_replacing_a_rows_entry_deletes_the_old_one_by_id():
    client, api = make_client()
    set_case_tests(client, 4, "OLD", ["90791"])
    old_id = api.metadata[0]["metadataId"]

    set_case_tests(client, 4, "NEW", ["96130-AUTISM"])

    assert len(api.metadata) == 1
    assert api.metadata[0]["metadataId"] != old_id
    assert decode_case_tests(api.metadata[0]["metadataValue"]).case_id == "NEW"


def test_reading_everything_is_one_search_request():
    client, api = make_client()
    for row in range(2, 8):
        set_case_tests(client, row, str(row), ["90791"])
    api.requests.clear()

    read_all_case_tests(client)
    assert len(api.requests) == 1


def test_sheet_zero_works_end_to_end():
    client, _ = make_client(sheet_id=0)
    set_case_tests(client, 1, "111", ["90791"])
    set_case_tests(client, 2, "222", ["90791"])
    assert sorted(read_all_case_tests(client).by_row) == [1, 2]