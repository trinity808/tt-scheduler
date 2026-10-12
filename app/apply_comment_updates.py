"""
Sweeps the Ongoing tab, applying OA's daily comment updates
(Provider, Psychometrist) to their respective columns, and flagging
comments that need attention.

Reads the whole tab in one call rather than one lookup per row,
same reasoning as find_case_row() elsewhere in this codebase: avoids
hitting Sheets API rate limits when scanning many cases in a single
sweep.

Rows without a Case ID are skipped entirely, comment and all. OA may
keep a reusable comment template in spare rows, and that isn't meant
to apply to any case.

Comments are left untouched after processing. Since this may be run
repeatedly against the same unchanged comment, Provider and
Psychometrist are only written if they'd actually change. Each run
records what happened to each field (updated, or already set), so
the console reports real changes rather than just what the comment
says. Test changes (Add and Change) are deliberately out of scope here.

Any parsed unrecognized_lines get flagged directly on the Comments
cell (highlight + note), cleared automatically once the comment is
fixed and reparses clean, since this reflects the comment's current
state, not a one-time event.
"""

from gspread.utils import rowcol_to_a1

from app.comment_parser import parse_comment
from app.sheet_client import get_header_map, update_case_field


# Orange, distinct from the yellow used to flag duplicate cases in
# ongoing_row_mapper.py, so a row can carry both flags without them
# reading as the same kind of issue.
COMMENT_ISSUE_COLOR = {"red": 1.0, "green": 0.8, "blue": 0.6}
# Plain white, the assumed "no flag" state. Doesn't attempt to
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
    Reads every case row's Comments cell, parses it, applies
    Provider/Psychometrist where present and changed, and flags the
    Comments cell if the parse found anything worth attention.

    Returns a list of dicts, one per case row with a non-empty
    comment:
        {
            "case_id": "15419897",
            "parsed": ParsedComment(...),
            "fields": {
                "Provider": ("updated", old_value, new_value),
                "Psychometrist": ("unchanged", old_value, new_value),
            },
        }
    "fields" only contains fields the comment actually mentioned.
    """
    header_map = get_header_map(worksheet)

    for required in ("Comments", "Case ID", "Provider", "Psychometrist"):
        if required not in header_map:
            raise ValueError(f"'{required}' column not found in sheet headers")

    all_values = worksheet.get_all_values()
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

        parsed = parse_comment(comment_text)

        field_outcomes = {}
        for field, new_value, idx in (
            ("Provider", parsed.provider, provider_idx),
            ("Psychometrist", parsed.psychometrist, psychometrist_idx),
        ):
            if not new_value:
                continue
            old_value = row[idx] if idx < len(row) else ""
            if new_value == old_value:
                field_outcomes[field] = ("unchanged", old_value, new_value)
            else:
                update_case_field(worksheet, row_number, field, new_value)
                field_outcomes[field] = ("updated", old_value, new_value)

        comments_cell = rowcol_to_a1(row_number, header_map["Comments"])
        flag_comment_issues(worksheet, comments_cell, parsed.unrecognized_lines)

        results.append({"case_id": case_id, "parsed": parsed, "fields": field_outcomes})

    return results


if __name__ == "__main__":
    from app.sheet_client import get_client

    sh = get_client()
    ongoing = sh.worksheet("Ongoing")

    results = apply_comment_updates(ongoing)

    if not results:
        print("No rows had a comment to process.")
    else:
        updated_count = 0
        flagged = []

        for result in results:
            parsed = result["parsed"]
            print(f"\nCase {result['case_id']}:")

            if not result["fields"] and not parsed.unrecognized_lines:
                print("  No Provider/Psychometrist in comment")

            for field, (status, old, new) in result["fields"].items():
                if status == "updated":
                    updated_count += 1
                    if old:
                        print(f"  {field}: updated from {old} to {new}")
                    else:
                        print(f"  {field}: set to {new}")
                else:
                    print(f"  {field}: already {new}, no change")

            if parsed.service_changes:
                print(f"  (Test changes found, not applied yet): {parsed.service_changes}")
            if parsed.unrecognized_lines:
                flagged.append(result["case_id"])
                print(f"  NEEDS ATTENTION: {parsed.unrecognized_lines}")

        print(
            f"\n{len(results)} comment(s) processed, "
            f"{updated_count} field(s) updated, "
            f"{len(flagged)} flagged for attention."
        )
        if flagged:
            print("Flagged cases:", ", ".join(flagged))

    input("\nPress Enter to close...")