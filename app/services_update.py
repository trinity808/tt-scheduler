"""
Decides what one case's Services Requested cell should say, and whether it is
safe to change it at all.

Inputs: the case's stored original tests (or None if nothing was stored), the
changes in its comment, the cell's current text, and the Services lookup.
Output: a ServicesUpdate saying what to do.

The cell is never the starting point. The result is always the original tests
plus the comment's changes, worked out by service_changes.compute_services().
The cell is read for three things only: its descriptions, any line nothing
explains, and the safety checks below.

Refused, with nothing written or stored:
  - The cell has a line that can't be read, such as a note typed under a test.
    Writing over it would lose what someone typed.
  - Nothing was stored for the case and the cell has already been changed,
    meaning some line carries a marker. Adopting it as the original would
    make an applied change permanent.
  - Nothing was stored and the cell is empty, while the comment has changes.
    There is nothing to adopt.

When nothing was stored and the cell is clean (every line readable, no
markers), the cell IS the PDF's list, so it is adopted as the original and
returned in original_to_store for the caller to save.

A line in the cell that neither the original nor the comment accounts for is
kept, and the comment is flagged. A comment accounts for a test if it mentions
it anywhere, even to swap it away or back again, so an explicit change back is
respected. This happens when OA rewrites her comment from scratch, or types a
line into the cell by hand. The flag says which: a line carrying a marker is "from
an earlier change", and one without a marker was typed in. The cell's marker says what the
line was: an "(added)" line becomes an implied Add, and a "(changed from X)"
line becomes an implied Change of X, applied after the comment's own changes so
it keeps its place. If an implied change no longer fits, for example the
comment now swaps the same test for something else, it is dropped, and the flag
says so. Nothing leaves the cell without a message.

Descriptions come from the cell's own line for a code if it has one, then the
Services tab's Name, then nothing, so the PDF's wording is never replaced.

Whitespace is ignored when deciding whether the cell already matches, so a
cell that differs only in spacing is left alone.
"""

import dataclasses
from dataclasses import dataclass, field

from app.comment_parser import ServiceChange
from app.service_changes import change_label, compute_services
from app.services_cell import parse_services_cell, render_services_cell


@dataclass
class ServicesUpdate:
    status: str                      # "update", "unchanged", or "refused"
    text: str = None                 # the cell text to write, when status is "update"
    lines: list = field(default_factory=list)  # the final ServiceLine list
    original_to_store: list = None   # codes to save, when the cell was adopted
    flags: list = field(default_factory=list)  # messages for the Comments cell


def _refused(reason):
    return ServicesUpdate(
        status="refused",
        flags=[f"Services Requested  (not changed: {reason})"],
    )


def _squash(text):
    return " ".join((text or "").split())


def build_final_services(original_codes, changes, cell_text, lookup=None):
    """
    original_codes: the stored original tests, or None if nothing is stored.
    changes: ServiceChange objects from the comment, in order.
    cell_text: the Services Requested cell as it is now.
    lookup: a ServiceLookup, used only for descriptions. May be None.
    """
    changes = list(changes)
    parsed = parse_services_cell(cell_text)

    if parsed.problems:
        more = len(parsed.problems) - 1
        extra = f" (and {more} more)" if more else ""
        return _refused(
            f"the cell has a line that can't be read{extra}. {parsed.problems[0]}"
        )

    adopted = None
    if original_codes is None:
        if parsed.has_markers():
            return _refused(
                "this case's original tests weren't saved and the cell has "
                "already been changed, so it can't be updated safely"
            )
        if not parsed.lines:
            if not changes:
                return ServicesUpdate(status="unchanged")
            return _refused("this case has no tests recorded, so changes can't be applied")
        original_codes = parsed.codes()
        adopted = list(original_codes)
    else:
        original_codes = list(original_codes)

    computed = compute_services(original_codes, changes)

    # Cell lines that neither the original nor the comment accounts for.
    mentioned = {change.code for change in changes} | {
        change.replaces for change in changes if change.replaces
    }
    explained = set(original_codes) | set(computed.codes()) | mentioned
    implied = []
    typed_by_hand = set()  # ids of implied changes from lines with no marker
    for line in parsed.lines:
        if line.code in explained:
            continue
        explained.add(line.code)
        if line.marker == "changed":
            change = ServiceChange("change", line.code, replaces=line.changed_from)
        else:
            change = ServiceChange("add", line.code)
            if not line.marker:
                typed_by_hand.add(id(change))
        implied.append(change)

    final = compute_services(original_codes, changes + implied) if implied else computed

    flags = [
        skipped.message
        for skipped in final.skipped
        if not any(skipped.change is change for change in implied)
    ]
    for change in implied:
        skipped = next((s for s in final.skipped if s.change is change), None)
        if skipped is None and id(change) in typed_by_hand:
            flags.append(
                f"{change_label(change)}  (kept: the cell has this line, but it "
                "isn't in the PDF's list or this comment. Add it to the comment to "
                "keep it as a change, or delete it from the cell to remove it)"
            )
        elif skipped is None:
            flags.append(
                f"{change_label(change)}  (kept: the cell has it from an earlier "
                "change, but this comment no longer includes it. Put the line back "
                "in the comment to keep it, or delete it from the cell to remove it)"
            )
        else:
            flags.append(
                f"{change_label(change)}  (dropped: the cell has it from an earlier "
                f"change, but it doesn't fit this comment: {skipped.reason})"
            )

    in_cell = {line.code: line.description for line in parsed.lines if line.description}

    def describe(code):
        if code in in_cell:
            return in_cell[code]
        return (lookup.description(code) if lookup else None) or ""

    lines = [
        dataclasses.replace(line, description=describe(line.code))
        for line in final.lines
    ]
    text = render_services_cell(lines)
    unchanged = _squash(text) == _squash(cell_text)

    return ServicesUpdate(
        status="unchanged" if unchanged else "update",
        text=None if unchanged else text,
        lines=lines,
        original_to_store=adopted,
        flags=flags,
    )