"""
Tests for app/case_metadata.py: storing and reading each case's original
test list as hidden row metadata.

These use an in-memory client defined in this file, so no Google Sheet is
involved. That also means they can't show how real Sheets behaves (metadata
following a row through a sort, say, which was checked separately on the
sandbox sheet), only that this logic uses the client correctly.

Run from the project root:
    python -m pytest tests/test_case_metadata.py -v
"""

import pytest

from app.case_metadata import (
    CASE_TESTS_KEY,
    MetadataEntry,
    decode_case_tests,
    encode_case_tests,
    read_all_case_tests,
    set_case_tests,
    original_tests_for_row,
)


class FakeMetadataClient:
    """An in-memory stand-in for the three operations the logic needs."""

    def __init__(self):
        self.entries = []   # MetadataEntry
        self.keys = {}      # metadata_id -> key
        self.next_id = 1
        self.searches = 0
        self.fail_create = False

    def search(self, key):
        self.searches += 1
        return [e for e in self.entries if self.keys[e.metadata_id] == key]

    def create(self, row_number, key, value):
        if self.fail_create:
            raise RuntimeError("API error")
        entry = MetadataEntry(self.next_id, row_number, value)
        self.keys[entry.metadata_id] = key
        self.entries.append(entry)
        self.next_id += 1

    def delete(self, metadata_id):
        self.entries = [e for e in self.entries if e.metadata_id != metadata_id]

    # --- test helpers ---

    def put(self, row_number, value, key=CASE_TESTS_KEY):
        self.create(row_number, key, value)

    def values_on(self, row_number, key=CASE_TESTS_KEY):
        return [
            e.value for e in self.entries
            if e.row_number == row_number and self.keys[e.metadata_id] == key
        ]


TESTS = ["96101-WISC-V", "90791", "96130-AUTISM"]


# --- Encoding and decoding ------------------------------------------


def test_encode_then_decode_round_trips():
    decoded = decode_case_tests(encode_case_tests("15419897", TESTS))
    assert decoded.case_id == "15419897"
    assert decoded.tests == TESTS


def test_encoded_value_is_compact():
    # Metadata is capped per tab, so the size of an entry matters.
    assert len(encode_case_tests("15419897", TESTS)) <= 80


def test_case_id_is_normalized():
    assert decode_case_tests(encode_case_tests(15419897, TESTS)).case_id == "15419897"
    assert decode_case_tests(encode_case_tests(" 15419897 ", TESTS)).case_id == "15419897"


def test_empty_test_list_round_trips():
    assert decode_case_tests(encode_case_tests("15419897", [])).tests == []


@pytest.mark.parametrize("case_id", ["", "   "])
def test_encoding_needs_a_case_id(case_id):
    with pytest.raises(ValueError):
        encode_case_tests(case_id, TESTS)


def test_encoding_needs_text_codes():
    with pytest.raises(ValueError):
        encode_case_tests("15419897", ["96101-WISC-V", 90791])


@pytest.mark.parametrize(
    "bad_value",
    [
        None,
        "",
        "not json",
        "[]",
        "42",
        '{"tests": ["90791"]}',                       # no Case ID
        '{"case_id": "", "tests": []}',               # empty Case ID
        '{"case_id": "1"}',                           # no tests
        '{"case_id": "1", "tests": "90791"}',         # tests not a list
        '{"case_id": "1", "tests": [90791]}',         # codes not text
        '{"case_id": 1, "tests": []}',                # Case ID not text
    ],
)
def test_decoding_rejects_anything_else(bad_value):
    with pytest.raises(ValueError):
        decode_case_tests(bad_value)


# --- Storing --------------------------------------------------------


def test_set_creates_one_entry_on_the_row():
    client = FakeMetadataClient()
    set_case_tests(client, 5, "15419897", TESTS)
    assert len(client.entries) == 1
    assert client.entries[0].row_number == 5
    assert decode_case_tests(client.entries[0].value).tests == TESTS


def test_set_replaces_what_is_already_on_the_row():
    # A row cleared by hand keeps its metadata, so a new case landing on
    # it must not end up beside a stale entry from the old case.
    client = FakeMetadataClient()
    client.put(5, encode_case_tests("OLD-CASE", ["90791"]))
    set_case_tests(client, 5, "15419897", TESTS)

    values = client.values_on(5)
    assert len(values) == 1
    assert decode_case_tests(values[0]).case_id == "15419897"


