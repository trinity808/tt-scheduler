"""
Hidden per-case storage of a case's original test list.

When a case is added to Ongoing, its original tests (the codes from the
authorization PDF) are attached to its row as Google Sheets developer
metadata: invisible in the Sheets UI, kept with the row as it moves
through inserts and sorts, and deleted with the row when the case moves
to Finished. Later, the sweep rebuilds a case's current tests from this
stored original plus whatever the comment says, so rerunning is always
safe and a retracted note restores the original.

Only codes are stored, never descriptions. Metadata is capped at 30,000
characters per tab (key and value both count), so a case's entry is kept
to roughly 80 characters, enough for a few hundred active cases.

This module is logic only. Everything it needs from Sheets goes through
a small client object with three operations, scoped to one tab and using
1-based row numbers like the Sheets UI:

    client.search(key)                  -> list of MetadataEntry on that tab
    client.create(row_number, key, value)
    client.delete(metadata_id)

Tests use an in-memory client. The real one wraps the Sheets API.

Two things this module guards against:
  - A row that was cleared by hand keeps its metadata, so set_case_tests()
    removes whatever is already on a row before writing, rather than
    leaving a stale entry from the old case beside the new one.
  - Cutting and pasting cell contents moves values but not the row they
    belong to, so every stored entry carries its Case ID, and
    original_tests_for_row() ignores an entry whose Case ID doesn't match the row.

"No stored list" and "a stored list that couldn't be used" are kept
apart on purpose: the first means the caller may adopt the cell's
current text as the original, the second means something is wrong and
that must not be overwritten.
"""

import json
from dataclasses import dataclass, field


CASE_TESTS_KEY = "case_tests"


@dataclass
class MetadataEntry:
    metadata_id: int
    row_number: int
    value: str


@dataclass
class CaseTests:
    case_id: str
    tests: list  # ordered service codes, e.g. ["96101-WISC-V", "90791"]


@dataclass
class StoredTests:
    by_row: dict = field(default_factory=dict)    # row number -> CaseTests
    unusable: dict = field(default_factory=dict)  # row number -> why it can't be used

    def problems(self):
        return list(self.unusable.values())


def encode_case_tests(case_id, tests):
    """The compact text stored for a case. Raises ValueError on bad input."""
    case_id = str(case_id).strip()
    if not case_id:
        raise ValueError("a stored test list needs a Case ID")
    tests = list(tests)
    if not all(isinstance(test, str) for test in tests):
        raise ValueError("tests must be a list of text codes")
    return json.dumps({"case_id": case_id, "tests": tests}, separators=(",", ":"))


def decode_case_tests(value):
    """Reads what encode_case_tests() wrote. Raises ValueError if it isn't that."""
    try:
        data = json.loads(value)
    except (TypeError, ValueError) as error:
        raise ValueError("not valid JSON") from error
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")

    case_id = data.get("case_id")
    tests = data.get("tests")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("missing Case ID")
    if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
        raise ValueError("tests must be a list of text codes")
    return CaseTests(case_id=case_id, tests=tests)


def set_case_tests(client, row_number, case_id, tests):
    """
    Stores a case's original tests on its row, replacing anything already
    stored there. The new value is built first, so bad input raises before
    an existing entry is touched. Existing entries are deleted before the
    new one is created: if the create then fails, the row has no entry,
    which readers treat as "nothing stored", never as two conflicting ones.
    """
    value = encode_case_tests(case_id, tests)
    for entry in client.search(CASE_TESTS_KEY):
        if entry.row_number == row_number:
            client.delete(entry.metadata_id)
    client.create(row_number, CASE_TESTS_KEY, value)


def read_all_case_tests(client):
    """
    Reads every stored test list on the tab with a single search. Entries
    that can't be decoded, and rows holding more than one entry, are
    recorded as unusable instead of raising or guessing.
    """
    entries_by_row = {}
    for entry in client.search(CASE_TESTS_KEY):
        entries_by_row.setdefault(entry.row_number, []).append(entry)

    stored = StoredTests()
    for row_number, entries in sorted(entries_by_row.items()):
        if len(entries) > 1:
            stored.unusable[row_number] = (
                f"Row {row_number}: more than one stored test list found, "
                "so none was used"
            )
            continue
        try:
            stored.by_row[row_number] = decode_case_tests(entries[0].value)
        except ValueError:
            stored.unusable[row_number] = (
                f"Row {row_number}: the stored test list couldn't be read, "
                "so it was ignored"
            )
    return stored


def original_tests_for_row(stored, row_number, row_case_id):
    """
    The stored tests for a row, checked against the Case ID actually in it.

    Returns (CaseTests, None) when usable, (None, None) when nothing is
    stored for the row, and (None, message) when something is stored but
    can't be trusted.
    """
    if row_number in stored.unusable:
        return None, stored.unusable[row_number]

    entry = stored.by_row.get(row_number)
    if entry is None:
        return None, None

    row_case_id = str(row_case_id).strip()
    if entry.case_id != row_case_id:
        return None, (
            f"Row {row_number}: the stored test list is for case "
            f"{entry.case_id}, not {row_case_id}, so it was ignored"
        )
    return entry, None