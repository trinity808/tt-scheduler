"""
A worksheet that answers the way Google does for a tab with nothing in it.

The shared FakeWorksheet (tests/fakes.py) returns [] for a blank tab. The real Sheets, read
through gspread 6.2.1, returns [[]]: one empty row. A live check against the sandbox found
this, after code written against the fake had treated [] as the only way a tab could be
empty. Use this class wherever a test needs a blank tab, or a tab the code has just created.

Not collected by pytest, since the filename doesn't start with test_.
"""

from tests.fakes import FakeWorksheet


class GoogleLikeWorksheet(FakeWorksheet):
    def get_all_values(self):
        return super().get_all_values() or [[]]