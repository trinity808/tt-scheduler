"""
Unit test for schedule_generator.py's sort_table_by_datetime().

Rows are added in deliberately WRONG chronological order (1 PM, then
8 AM, then 10 AM). If sort_table_by_datetime() genuinely sorts, the
output order should be 8 AM, 10 AM, 1 PM regardless of insertion
order — a real sort, not a coincidence of append order.

Run from the project root as:
    python -m tests.test_sort_logic
"""

from docx import Document

from app.schedule_generator import SCHEDULE_HEADERS, sort_table_by_datetime


def build_test_table():
    doc = Document()
    table = doc.add_table(rows=1, cols=len(SCHEDULE_HEADERS))

    for i, header in enumerate(SCHEDULE_HEADERS):
        table.rows[0].cells[i].text = header

    # Deliberately out of order — 1 PM first, then 8 AM, then 10 AM
    test_rows = [
        ["30003", "Row C - 1PM", "", "", "", "May 4, 2026\n 1:00 PM MST", "", "", ""],
        ["30001", "Row A - 8AM", "", "", "", "May 4, 2026\n 8:00 AM MST", "", "", ""],
        ["30002", "Row B - 10AM", "", "", "", "May 4, 2026\n 10:00 AM MST", "", "", ""],
    ]

    for row_data in test_rows:
        row = table.add_row().cells
        for i, value in enumerate(row_data):
            row[i].text = value

    return table


def main():
    table = build_test_table()

    print("Order BEFORE sort:")
    for row in table.rows[1:]:
        print(f"  {row.cells[0].text} - {row.cells[1].text}")

    sort_table_by_datetime(table)

    print("\nOrder AFTER sort:")
    for row in table.rows[1:]:
        print(f"  {row.cells[0].text} - {row.cells[1].text}")

    actual_order = [row.cells[0].text for row in table.rows[1:]]
    expected_order = ["30001", "30002", "30003"]

    if actual_order == expected_order:
        print("\nPASS — rows correctly reordered by time, independent of insertion order.")
    else:
        print(f"\nFAIL — expected {expected_order}, got {actual_order}")


if __name__ == "__main__":
    main()