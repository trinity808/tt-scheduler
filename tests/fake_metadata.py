"""
An in-memory stand-in for the three operations case_metadata.py needs from
Sheets (search, create, delete), shared by the tests that store or read a
case's original tests. Not collected by pytest, since the filename doesn't
start with test_.
"""

from app.case_metadata import CASE_TESTS_KEY, MetadataEntry


class FakeMetadataClient:
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