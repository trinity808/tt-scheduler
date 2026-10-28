"""
Reads and writes the text of the Services Requested cell.

The extractor writes a case's tests into that cell as numbered lines with a
separator line between them:

    1. 96101-WISC-V - Wechsler Intelligence Scale for Children - Ages 6 to 16 years
    ____________________
    2. 90791 - Psychiatric Exam

When test changes are applied, a line may end with a marker saying how it
differs from the PDF's original list:

    3. 96130-CARS-2 - Childhood Autism Rating Scale (added)
    1. 96101-WAIS-IV - Wechsler Adult Intelligence Scale (changed from 96101-WISC-V)

This module turns that text into ServiceLine objects and back. It reads what
the extractor writes and what it writes itself, and reports anything else
instead of guessing, so a caller can refuse to act on a cell it doesn't
understand.

Reading is forgiving about layout and strict about content:
  - Each line is one entry, and a run of whitespace within a line counts as
    one space.
  - The "1. " numbering is optional, since lines are always renumbered when
    written.
  - Any line made only of underscores (three or more) is a separator, and
    the separators themselves are optional.
  - Every entry must start with a valid service code. A line that doesn't,
    such as a note typed under a test, is reported in `problems` and left
    out of `lines`. It is never attached to the line above, because that
    would hide a test inside another test's description.

Only the exact markers "(added)" and "(changed from <code>)" at the end of an
entry count. A description that happens to end in parentheses, such as
"Review of Records (Psychological)", is just a description.
"""

import re
from dataclasses import dataclass, field

from app.service_format import SERVICE_CODE_PATTERN, SERVICE_SEPARATOR


_SEPARATOR_LINE = re.compile(r"^_{3,}$")
_MARKER = re.compile(
    r"\s+\((?:(added)|changed from (" + SERVICE_CODE_PATTERN + r"))\)$"
)
_ENTRY = re.compile(r"^(?:\d+\.\s+)?(" + SERVICE_CODE_PATTERN + r")(?:\s+-\s+(.*))?$")

_MARKERS = ("", "added", "changed")


@dataclass
class ServiceLine:
    code: str
    description: str = ""   # the text after "code - ", may be empty
    marker: str = ""        # "", "added", or "changed"
    changed_from: str = ""  # the code this line replaced, when marker is "changed"

    def __post_init__(self):
        self.code = self.code.strip()
        self.description = " ".join(self.description.split())
        if self.marker not in _MARKERS:
            raise ValueError(f"marker must be one of {_MARKERS}, not {self.marker!r}")
        if self.marker == "changed" and not self.changed_from:
            raise ValueError("a 'changed' line needs the code it changed from")
        if self.marker != "changed" and self.changed_from:
            raise ValueError("only a 'changed' line can say what it changed from")


@dataclass
class ParsedCell:
    lines: list = field(default_factory=list)     # ServiceLine, in order
    problems: list = field(default_factory=list)  # entries that couldn't be read

    def codes(self):
        return [line.code for line in self.lines]

    def has_markers(self):
        return any(line.marker for line in self.lines)


def _split_entries(text):
    """The cell's entries: one per line, each with its whitespace collapsed."""
    entries = []
    for raw in text.splitlines():
        entry = " ".join(raw.split())
        if entry and not _SEPARATOR_LINE.match(entry):
            entries.append(entry)
    return entries


def _parse_entry(entry):
    marker, changed_from = "", ""
    match = _MARKER.search(entry)
    if match:
        entry = entry[:match.start()]
        if match.group(1):
            marker = "added"
        else:
            marker, changed_from = "changed", match.group(2)

    match = _ENTRY.match(entry)
    if not match:
        return None
    return ServiceLine(
        code=match.group(1),
        description=match.group(2) or "",
        marker=marker,
        changed_from=changed_from,
    )


def parse_services_cell(text):
    """Reads a Services Requested cell. Empty or blank text gives no lines."""
    parsed = ParsedCell()
    for number, entry in enumerate(_split_entries(text or ""), start=1):
        line = _parse_entry(entry)
        if line is None:
            parsed.problems.append(
                f"Entry {number} couldn't be read as a test line: {entry}"
            )
        else:
            parsed.lines.append(line)
    return parsed


def render_services_cell(lines):
    """
    The cell text for a list of ServiceLine: numbered from 1, a separator row
    between entries and none at the end. A line with no description shows just
    its code.
    """
    entries = []
    for number, line in enumerate(lines, start=1):
        text = f"{number}. {line.code}"
        if line.description:
            text += f" - {line.description}"
        if line.marker == "added":
            text += " (added)"
        elif line.marker == "changed":
            text += f" (changed from {line.changed_from})"
        entries.append(text)
    return f"\n{SERVICE_SEPARATOR}\n".join(entries)