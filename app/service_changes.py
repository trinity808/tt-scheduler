"""
Works out a case's tests after OA's changes have been applied.

It starts from the case's original tests (the codes from the authorization
PDF, as stored when the case was added) and applies the comment's changes in
the order written, each one against the list built so far. It never looks at
the Services Requested cell, which is only output. That is what makes
rerunning safe: a comment that grows by one line replays the same earlier
changes and then the new one, so nothing already applied is ever "already
there", and nothing is applied twice.

Each change is checked before it is applied, against the list as built so far:
  - Add X: X must not already be on the case.
  - Change X to Y: X must be on the case, and Y must not be.
A change that doesn't fit is skipped, with the reason, and the rest still
apply. No change is ever half applied.

Each resulting line carries a marker saying how it differs from the PDF's
original list, judged on the net result and not on the path taken:
  - A test that was in the original list has no marker, wherever it ends up.
  - A test that isn't in the original list but holds the place of one that is
    is "changed from" that original, however many changes it took to get there.
  - Any other test is "added".

A changed test keeps the position it took over. An added test goes at the end.
Descriptions are left empty here. Choosing them is a later step.
"""

from dataclasses import dataclass, field

from app.services_cell import ServiceLine


def change_label(change):
    """A change as OA would write it, using full codes: "Add: X" or "Change: X to Y"."""
    if change.action == "add":
        return f"Add: {change.code}"
    return f"Change: {change.replaces} to {change.code}"


@dataclass
class SkippedChange:
    change: object  # a ServiceChange (see comment_parser.py)
    reason: str

    @property
    def message(self):
        """The change and why it was skipped, in the form used for flags."""
        return f"{change_label(self.change)}  (skipped: {self.reason})"


@dataclass
class ComputedServices:
    lines: list = field(default_factory=list)    # ServiceLine, descriptions empty
    skipped: list = field(default_factory=list)  # SkippedChange, in comment order

    def codes(self):
        return [line.code for line in self.lines]


@dataclass
class _Entry:
    code: str
    slot_of: str = None  # the original code whose place this entry holds, if any


def compute_services(original_codes, changes):
    """
    The case's final tests, given its original codes and the comment's changes
    (ServiceChange objects, in comment order). Neither argument is modified.
    """
    entries = [_Entry(code, slot_of=code) for code in original_codes]
    skipped = []

    def on_case(code):
        return any(entry.code == code for entry in entries)

    for change in changes:
        if change.action == "add":
            if on_case(change.code):
                skipped.append(
                    SkippedChange(change, f"{change.code} is already on this case")
                )
            else:
                entries.append(_Entry(change.code))

        elif change.action == "change":
            if not change.replaces:
                raise ValueError("a change needs the test it replaces")
            problems = []
            if not on_case(change.replaces):
                problems.append(f"{change.replaces} isn't on this case")
            if on_case(change.code):
                problems.append(f"{change.code} is already on this case")
            if problems:
                skipped.append(SkippedChange(change, "; ".join(problems)))
            else:
                next(e for e in entries if e.code == change.replaces).code = change.code

        else:
            raise ValueError(f"unknown change action: {change.action!r}")

    original = set(original_codes)
    lines = []
    for entry in entries:
        if entry.code in original:
            lines.append(ServiceLine(entry.code))
        elif entry.slot_of is not None:
            lines.append(ServiceLine(entry.code, marker="changed", changed_from=entry.slot_of))
        else:
            lines.append(ServiceLine(entry.code, marker="added"))

    return ComputedServices(lines=lines, skipped=skipped)