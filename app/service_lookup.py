"""
Lookup table for the services OA refers to in comments.

Built from the rows of the Services tab in the Google Sheet (columns:
Code, Aliases, Name, ...). Turns whatever OA types, like "wms",
"WISC-V", or "Psychiatric Exam", into the canonical service code used
in the authorization PDFs, like "96101-WMS-IV".

Each row's code answers to:
  - the full code itself ("96101-WISC-V")
  - its abbreviation, derived automatically by dropping the five digits
    and hyphen ("WISC-V")
  - any extra names in the Aliases column, comma-separated ("WISC")

Typed text and keys are both normalized (uppercase, letters and
digits only) before comparing, so "wisc v", "WISC-V" and "wiscv" all
match.

The Services tab is edited by people, so a bad row shouldn't break
everything:
  - A row whose code isn't a valid service code is skipped and
    reported in `problems`.
  - A key that points to more than one code (e.g. an alias that
    collides with another code's abbreviation) is left unusable,
    reported in `problems`, and recorded in `ambiguous`, rather than
    guessed. Every other key keeps working.
"""

import difflib
import re
from dataclasses import dataclass, field

from app.service_format import SERVICE_CODE_PATTERN


_VALID_CODE = re.compile(r"^" + SERVICE_CODE_PATTERN + r"$")
_LEADING_DIGITS = re.compile(r"^\d{5}-?")


def normalize_key(text):
    """Uppercase, letters and digits only: "wisc-v " -> "WISCV"."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


@dataclass
class ServiceLookup:
    keys: dict = field(default_factory=dict)       # normalized key -> code
    display: dict = field(default_factory=dict)    # normalized key -> key as written
    ambiguous: dict = field(default_factory=dict)  # normalized key -> sorted codes
    problems: list = field(default_factory=list)   # messages about the table itself

    def resolve(self, text):
        """The code for what was typed, or None if unknown or ambiguous."""
        return self.keys.get(normalize_key(text))

    def is_ambiguous(self, text):
        return normalize_key(text) in self.ambiguous

    def suggest(self, text):
        """The closest known key, as written in the table, or None."""
        match = difflib.get_close_matches(
            normalize_key(text), list(self.keys), n=1, cutoff=0.6
        )
        return self.display[match[0]] if match else None


def build_lookup(rows):
    """
    Builds a ServiceLookup from sheet rows, header row first. Columns
    are found by header name, so their order doesn't matter.
    """
    headers = [h.strip() for h in rows[0]]
    for required in ("Code", "Aliases"):
        if required not in headers:
            raise ValueError(f"Services tab is missing the '{required}' column")
    code_idx = headers.index("Code")
    aliases_idx = headers.index("Aliases")

    lookup = ServiceLookup()
    candidates = {}  # normalized key -> set of codes

    for row_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue

        code = row[code_idx].strip() if code_idx < len(row) else ""
        if not _VALID_CODE.match(code):
            lookup.problems.append(
                f"Row {row_number}: '{code}' isn't a valid service code "
                "(five digits, e.g. 96101-WISC-V), so it was skipped"
            )
            continue

        names = [code, _LEADING_DIGITS.sub("", code)]
        if aliases_idx < len(row):
            names += row[aliases_idx].split(",")

        for name in names:
            name = name.strip()
            key = normalize_key(name)
            if not key:
                continue
            candidates.setdefault(key, set()).add(code)
            lookup.display.setdefault(key, name)

    for key, codes in candidates.items():
        if len(codes) == 1:
            lookup.keys[key] = next(iter(codes))
        else:
            lookup.ambiguous[key] = sorted(codes)
            lookup.problems.append(
                f"'{lookup.display[key]}' matches more than one code "
                f"({', '.join(sorted(codes))}), so it won't be accepted "
                "until the Services tab is fixed"
            )

    return lookup