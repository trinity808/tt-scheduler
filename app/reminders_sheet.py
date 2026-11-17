"""
Reads what the reminders need from the tracking sheet, and writes nothing.

Three tabs, each read once with a single call (the same reasoning as the sweep:
Google's per-minute request limit, which a call per case would hit quickly):

  Ongoing         the open cases. For each: Case ID, Date & Time (the exam date is
                  read from it, see exam_date.py), Provider (the name, for the
                  greeting), and Provider Email, which the sweep fills in from the
                  Contacts tab. Rows with no Case ID are skipped, as in the sweep.
  Finished        the cases whose report is in. A case listed here gets no
                  reminders at all, since "in Finished" is what "report submitted"
                  means.
  Reminders Sent  one row per reminder already sent, with at least the columns
                  Case ID and Reminder (the key, such as DAY_5). It is how a second
                  run the same day knows not to send again. A missing tab just
                  means nothing has been sent yet. Later runs that send create it.

Columns are found by header name, ignoring capitals and extra spaces, so the
column order doesn't matter. A tab that is missing a column the reminders can't
do without raises ValueError saying which, because guessing is worse than
stopping: a log that can't be read could mean sending the same reminder twice,
and a Finished tab that can't be read could mean nagging someone whose report is
in. A missing Ongoing tab raises WorksheetNotFound, as it does for the sweep,
and so does a missing Finished tab, for the same reason. Network and API errors
are not caught, so a run that can't reach Google fails visibly.

The provider's address is carried on each Case so it can be sent to, but it is
kept out of repr() so it can't end up in a log line or a failed test's output.
"""

from dataclasses import dataclass, field

from gspread.exceptions import WorksheetNotFound


ONGOING_TAB = "Ongoing"
FINISHED_TAB = "Finished"
SENT_TAB = "Reminders Sent"

CASE_ID = "Case ID"
DATE_TIME = "Date & Time"
PROVIDER = "Provider"
PROVIDER_EMAIL = "Provider Email"   # the same header the sweep fills (contacts_tab.py)
SENT_REMINDER = "Reminder"

SENT_HEADERS = [CASE_ID, SENT_REMINDER, "Recipient", "Sent On", "Sent At"]


@dataclass
class Case:
    case_id: str
    row_number: int
    date_time_text: str
    provider_name: str
    provider_email: str = field(repr=False)


@dataclass
class ReminderData:
    cases: list = field(default_factory=list)       # open cases, in sheet order
    finished_ids: set = field(default_factory=set)  # Case IDs in the Finished tab
    sent: set = field(default_factory=set)          # (Case ID, reminder key) already sent
    log_found: bool = True                          # False if there is no Reminders Sent tab
    notes: list = field(default_factory=list)       # things worth saying about the sheet itself


def _normal(header):
    return " ".join(str(header).split()).casefold()


def _find_columns(headers, wanted):
    """{name: index} for each wanted header that exists (first one wins)."""
    positions = {}
    for index, header in enumerate(headers):
        positions.setdefault(_normal(header), index)
    return {name: positions[_normal(name)] for name in wanted if _normal(name) in positions}


def _cell(row, index):
    return row[index].strip() if index is not None and index < len(row) else ""


def _require(tab_name, headers, needed, consequence):
    columns = _find_columns(headers, needed)
    missing = [name for name in needed if name not in columns]
    if missing:
        names = ", ".join(f"'{name}'" for name in missing)
        raise ValueError(f"The {tab_name} tab has no {names} column, {consequence}")
    return columns


def _read_ongoing(worksheet, data):
    rows = worksheet.get_all_values()
    if not rows:
        raise ValueError("The Ongoing tab is empty: it has no header row")
    columns = _require(
        ONGOING_TAB,
        rows[0],
        [CASE_ID, DATE_TIME, PROVIDER_EMAIL],
        "so the reminders can't tell who to remind and when. Add it (the sweep "
        "fills Provider Email from the Contacts tab)",
    )
    provider_index = _find_columns(rows[0], [PROVIDER]).get(PROVIDER)

    seen = set()
    for row_number, row in enumerate(rows[1:], start=2):
        case_id = _cell(row, columns[CASE_ID])
        if not case_id:
            continue
        if case_id in seen:
            data.notes.append(
                f"Case {case_id} is listed more than once in Ongoing (row {row_number}). "
                "Only its first row is used."
            )
            continue
        seen.add(case_id)
        data.cases.append(
            Case(
                case_id=case_id,
                row_number=row_number,
                date_time_text=_cell(row, columns[DATE_TIME]),
                provider_name=_cell(row, provider_index),
                provider_email=_cell(row, columns[PROVIDER_EMAIL]),
            )
        )


def _read_finished(worksheet, data):
    rows = worksheet.get_all_values()
    if not rows:
        return  # nothing finished yet
    columns = _require(
        FINISHED_TAB, rows[0], [CASE_ID],
        "so the reminders can't tell which reports are in",
    )
    for row in rows[1:]:
        case_id = _cell(row, columns[CASE_ID])
        if case_id:
            data.finished_ids.add(case_id)


def _read_sent(spreadsheet, data):
    try:
        worksheet = spreadsheet.worksheet(SENT_TAB)
    except WorksheetNotFound:
        data.log_found = False
        return
    rows = worksheet.get_all_values()
    if not rows:
        return
    columns = _require(
        SENT_TAB, rows[0], [CASE_ID, SENT_REMINDER],
        "so the reminders can't tell what was already sent",
    )
    for row in rows[1:]:
        case_id = _cell(row, columns[CASE_ID])
        key = _cell(row, columns[SENT_REMINDER])
        if case_id and key:
            data.sent.add((case_id, key))


def read_reminder_data(spreadsheet):
    """
    Everything the reminders need, read from `spreadsheet` (anything with a
    worksheet(name) method). Raises ValueError for a tab missing a column it
    needs, and WorksheetNotFound for a missing Ongoing or Finished tab.
    """
    data = ReminderData()
    _read_ongoing(spreadsheet.worksheet(ONGOING_TAB), data)
    _read_finished(spreadsheet.worksheet(FINISHED_TAB), data)
    _read_sent(spreadsheet, data)
    return data