def test_set_removes_duplicates_already_on_the_row():
    client = FakeMetadataClient()
    client.put(5, encode_case_tests("A", ["90791"]))
    client.put(5, encode_case_tests("B", ["90791"]))
    set_case_tests(client, 5, "15419897", TESTS)
    assert len(client.values_on(5)) == 1


def test_set_leaves_other_rows_and_other_keys_alone():
    client = FakeMetadataClient()
    client.put(4, encode_case_tests("111", ["90791"]))
    client.put(5, "something else", key="other_key")
    set_case_tests(client, 5, "15419897", TESTS)

    assert len(client.values_on(4)) == 1
    assert client.values_on(5, key="other_key") == ["something else"]


def test_bad_input_raises_before_touching_an_existing_entry():
    client = FakeMetadataClient()
    client.put(5, encode_case_tests("OLD-CASE", ["90791"]))
    with pytest.raises(ValueError):
        set_case_tests(client, 5, "", TESTS)
    assert len(client.values_on(5)) == 1


def test_failed_create_raises_and_leaves_no_entry():
    # Deleting first means a failure leaves "nothing stored", never two
    # conflicting entries.
    client = FakeMetadataClient()
    client.put(5, encode_case_tests("OLD-CASE", ["90791"]))
    client.fail_create = True
    with pytest.raises(RuntimeError):
        set_case_tests(client, 5, "15419897", TESTS)
    assert client.values_on(5) == []


# --- Reading --------------------------------------------------------


def test_read_returns_every_row_with_its_tests():
    client = FakeMetadataClient()
    set_case_tests(client, 2, "111", ["90791"])
    set_case_tests(client, 7, "222", TESTS)

    stored = read_all_case_tests(client)
    assert sorted(stored.by_row) == [2, 7]
    assert stored.by_row[7].case_id == "222"
    assert stored.by_row[7].tests == TESTS
    assert stored.problems() == []


def test_read_makes_a_single_search():
    client = FakeMetadataClient()
    for row in range(2, 12):
        set_case_tests(client, row, str(row), ["90791"])
    client.searches = 0
    read_all_case_tests(client)
    assert client.searches == 1


def test_read_on_an_empty_tab():
    stored = read_all_case_tests(FakeMetadataClient())
    assert stored.by_row == {}
    assert stored.problems() == []


def test_read_ignores_other_keys():
    client = FakeMetadataClient()
    client.put(3, "unrelated", key="other_key")
    assert read_all_case_tests(client).by_row == {}


def test_undecodable_entry_is_reported_and_the_rest_still_load():
    client = FakeMetadataClient()
    set_case_tests(client, 2, "111", ["90791"])
    client.put(3, "garbage")

    stored = read_all_case_tests(client)
    assert list(stored.by_row) == [2]
    assert 3 in stored.unusable
    assert "Row 3" in stored.problems()[0]


def test_two_entries_on_one_row_means_neither_is_used():
    client = FakeMetadataClient()
    client.put(4, encode_case_tests("A", ["90791"]))
    client.put(4, encode_case_tests("B", ["90791"]))

    stored = read_all_case_tests(client)
    assert 4 not in stored.by_row
    assert "more than one stored test list" in stored.unusable[4]


# --- Checking an entry against its row ------------------------------


def stored_with(row_number, case_id, tests=TESTS):
    client = FakeMetadataClient()
    set_case_tests(client, row_number, case_id, tests)
    return read_all_case_tests(client)


def test_matching_case_id_returns_the_tests():
    entry, problem = original_tests_for_row(stored_with(5, "15419897"), 5, "15419897")
    assert entry.tests == TESTS
    assert problem is None


def test_row_case_id_is_normalized_before_comparing():
    entry, problem = original_tests_for_row(stored_with(5, "15419897"), 5, " 15419897 ")
    assert entry is not None
    assert problem is None


def test_nothing_stored_is_not_a_problem():
    assert original_tests_for_row(stored_with(5, "15419897"), 9, "999") == (None, None)


def test_mismatched_case_id_is_ignored_with_both_ids_named():
    entry, problem = original_tests_for_row(stored_with(5, "15419897"), 5, "15419898")
    assert entry is None
    assert "15419897" in problem
    assert "15419898" in problem


def test_unusable_entry_is_a_problem_not_nothing_stored():
    # "Nothing stored" lets a caller adopt the cell as the original. An
    # unusable entry means a stored original exists but can't be trusted,
    # so that must not look the same.
    client = FakeMetadataClient()
    client.put(5, "garbage")
    entry, problem = original_tests_for_row(read_all_case_tests(client), 5, "15419897")
    assert entry is None
    assert problem is not None