"""
Sweeps the Ongoing tab, applying OA's daily comment updates
(Provider, Psychometrist) to their respective columns, and flagging
comments that need attention.

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

Since this may be run repeatedly against the same unchanged comment
(triggered manually, possibly more than once in a row), Provider and
Psychometrist are only written if they'd actually change — skips a
redundant API call otherwise.

Any parsed unrecognized_lines get flagged directly on the Comments
cell (highlight + note), cleared automatically once the comment is
fixed and reparses clean, since this reflects the comment's current
state, not a one-time event.
"""

from gspread.utils import rowcol_to_a1

from app.comment_parser import parse_comment
from app.sheet_client import get_header_map, update_case_field


# Orange — distinct from the yellow used to flag duplicate cases in
# ongoing_row_mapper.py, so a row can carry both flags without them
# reading as the same kind of issue.
COMMENT_ISSUE_COLOR = {"red": 1.0, "green": 0.8, "blue": 0.6}
# Plain white — the assumed "no flag" state. Doesn't attempt to
# restore whatever the sheet's actual prior formatting was, a
# simplification worth knowing about if the sheet ever uses banding
# or a non-white theme.
CLEAR_COLOR = {"red": 1.0, "green": 1.0, "blue": 1.0}


def flag_comment_issues(worksheet, cell, unrecognized_lines):
    """
    Highlights the given cell and attaches a note listing each issue
    if unrecognized_lines is non-empty. Clears the highlight and note
    if it's empty.
    """
    if unrecognized_lines:
        worksheet.format(cell, {"backgroundColor": COMMENT_ISSUE_COLOR})
        note_text = "Comment needs attention:\n" + "\n".join(unrecognized_lines)
        worksheet.update_note(cell, note_text)
    else:
        worksheet.format(cell, {"backgroundColor": CLEAR_COLOR})
        worksheet.clear_note(cell)


def apply_comment_updates(worksheet):
    """
    Reads every row's Comments cell, parses it, applies
    Provider/Psychometrist where present and changed, and flags the
    Comments cell if the parse found anything worth attention.

    Returns a list of (case_id, parsed) tuples for every row that
    had a non-empty comment, so a caller can inspect what happened
    without re-parsing.
    """
    header_map = get_header_map(worksheet)

    for required in ("Comments", "Case ID", "Provider", "Psychometrist"):
        if required not in header_map:
            raise ValueError(f"'{required}' column not found in sheet headers")

    all_values = worksheet.get_all_values()
    headers = all_values[0]
    comments_idx = header_map["Comments"] - 1
    case_id_idx = header_map["Case ID"] - 1
    provider_idx = header_map["Provider"] - 1
    psychometrist_idx = header_map["Psychometrist"] - 1

    results = []

    for row_number, row in enumerate(all_values[1:], start=2):
        case_id = row[case_id_idx].strip()
        if not case_id:
            continue
        
        comment_text = row[comments_idx]
        if not comment_text.strip():
            continue

        case_id = row[case_id_idx]
        parsed = parse_comment(comment_text)

        if parsed.provider and parsed.provider != row[provider_idx]:
            update_case_field(worksheet, row_number, "Provider", parsed.provider)

        if parsed.psychometrist and parsed.psychometrist != row[psychometrist_idx]:
            update_case_field(worksheet, row_number, "Psychometrist", parsed.psychometrist)

        comments_cell = rowcol_to_a1(row_number, header_map["Comments"])
        flag_comment_issues(worksheet, comments_cell, parsed.unrecognized_lines)

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
        flagged = [(case_id, parsed) for case_id, parsed in results if parsed.unrecognized_lines]

        for case_id, parsed in results:
            print(f"\nCase {case_id}:")
            print(f"  Provider applied: {parsed.provider}")
            print(f"  Psychometrist applied: {parsed.psychometrist}")
            if parsed.added_services:
                print(f"  (Services present, not applied yet — Part 3): {parsed.added_services}")
            if parsed.price_override:
                print(f"  (Price present, not applied yet — Part 3): {parsed.price_override}")
            if parsed.unrecognized_lines:
                print(f"  NEEDS ATTENTION: {parsed.unrecognized_lines}")

        print(f"\n{len(results)} comment(s) processed, {len(flagged)} flagged for attention.")
        if flagged:
            print("Flagged cases:", ", ".join(case_id for case_id, _ in flagged))

    input("\nPress Enter to close...")