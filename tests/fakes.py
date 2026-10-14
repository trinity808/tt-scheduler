"""
Shared test helpers.

FakeWorksheet stands in for a gspread Worksheet, so tests can build
exactly the sheet they need in memory, run the real code against it,
and inspect what happened, without a Google account or network.

It implements only the methods our code calls, and imitates the real
Sheets behaviors that code depends on:
  - get_all_values() returns a snapshot, stops at the last row with
    any content, and pads rows to the same width.
  - The sheet has a fixed number of rows (row_count). Writing past it
    is an error, which is why the code calls add_rows() first.

It deliberately has no append_row(). Placement code once relied on it,
and real Sheets' table detection shifted rows into the wrong columns;
any code that goes back to using it will fail its tests here.

What a fake can't catch: real Sheets behavior beyond what's imitated
here. That's what the occasional live check is for.

Not collected by pytest, since the filename doesn't start with test_.
"""

from gspread.utils import a1_to_rowcol


class FakeWorksheet:
    def __init__(self, rows, row_count=1000):
        self.rows = [list(row) for row in rows]  # row 1 = headers
        self.row_count = row_count
        self.notes = {}       # cell (e.g. "D2") -> note text
        self.formats = {}     # cell or range -> last format applied
        self.writes = []      # every cell written, as (row, col, value)
        self.rows_added = 0

    # --- reads ---

    def get_all_values(self):
        rows = [list(row) for row in self.rows]
        while rows and not any(cell.strip() for cell in rows[-1]):
            rows.pop()
        width = max(len(row) for row in rows)
        return [row + [""] * (width - len(row)) for row in rows]

    def row_values(self, row_number):
        return list(self.rows[row_number - 1])

    # --- writes ---

    def _set(self, row, col, value):
        if row > self.row_count:
            raise IndexError(f"row {row} is beyond the sheet's {self.row_count} rows")
        width = len(self.rows[0])
        while len(self.rows) < row:
            self.rows.append([""] * width)
        target = self.rows[row - 1]
        while len(target) < col:
            target.append("")
        target[col - 1] = value
        self.writes.append((row, col, value))

    def update_cell(self, row, col, value):
        self._set(row, col, value)

    def update(self, range_name, values):
        start_row, start_col = a1_to_rowcol(range_name.split(":")[0])
        for r_offset, row_values in enumerate(values):
            for c_offset, value in enumerate(row_values):
                self._set(start_row + r_offset, start_col + c_offset, value)

    def add_rows(self, n):
        self.row_count += n
        self.rows_added += n

    def format(self, cell_range, fmt):
        self.formats[cell_range] = fmt

    def update_note(self, cell, text):
        self.notes[cell] = text

    def clear_note(self, cell):
        self.notes.pop(cell, None)

    # --- test helper ---

    def value(self, row_number, header):
        """Read a cell by row number and header name."""
        row = self.rows[row_number - 1]
        idx = self.rows[0].index(header)
        return row[idx] if idx < len(row) else ""