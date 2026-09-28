"""
Sweeps the Ongoing tab, applying OA's daily comment updates
(Provider, Psychometrist) to their respective columns.

Reads the whole tab in one call rather than one lookup per row,
same reasoning as find_case_row() elsewhere in this codebase —
avoids hitting Sheets API rate limits when scanning many cases in a
single sweep.

Comments are left untouched after processing. Reapplying the same
comment on a later run just re-sets the same value again for these
two fields specifically — harmless, since they're simple overwrites,
not something that accumulates. This assumption doesn't
automatically extend to price (+Service/Price entries), which is
deliberately out of scope here for exactly that reason.
"""

from app.comment_parser import parse_comment
from app.sheet_client import get_header_map, update_case_field


def apply_comment_updates(worksheet):
    """
    Reads every row's Comments cell, parses it, and applies
    Provider/Psychometrist where present.

    Returns a list of (case_id, parsed) tuples for every row that
    had a non-empty comment, so a caller — or Part 2, later — can
    inspect what happened without re-parsing.
    """
    header_map = get_header_map(worksheet)

    for required in ("Comments", "Case ID"):
        if required not in header_map:
            raise ValueError(f"'{required}' column not found in sheet headers")

    all_values = worksheet.get_all_values()
    headers = all_values[0]
    comments_idx = header_map["Comments"] - 1
    case_id_idx = header_map["Case ID"] - 1

    results = []

    for row_number, row in enumerate(all_values[1:], start=2):
        comment_text = row[comments_idx]
        if not comment_text.strip():
            continue

        case_id = row[case_id_idx]
        parsed = parse_comment(comment_text)

        if parsed.provider:
            update_case_field(worksheet, row_number, "Provider", parsed.provider)

        if parsed.psychometrist:
            update_case_field(worksheet, row_number, "Psychometrist", parsed.psychometrist)

        results.append((case_id, parsed))

    return results


if __name__ == "__main__":
    from app.sheet_client import get_client

    sh = get_client()
    ongoing = sh.worksheet("Ongoing")

    results = apply_comment_updates(ongoing)

    if not results:
        print("No rows had a comment to process.")
    else:
        for case_id, parsed in results:
            print(f"\nCase {case_id}:")
            print(f"  Provider applied: {parsed.provider}")
            print(f"  Psychometrist applied: {parsed.psychometrist}")
            if parsed.added_services:
                print(f"  (Services present, not applied yet — Part 3): {parsed.added_services}")
            if parsed.price_override:
                print(f"  (Price present, not applied yet — Part 3): {parsed.price_override}")
            if parsed.unrecognized_lines:
                print(f"  (Unrecognized lines, not surfaced yet — Part 2): {parsed.unrecognized_lines}")