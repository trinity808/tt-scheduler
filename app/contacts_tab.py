"""
Loads the Contacts tab from the tracking sheet and builds the lookup that turns the
provider and psychometrist names in OA's comments into email addresses (see
contact_lookup.py).

Like the Services tab, it is edited by people, so it can be in a bad state: renamed
or deleted, empty, or missing a header. None of that should stop the sweep. A tab
that can't be used returns no lookup plus a description of what's wrong, and the
caller carries on without it. Problems with individual rows don't stop the lookup
from loading either; they're reported alongside it.

Network and API errors are deliberately not caught here. If Sheets can't be
reached, the run should fail loudly rather than look like a missing tab.
"""

from dataclasses import dataclass, field

from gspread.exceptions import WorksheetNotFound

from app.contact_lookup import ContactLookup, build_contact_lookup


CONTACTS_TAB = "Contacts"

# The columns on the Ongoing tab that the sweep fills in. The sweep only touches
# emails when these headers exist there, so adding them is what switches it on.
PROVIDER_EMAIL_HEADER = "Provider Email"
PSYCHOMETRIST_EMAIL_HEADER = "Psychometrist Email"

_CONSEQUENCE = "so provider and psychometrist emails can't be filled in"


@dataclass
class ContactsTab:
    lookup: ContactLookup = None   # None if the tab couldn't be used
    problems: list = field(default_factory=list)
    worksheet: object = None       # the tab itself, None if it doesn't exist


def load_contacts_tab(spreadsheet):
    """
    Reads the Contacts tab from `spreadsheet` (anything with a worksheet(name) method)
    and returns a ContactsTab. `lookup` is None, with `problems` saying why, if the tab
    is missing, has no people listed, or lacks the Name or Email column.
    """
    try:
        worksheet = spreadsheet.worksheet(CONTACTS_TAB)
    except WorksheetNotFound:
        return ContactsTab(problems=[f"No tab named '{CONTACTS_TAB}' was found, {_CONSEQUENCE}"])

    rows = worksheet.get_all_values()
    if len(rows) < 2:
        return ContactsTab(
            problems=[f"The {CONTACTS_TAB} tab has no people listed, {_CONSEQUENCE}"],
            worksheet=worksheet,
        )
    try:
        lookup = build_contact_lookup(rows)
    except ValueError as error:
        return ContactsTab(problems=[f"{error}, {_CONSEQUENCE}"], worksheet=worksheet)
    return ContactsTab(lookup=lookup, problems=list(lookup.problems), worksheet=worksheet)


# --- Flagging problems on the tab itself -----------------------------

NOTE_CELL = "A1"
NOTE_HEADER = "Contacts tab needs attention:"
NOTE_FOOTER = "This note clears itself once these are fixed."


def _note_text(problems):
    return "\n".join([NOTE_HEADER] + [f"- {p}" for p in problems] + [NOTE_FOOTER])


def flag_contacts_tab(worksheet, problems):
    """
    Puts a note on the Contacts tab's header cell listing `problems`, and removes it
    once there are none. Only a note this function wrote (recognized by its opening
    line) is ever replaced or cleared. A note a person wrote is left alone.

    Returns "flagged", "cleared", "unchanged", "skipped" (there are problems but A1
    holds someone else's note), or "no_tab".
    """
    if worksheet is None:
        return "no_tab"
    current = worksheet.get_note(NOTE_CELL)
    is_ours = current.startswith(NOTE_HEADER)
    if problems:
        desired = _note_text(problems)
        if current.strip() == desired.strip():
            return "unchanged"
        if current and not is_ours:
            return "skipped"
        worksheet.update_note(NOTE_CELL, desired)
        return "flagged"
    if is_ours:
        worksheet.clear_note(NOTE_CELL)
        return "cleared"
    return "unchanged"