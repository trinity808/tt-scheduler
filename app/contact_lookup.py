"""
Lookup for the people the reminders write to: providers and psychometrists.

Built from the rows of the Contacts tab in the Google Sheet. Its columns, found
by header name so their order doesn't matter, are:
  Name   how the person is listed, for example "Jane Smith" or "Dr. Jane Smith"
  Email  where to write to them
  Role   optional: Provider or Psychometrist. A row with no role counts as both,
         so a provider "Smith" and a student "Smith" can't be confused when roles
         are filled in.

OA types names in comments, so a name is matched the way a person would read it,
after ignoring case, accents, punctuation, a leading "Dr", and middle names:
  "Dr Smith", "Smith"           the last name alone, if only one person has it
  "Dr J Smith", "J. Smith"      an initial and the last name
  "Dr Jane Smith", "Smith, Jane"  the whole name
A name that fits two people is never guessed. The lookup says which people it
could be. A single letter ("Dr S") is too short to match anyone.

The tab is edited by people, so a bad row doesn't break everything: a row with no
name, no valid email or an unknown role is skipped and reported in `problems`.
Email addresses are never put in a problem message, since those end up on the
sheet and in logs.
"""

import re
import unicodedata
from dataclasses import dataclass, field


ROLES = ("provider", "psychometrist")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TITLE = re.compile(r"^\s*(dr|doctor)\b\.?\s*")


def name_words(text):
    """
    A person's name as lowercase words, without accents, punctuation, or a leading
    "Dr": "Dr. J. O'Brien" -> ("j", "obrien"), "Smith, Jane" -> ("jane", "smith").
    """
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = _TITLE.sub("", text)
    if "," in text:
        last, _, rest = text.partition(",")
        text = f"{rest} {last}"
    text = re.sub(r"[.'\u2019\-]", "", text)
    return tuple(re.sub(r"[^a-z0-9\s]", " ", text).split())


@dataclass
class Contact:
    name: str       # as written in the tab
    email: str
    role: str       # "provider", "psychometrist", or "" for both
    words: tuple


@dataclass
class ContactMatch:
    status: str                                    # "found", "unknown", "ambiguous" or "too_short"
    email: str = ""                                # for "found"
    name: str = ""                                 # for "found": the name as written in the tab
    options: list = field(default_factory=list)    # for "ambiguous": the names it could be


def _same_first_name(typed, listed):
    return (
        typed == listed
        or (len(typed) == 1 and listed.startswith(typed))
        or (len(listed) == 1 and typed.startswith(listed))
    )


@dataclass
class ContactLookup:
    contacts: list = field(default_factory=list)
    problems: list = field(default_factory=list)

    def find(self, text, role=None):
        """
        Who `text` (as OA wrote it) is. `role` is "provider" or "psychometrist" to
        look only at that kind of person, or None for everyone.
        """
        words = name_words(text)
        if not words:
            return ContactMatch("unknown")

        pool = [c for c in self.contacts if role is None or c.role in ("", role)]

        whole = [c for c in pool if c.words == words]
        if whole:
            return self._decide(whole)

        last = words[-1]
        if len(last) == 1:
            return ContactMatch("too_short")
        first = words[0] if len(words) > 1 else None
        partial = [
            c for c in pool
            if c.words[-1] == last
            and (first is None or len(c.words) == 1 or _same_first_name(first, c.words[0]))
        ]
        if not partial:
            return ContactMatch("unknown")
        return self._decide(partial)

    @staticmethod
    def _decide(matches):
        if len(matches) == 1:
            return ContactMatch("found", email=matches[0].email, name=matches[0].name)
        return ContactMatch("ambiguous", options=[c.name for c in matches])


def build_contact_lookup(rows):
    """
    Builds a ContactLookup from sheet rows, header row first. Raises ValueError if
    the Name or Email column is missing.
    """
    headers = [h.strip().casefold() for h in rows[0]]
    for required in ("name", "email"):
        if required not in headers:
            raise ValueError(f"Contacts tab is missing the '{required.title()}' column")
    name_idx, email_idx = headers.index("name"), headers.index("email")
    role_idx = headers.index("role") if "role" in headers else None

    lookup = ContactLookup()
    for row_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue
        cell = lambda idx: row[idx].strip() if idx is not None and idx < len(row) else ""
        name, email, role_text = cell(name_idx), cell(email_idx), cell(role_idx)

        words = name_words(name)
        if not words:
            lookup.problems.append(f"Row {row_number}: there is no name, so it was skipped")
            continue
        if not _EMAIL.match(email):
            lookup.problems.append(
                f"Row {row_number}: '{name}' has no valid email address, so it was skipped"
            )
            continue
        role = role_text.casefold()
        if role and role not in ROLES:
            lookup.problems.append(
                f"Row {row_number}: the role '{role_text}' isn't Provider or Psychometrist, "
                f"so '{name}' was skipped"
            )
            continue
        lookup.contacts.append(Contact(name, email, role, words))

    for i, first in enumerate(lookup.contacts):
        for second in lookup.contacts[i + 1:]:
            if first.words == second.words and (not first.role or not second.role or first.role == second.role):
                lookup.problems.append(
                    f"'{first.name}' is listed more than once, so it won't be accepted "
                    "until the Contacts tab is fixed"
                )
    return lookup