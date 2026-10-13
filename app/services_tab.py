"""
Loads the Services tab from the tracking sheet and builds the lookup
that OA's test names are resolved through (see service_lookup.py).

The tab is edited by people, so it can be in a bad state: renamed or
deleted, emptied, or missing a header the lookup needs. None of that
should stop Provider and Psychometrist updates, so a tab that can't be
used returns no lookup plus a description of what's wrong, and the
caller carries on without it. Problems with individual rows don't stop
the lookup from loading either; they're reported alongside it.

Network and API errors are deliberately not caught here. If Sheets
can't be reached, the run should fail loudly rather than look like a
missing tab.
"""

from dataclasses import dataclass, field

from gspread.exceptions import WorksheetNotFound

from app.service_lookup import ServiceLookup, build_lookup


SERVICES_TAB = "Services"

_CONSEQUENCE = "so test names in comments can't be looked up"


@dataclass
class ServicesTab:
    lookup: ServiceLookup = None  # None if the tab couldn't be used
    problems: list = field(default_factory=list)
    worksheet: object = None      # the tab itself, None if it doesn't exist


def load_services_tab(spreadsheet):
    """
    Reads the Services tab from `spreadsheet` (anything with a
    worksheet(name) method) and returns a ServicesTab.

    - Tab usable: `lookup` is set. `problems` lists any bad rows or
      conflicting names found in it.
    - Tab missing, empty, or missing a required column: `lookup` is
      None and `problems` says what's wrong. `worksheet` is still set
      when the tab exists, so the problem can be flagged on it.
    """
    try:
        worksheet = spreadsheet.worksheet(SERVICES_TAB)
    except WorksheetNotFound:
        return ServicesTab(
            problems=[f"No tab named '{SERVICES_TAB}' was found, {_CONSEQUENCE}"]
        )

    rows = worksheet.get_all_values()
    if len(rows) < 2:  # nothing, or just a header row
        return ServicesTab(
            problems=[f"The {SERVICES_TAB} tab has no tests listed, {_CONSEQUENCE}"],
            worksheet=worksheet,
        )

    try:
        lookup = build_lookup(rows)
    except ValueError as error:  # a required column is missing
        return ServicesTab(
            problems=[f"{error}, {_CONSEQUENCE}"],
            worksheet=worksheet,
        )

    return ServicesTab(
        lookup=lookup,
        problems=list(lookup.problems),
        worksheet=worksheet,
    )