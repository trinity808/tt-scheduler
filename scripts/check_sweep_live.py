"""
Live check of the sweep, run through the real Google Sheets API.

It works on scratch tabs that it creates in the sandbox sheet, and deletes them
at the end. It never touches a real tab: the sweep is handed a stand-in
spreadsheet that maps "Ongoing" and "Services" to scratch tabs, and refuses any
other name. Everything the sweep reads, and writes, is made up here.

Every run starts from nothing: it removes scratch tabs left by an earlier
failed run, builds fresh ones, and deletes them at the end.

The sweep is run from a temporary folder, so its lock file and its logs never
touch the project folder.

Google limits a user to 60 read requests a minute, and this script plus the
sweep it runs would spend that quickly. So every request goes through a pacer
that keeps to 45 a minute, and a request refused for being over the limit is
tried again after a pause. A full run takes a few minutes for that reason.

STEPS SO FAR: set-up and clean-up, the stand-in spreadsheet, the scratch
Services tab, scenario 1 (previewed, applied, applied again), scenarios 2 and 3,
which edit the comment and sweep again, scenarios 4, 5 and 10, which are about
flags and refusals, scenarios 11 to 14, where the stored original is missing or
belongs to another row, scenarios 6 to 9, where a comment is rewritten or cleared
or a line is typed into the cell, scenarios 15 and 16, where rows move and a case
goes to Finished, and scenario 19, where a move is interrupted and run again.
Each batch shares sweeps, on a row each.

Run from the project root:
    python -m scripts.check_sweep_live                 everything
    python -m scripts.check_sweep_live --list          the groups of scenarios
    python -m scripts.check_sweep_live --only 6,15     just these scenarios, with their groups
    python -m scripts.check_sweep_live --only moves    just one group

Each group of scenarios has a scratch tab of its own, so a group can be run
alone. The scenarios inside a group share their sweeps, so they run together.
"""

import argparse
import collections
import contextlib
import functools
import io
import logging
import os
import re
import sys
import tempfile
import time
from types import SimpleNamespace

import gspread
from gspread.exceptions import APIError
from gspread.utils import rowcol_to_a1

from app.apply_comment_updates import ONGOING_TAB, main as sweep_main
from app.case_metadata import read_all_case_tests, set_case_tests
from app.services_cell import ServiceLine, render_services_cell
from app.services_tab import SERVICES_TAB, load_services_tab
from app.sheet_case_mover import move_case_to_finished
from app.sheet_client import get_client
from app.sheets_metadata_client import SheetsMetadataClient


PREFIX = "SweepCheck "
SCRATCH_SERVICES = PREFIX + "Services"

ONGOING_HEADERS = [
    "Case ID", "Patient Name", "Provider", "Psychometrist",
    "Services Requested", "Price", "Comments",
]

FINISHED_HEADERS = [
    "Case ID", "Patient Name", "Provider", "Psychometrist", "Price",
    "Payment Status", "Date Completed",
]

# The scratch Services tab. Fixed, so that what the sweep resolves never
# depends on what the sandbox's own Services tab holds. Names are made up, so
# a line in a cell can only have come from this table.
SERVICES_HEADERS = ["Code", "Aliases", "Name"]
SERVICES = [
    ["96101-WISC-V", "WISC", "Scratch name for WISC-V"],
    ["90791", "Psychiatric Exam", "Scratch name for 90791"],
    ["96130-AUTISM", "", "Scratch name for AUTISM"],
    ["96101-WAIS-IV", "WAIS", "Scratch name for WAIS-IV"],
    ["96101-WMS-IV", "WMS", "Scratch name for WMS-IV"],
    ["96130-CARS-2", "CARS", "Scratch name for CARS-2"],
    ["96101-WIAT-III", "WIAT", "Scratch name for WIAT-III"],
    ["96101-WPPSI-IV", "WPPSI", "Scratch name for WPPSI-IV"],
]
NAMES = {code: name for code, _, name in SERVICES}

# How a case's cell worded a test when the case was created, where that differs
# from the Services tab's Name. (A real case's cell is written from its
# authorization PDF. Here the script writes it.) A test that is changed away
# and back must take its text from the Name, because the original line is gone.
ORIGINAL_WORDING = {"96130-AUTISM": "Autism evaluation, as the cell first worded it"}

# What a person types, and the code it must become.
TYPED = {
    "wms": "96101-WMS-IV", "wisc": "96101-WISC-V", "wais": "96101-WAIS-IV",
    "autism": "96130-AUTISM", "cars": "96130-CARS-2", "wiat": "96101-WIAT-III",
    "wppsi": "96101-WPPSI-IV", "psychiatric exam": "90791",
}

ORIGINAL_TESTS = ["96101-WISC-V", "90791", "96130-AUTISM"]

# Scenarios 1 to 3 are a chain on one row: (scenario number, Case ID, the original
# tests, the comment).
CASES = [
    (1, "S01", ORIGINAL_TESTS, "Provider: Dr. Doe\nPsychometrist: J. Smith\nAdd: wms"),
]

# Scenarios that don't depend on each other, each on a row below the chain:
# (scenario number, Case ID, the original tests, the comment, a line typed into
# the cell by hand).
BATCH = [
    (4, "S04", ORIGINAL_TESTS, "Add: wisc\nChange: wppsi to wais\nAdd: wiat", ""),
    (5, "S05", ORIGINAL_TESTS, "Provider: Dr. Doe\nAdd: wisk\nAdd: cars", ""),
    (10, "S10", ORIGINAL_TESTS, "Provider: Dr. Doe\nPsychometrist: J. Smith\nAdd: cars", "add wms"),
]

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    line = f"  [{'OK' if ok else 'FAILED'}] {label}"
    if not ok and detail:
        line += f"  ->  {detail}"
    print(line)


# --- Keeping to Google's request limit ---------------------------------


def say_on_screen(text):
    """
    The sweep's output is captured while it runs, so a pause is shown on the real
    screen. A message must never stop a long run, so if the real screen can't be
    written to (for example under a test runner on Windows), it goes to stderr.
    """
    for stream in (sys.__stdout__, sys.stderr):
        if stream is None:
            continue
        try:
            print(text, file=stream, flush=True)
            return
        except (OSError, ValueError):
            continue


class Pacer:
    """Waits, when it has to, so that no more than `limit` requests go out in any `window` seconds."""

    def __init__(self, limit=45, window=60.0, clock=time.monotonic, sleep=time.sleep, say=say_on_screen):
        self.limit, self.window = limit, window
        self.clock, self.sleep, self.say = clock, sleep, say
        self.times = collections.deque()

    def _forget_old(self):
        now = self.clock()
        while self.times and now - self.times[0] >= self.window:
            self.times.popleft()

    def wait(self):
        self._forget_old()
        if len(self.times) >= self.limit:
            delay = self.window - (self.clock() - self.times[0]) + 0.5
            if delay > 3:
                self.say(f"  (pausing {delay:.0f} s to stay under Google's per-minute request limit)")
            self.sleep(delay)
            self._forget_old()
        self.times.append(self.clock())


def is_over_the_limit(error):
    return getattr(getattr(error, "response", None), "status_code", None) == 429


def install_pacing(client, pacer, retries=3, pause=30.0, sleep=time.sleep, say=say_on_screen):
    """
    Routes every request the gspread client makes through the pacer, and tries
    a request again, after a pause, if Google refuses it for being over the limit.
    """
    original = client.request

    @functools.wraps(original)
    def paced(*args, **kwargs):
        for attempt in range(retries + 1):
            pacer.wait()
            try:
                return original(*args, **kwargs)
            except APIError as error:
                if not is_over_the_limit(error) or attempt == retries:
                    raise
                say(f"  (Google's request limit was hit; waiting {pause:.0f} s, then trying again)")
                sleep(pause)

    client.request = paced


class ScratchSpreadsheet:
    """
    Stands in for the spreadsheet the sweep is given. Only the names in `tabs`
    can be looked up, and each gives its scratch tab. Any other name raises
    WorksheetNotFound, even if a real tab has that name, and a method this
    class lacks fails instead of reaching the real sheet.
    """

    def __init__(self, tabs):
        self._tabs = dict(tabs)

    def worksheet(self, title):
        if title not in self._tabs:
            raise gspread.WorksheetNotFound(title)
        return self._tabs[title]


def remove_leftovers(sh):
    """Deletes scratch tabs left behind by an earlier run that failed."""
    for ws in sh.worksheets():
        if ws.title.startswith(PREFIX):
            sh.del_worksheet(ws)
            print(f"  (removed a leftover tab from an earlier run: {ws.title})")


def is_refused(stand_in, name):
    try:
        stand_in.worksheet(name)
    except gspread.WorksheetNotFound:
        return True
    return False


def make_tab(sh, created, title, headers, rows=20):
    """A new scratch tab with its header row. It is deleted at the end with the others."""
    ws = sh.add_worksheet(title=title, rows=rows, cols=len(headers))
    created.append(ws)
    ws.update(range_name=f"A1:{rowcol_to_a1(1, len(headers))}", values=[headers])
    return ws


def make_ongoing_tab(sh, created, title):
    return make_tab(sh, created, title, ONGOING_HEADERS)


def strip_number(line):
    """'4. 96101-WIAT-III - text' -> '96101-WIAT-III - text'."""
    return line.split(". ", 1)[1] if ". " in line else line


def write_table(ws, rows):
    end = rowcol_to_a1(len(rows), max(len(row) for row in rows))
    ws.update(range_name=f"A1:{end}", values=rows)


def services_cell(codes):
    return render_services_cell(
        [ServiceLine(code, ORIGINAL_WORDING.get(code, NAMES[code])) for code in codes]
    )


def edit_comment(ws, row_number, comment):
    cell = rowcol_to_a1(row_number, ONGOING_HEADERS.index("Comments") + 1)
    ws.update(range_name=cell, values=[[comment]])


def test_lines(cell_text):
    """The tests in a Services Requested cell: its lines, minus the separators."""
    return [line for line in cell_text.split("\n") if line.strip("_ ")]


def build_cell(separator, tests):
    return ("\n" + separator + "\n").join(tests)


def stored_tests(client, row_number):
    entry = read_all_case_tests(client).by_row.get(row_number)
    return entry.tests if entry else None


def row_of(table, row_number):
    """One row of the tab's values, as {header: value}."""
    row = list(table[row_number - 1]) if row_number <= len(table) else []
    row += [""] * (len(ONGOING_HEADERS) - len(row))
    return dict(zip(ONGOING_HEADERS, row))


def look(ws, services_ws, client, row_number=2):
    """
    One look at the state after a step, taking as few requests as possible
    (each one counts against Google's limit): the tab, the Comments cell's note,
    the Services tab's note, and the stored original.
    """
    table = ws.get_all_values()
    return {
        "table": table,
        "row": row_of(table, row_number),
        "comments_note": ws.get_note(rowcol_to_a1(row_number, ONGOING_HEADERS.index("Comments") + 1)),
        "services_note": services_ws.get_note("A1"),
        "stored": stored_tests(client, row_number),
    }


def case_block(report, case_id):
    """The part of a report about one case: from its 'Case <id>:' line to the next case's."""
    block, inside = [], False
    for line in report.splitlines():
        if line.startswith("Case "):
            inside = line.strip() == f"Case {case_id}:"
        if inside:
            block.append(line)
    return "\n".join(block)


def all_stored(client):
    """Every stored original, as {row number: tests}."""
    return {row: entry.tests for row, entry in read_all_case_tests(client).by_row.items()}


def stored_with_ids(client):
    """Every stored original, as {row number: (the Case ID it was stored under, its tests)}."""
    return {row: (entry.case_id, entry.tests) for row, entry in read_all_case_tests(client).by_row.items()}


def rows_by_id(table):
    """{Case ID: row number} for every row of the tab that has a Case ID."""
    return {row[0]: n for n, row in enumerate(table, start=1) if n > 1 and row and row[0].strip()}


def look_at_moved(ws, client):
    """The tab, the Comments note of every case on it, and every stored original with its Case ID."""
    table = ws.get_all_values()
    column = ONGOING_HEADERS.index("Comments") + 1
    where = rows_by_id(table)
    return {
        "table": table,
        "where": where,
        "notes": {cid: ws.get_note(rowcol_to_a1(row, column)) for cid, row in where.items()},
        "stored": stored_with_ids(client),
    }


def look_at_rows(ws, client, rows):
    """The tab, the Comments note of each of `rows`, and every stored original."""
    column = ONGOING_HEADERS.index("Comments") + 1
    return {
        "table": ws.get_all_values(),
        "notes": {row: ws.get_note(rowcol_to_a1(row, column)) for row in rows},
        "stored": all_stored(client),
    }


def reset_logging():
    """The sweep's log handlers outlive a run. Close them, so each run logs into its own folder."""
    logger = logging.getLogger("trinity_scheduler")
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def run_sweep_command(stand_in, argv, work_folder):
    """
    Runs the sweep's real command line on the stand-in spreadsheet, from
    `work_folder`, where its log and its lock file go. Returns what it printed.
    """
    reset_logging()
    config = {"health_check": {"log_folder": work_folder, "email_notifications_enabled": False}}
    out = io.StringIO()
    here = os.getcwd()
    os.chdir(work_folder)
    try:
        with contextlib.redirect_stdout(out):
            sweep_main(argv, spreadsheet=stand_in, pause=False, config=config)
    finally:
        os.chdir(here)
        reset_logging()
    return out.getvalue()


def apply_live(stand_in, work):
    """Runs the sweep live, shows its report, and returns it."""
    report = run_sweep_command(stand_in, ["--live"], work)
    show(report)
    return report


def show(report):
    print("  --- the report, for reference ---")
    for line in report.splitlines():
        print(f"  | {line}")
    print("  --- end of report ---")


def read_log(work_folder):
    path = os.path.join(work_folder, "sweep.log")
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""




class FailsToDelete:
    """
    A worksheet whose delete always fails, and which otherwise is the worksheet it
    wraps. Handed to the mover, it interrupts a move after the case has been written
    to Finished and before it leaves Ongoing, which is the state a retry has to handle.
    """

    def __init__(self, worksheet):
        self._worksheet = worksheet

    def __getattr__(self, name):
        return getattr(self._worksheet, name)

    def delete_rows(self, *args, **kwargs):
        raise RuntimeError("API error")


class LogCatcher(logging.Handler):
    """Keeps the warnings that one logger emits, until it is closed."""

    def __init__(self, logger_name):
        super().__init__(logging.WARNING)
        self.messages = []
        self._logger = logging.getLogger(logger_name)
        self._logger.addHandler(self)

    def emit(self, record):
        self.messages.append(record.getMessage())

    def close(self):
        self._logger.removeHandler(self)
        super().close()


def build_env(sh, created, services_ws, work):
    """What every group needs: the scratch spreadsheet, and the lines the cells are expected to hold."""
    plain = services_cell(ORIGINAL_TESTS)
    original_lines = test_lines(plain)
    wisc_line, psych_line, autism_original_line = original_lines
    return SimpleNamespace(
        sh=sh, created=created, services_ws=services_ws, work=work,
        lock_path=os.path.join(work, "sweep.lock"),
        plain=plain, separator=plain.split("\n")[1], original_lines=original_lines,
        wisc_line=wisc_line, psych_line=psych_line, autism_original_line=autism_original_line,
        wms_line=f"4. 96101-WMS-IV - {NAMES['96101-WMS-IV']} (added)",
        wiat4=f"4. 96101-WIAT-III - {NAMES['96101-WIAT-III']} (added)",
        cars4=f"4. 96130-CARS-2 - {NAMES['96130-CARS-2']} (added)",
        cars5=f"5. 96130-CARS-2 - {NAMES['96130-CARS-2']} (added)",
        wais_swapped=f"1. 96101-WAIS-IV - {NAMES['96101-WAIS-IV']} (changed from 96101-WISC-V)",
    )


def check_setup(env):
    """The stand-in spreadsheet, and the scratch Services tab read by the sweep's own code."""
    sh, created, services_ws, work = env.sh, env.created, env.services_ws, env.work
    lock_path, plain, separator = env.lock_path, env.plain, env.separator
    original_lines = env.original_lines
    wisc_line, psych_line, autism_original_line = env.wisc_line, env.psych_line, env.autism_original_line
    wms_line, wiat4, cars4, cars5, wais_swapped = env.wms_line, env.wiat4, env.cars4, env.cars5, env.wais_swapped
    ws = make_ongoing_tab(sh, created, PREFIX + "Setup")

    print("\nThe stand-in spreadsheet hands the sweep only scratch tabs")
    stand_in = ScratchSpreadsheet({ONGOING_TAB: ws, SERVICES_TAB: services_ws})
    check("asking for 'Ongoing' gives the scratch tab",
          stand_in.worksheet(ONGOING_TAB).id == ws.id)
    check("asking for 'Services' gives the scratch tab",
          stand_in.worksheet(SERVICES_TAB).id == services_ws.id)
    for name in (ONGOING_TAB, SERVICES_TAB):
        try:
            real = sh.worksheet(name)
        except gspread.WorksheetNotFound:
            print(f"  (no real tab named '{name}' to confuse it with)")
            continue
        check(f"that is not the real '{name}' tab", stand_in.worksheet(name).id != real.id)
    for name in ("Finished", "Some Other Tab"):
        check(f"a tab it wasn't given ('{name}') is refused", is_refused(stand_in, name))

    print("\nThe scratch Services tab, read by the sweep's own code")
    loaded = load_services_tab(stand_in)
    check("it loads, with no problems", loaded.lookup is not None and loaded.problems == [],
          f"problems: {loaded.problems}")
    if loaded.lookup is not None:
        wrong = {text: loaded.lookup.resolve(text) for text, code in TYPED.items()
                 if loaded.lookup.resolve(text) != code}
        check("every name the scenarios type resolves to its code", not wrong, f"wrong: {wrong}")
        check("an unknown name is not guessed, and a close one is suggested",
              loaded.lookup.resolve("wisk") is None and loaded.lookup.suggest("wisk") == "WISC",
              f"resolve={loaded.lookup.resolve('wisk')}, suggest={loaded.lookup.suggest('wisk')}")
        check("descriptions come from the Name column",
              loaded.lookup.description("96101-WMS-IV") == NAMES["96101-WMS-IV"],
              f"got {loaded.lookup.description('96101-WMS-IV')}")


def check_chain(env):
    """Scenarios 1 to 3: one case, applied, and then its comment edited and swept again."""
    sh, created, services_ws, work = env.sh, env.created, env.services_ws, env.work
    lock_path, plain, separator = env.lock_path, env.plain, env.separator
    original_lines = env.original_lines
    wisc_line, psych_line, autism_original_line = env.wisc_line, env.psych_line, env.autism_original_line
    wms_line, wiat4, cars4, cars5, wais_swapped = env.wms_line, env.wiat4, env.cars4, env.cars5, env.wais_swapped
    ws = make_ongoing_tab(sh, created, PREFIX + "Ongoing")
    stand_in = ScratchSpreadsheet({ONGOING_TAB: ws, SERVICES_TAB: services_ws})
    client = SheetsMetadataClient(ws)
    comments_cell = rowcol_to_a1(2, ONGOING_HEADERS.index("Comments") + 1)

    print("Scratch tabs, and the cases written to them")
    rows = [ONGOING_HEADERS]
    for _, case_id, codes, comment in CASES:
        rows.append([case_id, "John Doe", "", "", services_cell(codes), "484.00", comment])
    write_table(ws, rows)
    client = SheetsMetadataClient(ws)
    for index, (_, case_id, codes, _) in enumerate(CASES, start=2):
        set_case_tests(client, index, case_id, codes)

    values = ws.get_all_values()
    stored = [stored_tests(client, i) for i in range(2, 2 + len(CASES))]
    check("the headers read back as written", values[:1] == [ONGOING_HEADERS], f"got {values[:1]}")
    check("every case reads back with its comment",
          [row[6] for row in values[1:]] == [c[3] for c in CASES], f"got {[row[6] for row in values[1:]]}")
    check("every case has its original tests stored", stored == [c[2] for c in CASES], f"got {stored}")

    print("\nScenario 1, previewed: it must say what would change, and write nothing")
    case_id, codes, comment = CASES[0][1], CASES[0][2], CASES[0][3]
    before = look(ws, services_ws, client)
    report = run_sweep_command(stand_in, [], work)
    show(report)
    shown = report[:400]
    state = look(ws, services_ws, client)
    check("the report says it is a dry run", "DRY RUN" in report, shown)
    check("Provider: would be set to Dr. Doe", "Provider: would be set to Dr. Doe" in report, shown)
    check("Psychometrist: would be set to J. Smith",
          "Psychometrist: would be set to J. Smith" in report, shown)
    check("tests that would be updated: add 96101-WMS-IV",
          "Tests that would be updated:" in report and "add 96101-WMS-IV" in report, shown)
    check("the sheet is unchanged", state["table"] == before["table"])
    check("the stored original is unchanged", state["stored"] == codes, f"got {state['stored']}")
    check("nothing was noted on the Services tab", state["services_note"] == before["services_note"])
    check("no lock was taken (a preview takes none)", not os.path.exists(lock_path))
    log = read_log(work)
    check("the log has a preview start line", "Sweep started (preview)" in log, f"log: {log[:200]!r}")
    check("and a finish line", "Sweep finished" in log, f"log: {log[:200]!r}")

    print("\nScenario 1, applied for real")
    cell_before = state["row"]["Services Requested"].split("\n")
    report = apply_live(stand_in, work)
    shown = report[:400]
    state = look(ws, services_ws, client)
    row = state["row"]
    check("the report is not a dry run", "DRY RUN" not in report, shown)
    check("Provider: set to Dr. Doe", "Provider: set to Dr. Doe" in report, shown)
    check("Psychometrist: set to J. Smith", "Psychometrist: set to J. Smith" in report, shown)
    check("tests updated: add 96101-WMS-IV",
          "Tests updated:" in report and "add 96101-WMS-IV" in report, shown)
    lines = row["Services Requested"].split("\n")
    # The cell separates its tests with lines of underscores, so its lines are
    # not the same as its tests. The separator is read from the cell as it was.
    separator = cell_before[1] if len(cell_before) > 1 else ""
    new_line = f"4. 96101-WMS-IV - {NAMES['96101-WMS-IV']} (added)"
    tests_in_cell = test_lines(row["Services Requested"])
    check("the cell has four tests", len(tests_in_cell) == 4, f"got {tests_in_cell}")
    check("the first three tests are exactly as before, separators and all",
          lines[:len(cell_before)] == cell_before, f"got {lines[:len(cell_before)]}")
    check("the new test follows a separator, with its Name and marker",
          lines[len(cell_before):] == [separator, new_line], f"got {lines[len(cell_before):]}")
    check("Provider reads Dr. Doe", row["Provider"] == "Dr. Doe", f"got {row['Provider']!r}")
    check("Psychometrist reads J. Smith", row["Psychometrist"] == "J. Smith",
          f"got {row['Psychometrist']!r}")
    check("Price is untouched", row["Price"] == "484.00", f"got {row['Price']!r}")
    check("the comment is unchanged", row["Comments"] == comment)
    check("the Comments cell has no note", state["comments_note"] == "",
          f"note: {state['comments_note']!r}")
    check("the stored original is still the original list", state["stored"] == codes,
          f"got {state['stored']}")
    check("nothing was noted on the Services tab", state["services_note"] == before["services_note"])
    check("the lock was given back", not os.path.exists(lock_path))
    log = read_log(work)
    check("the log has a live start line", log.count("Sweep started (live)") == 1, f"log: {log[-300:]!r}")
    check("and a finish line for it (the preview's was the first)",
          log.count("Sweep finished") == 2, f"log: {log[-300:]!r}")

    print("\nScenario 1, applied again: it must change nothing")
    after_first = state["table"]
    report = apply_live(stand_in, work)
    shown = report[:400]
    state = look(ws, services_ws, client)
    check("Provider: already Dr. Doe, no change", "Provider: already Dr. Doe, no change" in report, shown)
    check("Psychometrist: already J. Smith, no change",
          "Psychometrist: already J. Smith, no change" in report, shown)
    check("Tests: already up to date, no change",
          "Tests: already up to date, no change" in report, shown)
    check("the sheet is unchanged by the second run", state["table"] == after_first)
    check("the stored original is still the original list", state["stored"] == codes)
    check("the lock was given back", not os.path.exists(lock_path))
    log = read_log(work)
    check("the log has a second live start and finish",
          log.count("Sweep started (live)") == 2 and log.count("Sweep finished") == 3,
          f"log: {log[-300:]!r}")

    # Scenarios 2 and 3 continue from here, so the tests are written out once.
    wisc_line, psych_line = cell_before[0], cell_before[2]
    autism_original_line = cell_before[4]
    wms_line = new_line
    wais_line = f"3. 96101-WAIS-IV - {NAMES['96101-WAIS-IV']} (changed from 96130-AUTISM)"
    cars_line = f"5. 96130-CARS-2 - {NAMES['96130-CARS-2']} (added)"
    restored_line = f"3. 96130-AUTISM - {NAMES['96130-AUTISM']}"

    print("\nScenario 2, Tuesday: the provider is edited and a Change line is added")
    comment = comment.replace("Dr. Doe", "Dr. Roe") + "\nChange: autism to wais"
    edit_comment(ws, 2, comment)
    report = apply_live(stand_in, work)
    shown = report[:500]
    state = look(ws, services_ws, client)
    cell = state["row"]["Services Requested"]
    check("Provider: updated from Dr. Doe to Dr. Roe",
          "Provider: updated from Dr. Doe to Dr. Roe" in report, shown)
    check("Psychometrist: already J. Smith, no change",
          "Psychometrist: already J. Smith, no change" in report, shown)
    check("change 96130-AUTISM to 96101-WAIS-IV",
          "change 96130-AUTISM to 96101-WAIS-IV" in report, shown)
    check("nothing is flagged", "0 flagged for attention" in report, shown)
    expected = [wisc_line, psych_line, wais_line, wms_line]
    check("AUTISM is swapped for WAIS, and WMS is still marked (added)",
          test_lines(cell) == expected, f"got {test_lines(cell)}")
    check("the cell keeps a separator between tests", cell == build_cell(separator, expected),
          f"got {cell!r}")
    check("Provider reads Dr. Roe", state["row"]["Provider"] == "Dr. Roe")
    check("the Comments cell has no note", state["comments_note"] == "")
    check("the stored original is still the original list", state["stored"] == codes,
          f"got {state['stored']}")
    check("the lock was given back", not os.path.exists(lock_path))

    print("\nScenario 2, Wednesday: one more line, Add: cars")
    comment += "\nAdd: cars"
    edit_comment(ws, 2, comment)
    report = apply_live(stand_in, work)
    shown = report[:500]
    state = look(ws, services_ws, client)
    cell = state["row"]["Services Requested"]
    check("add 96130-CARS-2", "add 96130-CARS-2" in report, shown)
    check("nothing about the earlier changes",
          "96101-WAIS-IV" not in report and "96101-WMS-IV" not in report, shown)
    expected = expected + [cars_line]
    check("CARS-2 is added last, marked (added)", test_lines(cell) == expected,
          f"got {test_lines(cell)}")
    check("the cell keeps a separator between tests", cell == build_cell(separator, expected),
          f"got {cell!r}")
    check("the Comments cell has no note", state["comments_note"] == "")
    check("the lock was given back", not os.path.exists(lock_path))

    print("\nScenario 3: changing back, Change: wais to autism")
    comment += "\nChange: wais to autism"
    edit_comment(ws, 2, comment)
    report = apply_live(stand_in, work)
    shown = report[:500]
    state = look(ws, services_ws, client)
    cell = state["row"]["Services Requested"]
    check("change 96101-WAIS-IV to 96130-AUTISM",
          "change 96101-WAIS-IV to 96130-AUTISM" in report, shown)
    expected = [wisc_line, psych_line, restored_line, wms_line, cars_line]
    check("line 3 is AUTISM again, with no marker, and WMS and CARS are still (added)",
          test_lines(cell) == expected, f"got {test_lines(cell)}")
    check("its text is the Services tab's Name, not the cell's original wording",
          restored_line != autism_original_line and test_lines(cell)[2:3] == [restored_line],
          f"got {test_lines(cell)[2:3]}")
    check("the cell keeps a separator between tests", cell == build_cell(separator, expected),
          f"got {cell!r}")
    check("the Comments cell has no note", state["comments_note"] == "")
    check("the stored original is still the original list", state["stored"] == codes)
    after_chain = state["table"]
    report = apply_live(stand_in, work)
    state = look(ws, services_ws, client)
    check("applied again: Tests: already up to date, no change",
          "Tests: already up to date, no change" in report, report[:500])
    check("the sheet is unchanged by that run", state["table"] == after_chain)
    check("the lock was given back", not os.path.exists(lock_path))


def check_flags(env):
    """Scenarios 4, 5 and 10: changes that don't fit, an unknown test, and a line that can't be read."""
    sh, created, services_ws, work = env.sh, env.created, env.services_ws, env.work
    lock_path, plain, separator = env.lock_path, env.plain, env.separator
    original_lines = env.original_lines
    wisc_line, psych_line, autism_original_line = env.wisc_line, env.psych_line, env.autism_original_line
    wms_line, wiat4, cars4, cars5, wais_swapped = env.wms_line, env.wiat4, env.cars4, env.cars5, env.wais_swapped
    ws = make_ongoing_tab(sh, created, PREFIX + "Ongoing 2")
    stand_in = ScratchSpreadsheet({ONGOING_TAB: ws, SERVICES_TAB: services_ws})
    client = SheetsMetadataClient(ws)

    # --- Scenarios 4, 5 and 10: each on its own row, sharing a sweep -----
    original_lines = [wisc_line, psych_line, autism_original_line]
    first_row = 2
    batch_rows = {bid: first_row + i for i, (_, bid, _, _, _) in enumerate(BATCH)}
    cells = {}
    rows = []
    for _, bid, tests, bcomment, typed in BATCH:
        cells[bid] = services_cell(tests) + (("\n" + typed) if typed else "")
        rows.append([bid, "John Doe", "", "", cells[bid], "484.00", bcomment])
    last_row = first_row + len(rows) - 1
    ws.update(range_name=f"A{first_row}:{rowcol_to_a1(last_row, len(ONGOING_HEADERS))}", values=rows)
    for _, bid, tests, _, _ in BATCH:
        set_case_tests(client, batch_rows[bid], bid, tests)
    r04, r05, r10 = batch_rows["S04"], batch_rows["S05"], batch_rows["S10"]
    note_rows = list(batch_rows.values())

    print("\nScenarios 4, 5 and 10: set up on their own rows, then previewed")
    set_up = look_at_rows(ws, client, [])
    check("the batch rows read back with their comments and cells",
          [(set_up["table"][batch_rows[b] - 1][4], set_up["table"][batch_rows[b] - 1][6])
           for _, b, _, _, _ in BATCH] == [(cells[b], c) for _, b, _, c, _ in BATCH],
          f"got {[set_up['table'][batch_rows[b] - 1][4:7:2] for _, b, _, _, _ in BATCH]}")
    check("each batch case has its original stored",
          all(set_up["stored"].get(batch_rows[b]) == t for _, b, t, _, _ in BATCH),
          f"got {set_up['stored']}")
    report = run_sweep_command(stand_in, [], work)
    show(report)
    previewed = look_at_rows(ws, client, [])
    check("the report says it is a dry run", "DRY RUN" in report, report[:300])
    check("the sheet is unchanged by the preview", previewed["table"] == set_up["table"])
    check("the stored originals are unchanged by the preview", previewed["stored"] == set_up["stored"])

    print("\nScenarios 4, 5 and 10, applied")
    report = apply_live(stand_in, work)
    state = look_at_rows(ws, client, note_rows)
    table = state["table"]

    print("  -- scenario 4: changes that don't fit")
    block = case_block(report, "S04")
    wiat4 = f"4. 96101-WIAT-III - {NAMES['96101-WIAT-III']} (added)"
    skipped_add = "Add: 96101-WISC-V  (skipped: 96101-WISC-V is already on this case)"
    skipped_change = ("Change: 96101-WPPSI-IV to 96101-WAIS-IV  "
                      "(skipped: 96101-WPPSI-IV isn't on this case)")
    cell = row_of(table, r04)["Services Requested"]
    note = state["notes"][r04]
    check("S04: add 96101-WIAT-III", "add 96101-WIAT-III" in block, block[:300])
    check("S04: the report has a NEEDS ATTENTION list", "NEEDS ATTENTION:" in report, report[:300])
    check("S04: the Add of a test already there is skipped", skipped_add in report, report[:400])
    check("S04: the Change of a test that isn't there is skipped", skipped_change in report, report[:400])
    check("S04: the cell gains only WIAT-III, marked (added)",
          test_lines(cell) == original_lines + [wiat4], f"got {test_lines(cell)}")
    check("S04: the cell keeps a separator between tests",
          cell == build_cell(separator, original_lines + [wiat4]), f"got {cell!r}")
    check("S04: the Comments note lists both skipped lines",
          skipped_add in note and skipped_change in note, f"note: {note!r}")

    print("  -- scenario 5: an unknown test next to a good one")
    block = case_block(report, "S05")
    cars4 = f"4. 96130-CARS-2 - {NAMES['96130-CARS-2']} (added)"
    row = row_of(table, r05)
    cell = row["Services Requested"]
    note = state["notes"][r05]
    issue_lines = note.split("\n")[1:]
    check("S05: Provider: set to Dr. Doe", "Provider: set to Dr. Doe" in block, block[:300])
    check("S05: add 96130-CARS-2", "add 96130-CARS-2" in block, block[:300])
    check("S05: wisk is flagged with a suggestion", "did you mean WISC?" in report, report[:400])
    check("S05: CARS-2 is added, and nothing else changes",
          test_lines(cell) == original_lines + [cars4], f"got {test_lines(cell)}")
    check("S05: the cell keeps a separator between tests",
          cell == build_cell(separator, original_lines + [cars4]), f"got {cell!r}")
    check("S05: the Comments note has only the wisk line, with its suggestion",
          len(issue_lines) == 1 and "wisk" in issue_lines[0].lower()
          and "did you mean WISC?" in issue_lines[0], f"note: {note!r}")
    check("S05: Provider reads Dr. Doe", row["Provider"] == "Dr. Doe", f"got {row['Provider']!r}")

    print("  -- scenario 10: a line the system can't read")
    block = case_block(report, "S10")
    cant_read = "Services Requested  (not changed: the cell has a line that can't be read."
    row = row_of(table, r10)
    note = state["notes"][r10]
    check("S10: Provider: set to Dr. Doe", "Provider: set to Dr. Doe" in block, block[:300])
    check("S10: Psychometrist: set to J. Smith", "Psychometrist: set to J. Smith" in block, block[:300])
    check("S10: the report says the cell has a line that can't be read",
          cant_read in report, report[:500])
    check("S10: the report quotes the line, add wms", "add wms" in report, report[:500])
    check("S10: the cell is not changed", row["Services Requested"] == cells["S10"],
          f"got {row['Services Requested']!r}")
    check("S10: the Comments note says so, and quotes the line",
          cant_read in note and "add wms" in note, f"note: {note!r}")
    check("S10: Provider and Psychometrist read back",
          (row["Provider"], row["Psychometrist"]) == ("Dr. Doe", "J. Smith"),
          f"got {(row['Provider'], row['Psychometrist'])}")

    check("the stored originals are unchanged", state["stored"] == previewed["stored"],
          f"got {state['stored']}")
    check("the lock was given back", not os.path.exists(lock_path))
    round_one = state

    print("\nScenarios 4 and 10, corrected, and 5 left as it was")
    edit_comment(ws, r04, "Add: wiat")        # the two bad lines are deleted
    cell_address = rowcol_to_a1(r10, ONGOING_HEADERS.index("Services Requested") + 1)
    ws.update(range_name=cell_address, values=[[services_cell(ORIGINAL_TESTS)]])  # the loose line is deleted
    report = apply_live(stand_in, work)
    state = look_at_rows(ws, client, note_rows)
    table = state["table"]

    print("  -- scenario 4")
    check("S04: Tests: already up to date, no change",
          "Tests: already up to date, no change" in case_block(report, "S04"),
          case_block(report, "S04")[:300])
    check("S04: the note clears", state["notes"][r04] == "", f"note: {state['notes'][r04]!r}")
    check("S04: the cell is unchanged",
          row_of(table, r04)["Services Requested"] == row_of(round_one["table"], r04)["Services Requested"])

    print("  -- scenario 10")
    cell = row_of(table, r10)["Services Requested"]
    check("S10: add 96130-CARS-2", "add 96130-CARS-2" in case_block(report, "S10"),
          case_block(report, "S10")[:300])
    check("S10: CARS-2 is added", test_lines(cell) == original_lines + [cars4], f"got {test_lines(cell)}")
    check("S10: the cell keeps a separator between tests",
          cell == build_cell(separator, original_lines + [cars4]), f"got {cell!r}")
    check("S10: the note clears", state["notes"][r10] == "", f"note: {state['notes'][r10]!r}")

    print("  -- scenario 5 (its comment still has the unknown test)")
    check("S05: its flag stays", state["notes"][r05] == round_one["notes"][r05],
          f"note: {state['notes'][r05]!r}")
    check("S05: its cell is unchanged",
          row_of(table, r05)["Services Requested"] == row_of(round_one["table"], r05)["Services Requested"])

    check("the lock was given back", not os.path.exists(lock_path))


def check_nothing_stored(env):
    """Scenarios 11 to 14: the stored original is missing, or belongs to another row."""
    sh, created, services_ws, work = env.sh, env.created, env.services_ws, env.work
    lock_path, plain, separator = env.lock_path, env.plain, env.separator
    original_lines = env.original_lines
    wisc_line, psych_line, autism_original_line = env.wisc_line, env.psych_line, env.autism_original_line
    wms_line, wiat4, cars4, cars5, wais_swapped = env.wms_line, env.wiat4, env.cars4, env.cars5, env.wais_swapped
    ws = make_ongoing_tab(sh, created, PREFIX + "Ongoing 5")
    stand_in = ScratchSpreadsheet({ONGOING_TAB: ws, SERVICES_TAB: services_ws})
    client = SheetsMetadataClient(ws)

    # --- Scenarios 11 to 14: the stored original is missing, or isn't this row's ---
    changed_cell = build_cell(separator, original_lines + [wms_line])
    clean_cell = services_cell(ORIGINAL_TESTS)
    refused = "Services Requested  (not changed:"
    second = [
        # Case ID on the row, the original stored for it (a Case ID and tests), the cell, the comment
        ("S11", None, changed_cell, "Add: wiat"),
        ("S12", None, "", "Add: cars"),
        ("S12-CONTROL", None, "", ""),
        ("S13", None, clean_cell, "Psychometrist: J. Smith\nAdd: wiat"),
        ("S14X", ("S14", ORIGINAL_TESTS), clean_cell, "Provider: Dr. Doe\nAdd: cars"),
    ]
    first_row2 = 2
    rows2 = {cid: first_row2 + i for i, (cid, _, _, _) in enumerate(second)}
    r11, r12, r12c, r13, r14 = (rows2[c] for c in ("S11", "S12", "S12-CONTROL", "S13", "S14X"))
    last_row2 = first_row2 + len(second) - 1
    ws.update(
        range_name=f"A{first_row2}:{rowcol_to_a1(last_row2, len(ONGOING_HEADERS))}",
        values=[[cid, "John Doe", "", "", cell_text, "484.00", c] for cid, _, cell_text, c in second],
    )
    for cid, stored_for, _, _ in second:
        if stored_for:
            set_case_tests(client, rows2[cid], stored_for[0], stored_for[1])

    print("\nScenarios 11 to 14: set up on their own rows, then previewed")
    set_up = look_at_rows(ws, client, [])
    check("the rows read back with their comments and cells",
          [(set_up["table"][rows2[cid] - 1][4], set_up["table"][rows2[cid] - 1][6])
           for cid, _, _, _ in second] == [(cell_text, c) for _, _, cell_text, c in second],
          f"got {[set_up['table'][rows2[cid] - 1][4:7:2] for cid, _, _, _ in second]}")
    check("only scenario 14 has an original stored, under the Case ID it had",
          [r for r in set_up["stored"] if r >= first_row2] == [r14] and set_up["stored"][r14] == ORIGINAL_TESTS,
          f"got {set_up['stored']}")
    report = run_sweep_command(stand_in, [], work)
    show(report)
    previewed = look_at_rows(ws, client, [])
    check("the report says it is a dry run", "DRY RUN" in report, report[:300])
    check("the sheet is unchanged by the preview", previewed["table"] == set_up["table"])
    check("the preview saved no original (scenario 13 is not adopted by a preview)",
          previewed["stored"] == set_up["stored"], f"got {previewed['stored']}")

    print("\nScenarios 11 to 14, applied")
    report = apply_live(stand_in, work)
    state = look_at_rows(ws, client, [r11, r12, r14])
    table, stored = state["table"], state["stored"]

    print("  -- scenario 11: nothing stored, and the cell already shows changes")
    block, note = case_block(report, "S11"), state["notes"][r11]
    check("S11: the cell is not changed", row_of(table, r11)["Services Requested"] == changed_cell,
          f"got {row_of(table, r11)['Services Requested']!r}")
    check("S11: nothing is stored for it", r11 not in stored, f"got {stored.get(r11)}")
    reason_11 = ("Services Requested  (not changed: this case's original tests weren't saved and "
                 "the cell has already been changed, so it can't be updated safely)")
    check("S11: the report gives the reason: nothing saved, and the cell already changed",
          reason_11 in block, block[:400])
    check("S11: the Comments note gives it too", reason_11 in note, f"note: {note!r}")
    print(f"  (for reference, the note on S11 reads: {note!r})")

    print("  -- scenario 12: nothing stored, and an empty cell")
    block, note = case_block(report, "S12"), state["notes"][r12]
    check("S12: the cell stays empty", row_of(table, r12)["Services Requested"] == "",
          f"got {row_of(table, r12)['Services Requested']!r}")
    check("S12: nothing is stored for it", r12 not in stored, f"got {stored.get(r12)}")
    reason_12 = ("Services Requested  (not changed: this case has no tests recorded, "
                 "so changes can't be applied)")
    check("S12: the report gives the reason: no tests recorded", reason_12 in block, block[:400])
    check("S12: the Comments note gives it too", reason_12 in note, f"note: {note!r}")
    check("S12 control: an empty comment and an empty cell is not in the report at all",
          "S12-CONTROL" not in report, report[:400])
    check("S12 control: its row is untouched", row_of(table, r12c) == row_of(set_up["table"], r12c))
    print(f"  (for reference, the note on S12 reads: {note!r})")

    print("  -- scenario 13: nothing stored, and a clean cell, so its tests are adopted")
    block = case_block(report, "S13")
    saved_line = next((l for l in block.splitlines() if "Original tests saved from the sheet:" in l), "")
    row = row_of(table, r13)
    cell = row["Services Requested"]
    check("S13: it saves the cell's three tests as the original, without WIAT-III",
          bool(saved_line) and all(code in saved_line for code in ORIGINAL_TESTS)
          and "96101-WIAT-III" not in saved_line, block[:400])
    check("S13: add 96101-WIAT-III", "add 96101-WIAT-III" in block, block[:400])
    check("S13: Psychometrist: set to J. Smith", "Psychometrist: set to J. Smith" in block, block[:400])
    check("S13: the stored original is the cell's tests, without WIAT-III",
          stored.get(r13) == ORIGINAL_TESTS, f"got {stored.get(r13)}")
    check("S13: the cell gains only WIAT-III, marked (added)",
          test_lines(cell) == original_lines + [wiat4], f"got {test_lines(cell)}")
    check("S13: the cell keeps a separator between tests",
          cell == build_cell(separator, original_lines + [wiat4]), f"got {cell!r}")
    check("S13: Psychometrist reads J. Smith", row["Psychometrist"] == "J. Smith",
          f"got {row['Psychometrist']!r}")

    print("  -- scenario 14: a stored original that doesn't match its row")
    block, note = case_block(report, "S14X"), state["notes"][r14]
    row = row_of(table, r14)
    check("S14: Provider: set to Dr. Doe", "Provider: set to Dr. Doe" in block, block[:400])
    check("S14: Provider reads Dr. Doe", row["Provider"] == "Dr. Doe", f"got {row['Provider']!r}")
    check("S14: the cell is not changed", row["Services Requested"] == clean_cell,
          f"got {row['Services Requested']!r}")
    reason_14 = (f"Services Requested  (not changed: Row {r14}: the stored test list is for "
                 "case S14, not S14X, so it was ignored)")
    check("S14: the Comments note gives the reason: the stored list is for another case",
          reason_14 in note, f"note: {note!r}")
    check("S14: its stored original is untouched", stored.get(r14) == ORIGINAL_TESTS,
          f"got {stored.get(r14)}")
    print(f"  (for reference, the note on S14 reads: {note!r})")

    check("the lock was given back", not os.path.exists(lock_path))
    round_three = state

    print("\nScenario 14 corrected, scenario 13 applied again, 11 and 12 left as they were")
    ws.update(range_name=rowcol_to_a1(r14, 1), values=[["S14"]])     # the Case ID is put back
    report = apply_live(stand_in, work)
    state = look_at_rows(ws, client, [r11, r12, r14])
    table = state["table"]

    print("  -- scenario 14")
    cell = row_of(table, r14)["Services Requested"]
    check("S14: add 96130-CARS-2", "add 96130-CARS-2" in case_block(report, "S14"),
          case_block(report, "S14")[:400])
    check("S14: CARS-2 is added", test_lines(cell) == original_lines + [cars4], f"got {test_lines(cell)}")
    check("S14: the cell keeps a separator between tests",
          cell == build_cell(separator, original_lines + [cars4]), f"got {cell!r}")
    check("S14: the note clears", state["notes"][r14] == "", f"note: {state['notes'][r14]!r}")

    print("  -- scenario 13")
    check("S13: Tests: already up to date, no change",
          "Tests: already up to date, no change" in case_block(report, "S13"),
          case_block(report, "S13")[:400])
    check("S13: the stored original is still the cell's original tests",
          state["stored"].get(r13) == ORIGINAL_TESTS, f"got {state['stored'].get(r13)}")

    print("  -- scenarios 11 and 12 (nothing was done about them)")
    check("S11 and S12: their flags stay",
          state["notes"][r11] == round_three["notes"][r11] and state["notes"][r12] == round_three["notes"][r12])
    check("S11 and S12: their cells are unchanged",
          [row_of(table, r)["Services Requested"] for r in (r11, r12)] == [changed_cell, ""])
    check("the lock was given back", not os.path.exists(lock_path))


def check_rewrites(env):
    """Scenarios 6 to 9: a comment rewritten or cleared, and a line typed into the cell by hand."""
    sh, created, services_ws, work = env.sh, env.created, env.services_ws, env.work
    lock_path, plain, separator = env.lock_path, env.plain, env.separator
    original_lines = env.original_lines
    wisc_line, psych_line, autism_original_line = env.wisc_line, env.psych_line, env.autism_original_line
    wms_line, wiat4, cars4, cars5, wais_swapped = env.wms_line, env.wiat4, env.cars4, env.cars5, env.wais_swapped

    # --- Scenarios 6 to 9: rewrites and hand edits, on a tab of their own ---
    print("\nScenarios 6 to 9: a tab of their own, set up, then the first state applied")
    ws3 = make_ongoing_tab(sh, created, PREFIX + "Ongoing 3")
    stand_in3 = ScratchSpreadsheet({ONGOING_TAB: ws3, SERVICES_TAB: services_ws})
    client3 = SheetsMetadataClient(ws3)
    plain = services_cell(ORIGINAL_TESTS)
    hand_line = "4. 96130-CARS-2 - typed by hand"
    third = [
        # Case ID, the cell, the comment for the first sweep
        ("S06A", plain, "Add: cars"),
        ("S06B", plain, "Add: cars"),
        ("S07", plain, "Change: wisc to wais"),
        ("S08", plain, "Add: cars"),
        ("S08-CONTROL", plain, ""),
        ("S09", plain + "\n" + hand_line, "Provider: Dr. Doe"),
    ]
    rows3 = {cid: 2 + i for i, (cid, _, _) in enumerate(third)}
    r6a, r6b, r7, r8, r8c, r9 = (rows3[c] for c in ("S06A", "S06B", "S07", "S08", "S08-CONTROL", "S09"))
    ws3.update(
        range_name=f"A2:{rowcol_to_a1(1 + len(third), len(ONGOING_HEADERS))}",
        values=[[cid, "John Doe", "", "", cell_text, "484.00", c] for cid, cell_text, c in third],
    )
    for cid, _, _ in third:
        set_case_tests(client3, rows3[cid], cid, ORIGINAL_TESTS)
    started = look_at_rows(ws3, client3, [])
    check("the rows read back with their comments and cells",
          [(started["table"][rows3[c] - 1][4], started["table"][rows3[c] - 1][6]) for c, _, _ in third]
          == [(cell_text, comment_text) for _, cell_text, comment_text in third],
          f"got {[started['table'][rows3[c] - 1][4:7:2] for c, _, _ in third]}")

    wais_swapped = f"1. 96101-WAIS-IV - {NAMES['96101-WAIS-IV']} (changed from 96101-WISC-V)"
    cars5 = f"5. 96130-CARS-2 - {NAMES['96130-CARS-2']} (added)"

    print("  -- round A: the first state of each is applied")
    report = apply_live(stand_in3, work)
    state = look_at_rows(ws3, client3, [])
    table = state["table"]
    cell_of = lambda cid: row_of(table, rows3[cid])["Services Requested"]
    check("S06A, S06B and S08: CARS-2 is added",
          all(test_lines(cell_of(c)) == original_lines + [cars4] for c in ("S06A", "S06B", "S08")),
          f"got {[test_lines(cell_of(c)) for c in ('S06A', 'S06B', 'S08')]}")
    check("S07: WISC-V is swapped for WAIS-IV, and nothing else changes",
          test_lines(cell_of("S07")) == [wais_swapped, psych_line, autism_original_line],
          f"got {test_lines(cell_of('S07'))}")
    check("S09: Provider: set to Dr. Doe", "Provider: set to Dr. Doe" in case_block(report, "S09"),
          case_block(report, "S09")[:300])
    check("S09: Provider reads Dr. Doe", row_of(table, r9)["Provider"] == "Dr. Doe")
    check("S09: the cell is not touched, since the comment has no test changes",
          cell_of("S09") == plain + "\n" + hand_line, f"got {cell_of('S09')!r}")
    check("the report is only about this batch's cases, since it has a tab of its own",
          set(re.findall(r"^Case (\S+):", report, re.M)) <= set(rows3), report[:400])
    check("the control (empty comment, plain cell) is not in the report at all",
          "S08-CONTROL" not in report, report[:300])
    check("the control row is untouched", row_of(table, r8c) == row_of(started["table"], r8c))
    check("the lock was given back", not os.path.exists(lock_path))
    round_a = state

    print("\nScenarios 6 to 9: rewritten, cleared, and added to")
    comments_b = {"S06A": "Add: wiat", "S06B": "Add: wiat", "S07": "Add: cars",
                  "S08": "", "S09": "Provider: Dr. Doe\nAdd: wiat"}
    ws3.update(range_name=f"G2:G{1 + len(third)}",
               values=[[comments_b.get(cid, c)] for cid, _, c in third])
    report = apply_live(stand_in3, work)
    state = look_at_rows(ws3, client3, [r6a, r6b, r7, r8, r9])
    table, notes = state["table"], state["notes"]
    cell_of = lambda cid: row_of(table, rows3[cid])["Services Requested"]
    kept_earlier = ("Add: 96130-CARS-2  (kept: the cell has it from an earlier change, "
                    "but this comment no longer includes it.")
    kept_advice = " Put the line back in the comment to keep it, or delete it from the cell to remove it)"

    print("  -- scenario 6: a comment rewritten from scratch")
    expected = original_lines + [wiat4, cars5]
    check("S06A: add 96101-WIAT-III", "add 96101-WIAT-III" in case_block(report, "S06A"),
          case_block(report, "S06A")[:400])
    check("S06A: the report says CARS-2 was kept", kept_earlier in case_block(report, "S06A"),
          case_block(report, "S06A")[:400])
    check("S06A: the cell has WIAT-III and then CARS-2, both marked (added)",
          test_lines(cell_of("S06A")) == expected, f"got {test_lines(cell_of('S06A'))}")
    check("S06A: the cell keeps a separator between tests",
          cell_of("S06A") == build_cell(separator, expected), f"got {cell_of('S06A')!r}")
    check("S06A: the Comments note lists the kept line, with how to keep or remove it",
          kept_earlier + kept_advice in notes[r6a], f"note: {notes[r6a]!r}")
    check("S06B: the cell has WIAT-III and then CARS-2, both marked (added)",
          test_lines(cell_of("S06B")) == expected, f"got {test_lines(cell_of('S06B'))}")
    check("S06B: the Comments note lists the kept line, with how to keep or remove it",
          kept_earlier + kept_advice in notes[r6b], f"note: {notes[r6b]!r}")
    print(f"  (for reference, the note on S06A reads: {notes[r6a]!r})")

    print("  -- scenario 7: a swap the comment forgets")
    check("S07: add 96130-CARS-2", "add 96130-CARS-2" in case_block(report, "S07"),
          case_block(report, "S07")[:400])
    check("S07: line 1 stays WAIS-IV, WISC-V does not come back, and CARS-2 is added",
          test_lines(cell_of("S07")) == [wais_swapped, psych_line, autism_original_line, cars4],
          f"got {test_lines(cell_of('S07'))}")
    check("S07: the note says the Change was kept, with how to keep or remove it",
          "Change: 96101-WISC-V to 96101-WAIS-IV  (kept: the cell has it from an earlier change, "
          "but this comment no longer includes it." + kept_advice in notes[r7], f"note: {notes[r7]!r}")
    print(f"  (for reference, the note on S07 reads: {notes[r7]!r})")

    print("  -- scenario 8: a comment cleared completely")
    check("S08: the case is still in the report, with a kept line for CARS-2",
          "Case S08:" in report and "Add: 96130-CARS-2  (kept:" in case_block(report, "S08"),
          case_block(report, "S08")[:400])
    check("S08: the Comments cell has a note, although it is empty",
          "kept" in notes[r8] and "96130-CARS-2" in notes[r8], f"note: {notes[r8]!r}")
    check("S08: the CARS-2 line is still in the cell",
          test_lines(cell_of("S08")) == original_lines + [cars4], f"got {test_lines(cell_of('S08'))}")
    check("S08 control: still not in the report", "S08-CONTROL" not in report, report[:300])

    print("  -- scenario 9: a line typed into the cell by hand")
    lines9 = test_lines(cell_of("S09"))
    tail = lines9[3:]
    check("S09: add 96101-WIAT-III", "add 96101-WIAT-III" in case_block(report, "S09"),
          case_block(report, "S09")[:400])
    check("S09: WIAT-III is added, and the hand-typed line stays in its own words, marked (added)",
          lines9[:3] == original_lines
          and sorted(strip_number(l) for l in tail)
          == sorted([f"96101-WIAT-III - {NAMES['96101-WIAT-III']} (added)",
                     "96130-CARS-2 - typed by hand (added)"])
          and [l.split(". ", 1)[0] for l in tail] == ["4", "5"], f"got {lines9}")
    check("S09: the note has a kept line for CARS-2 that says it was typed by hand, not that it came from an earlier change",
          "Add: 96130-CARS-2  (kept: the cell has this line, but it isn't in the PDF's list or this comment. "
          "Add it to the comment to keep it as a change, or delete it from the cell to remove it)" in notes[r9]
          and "from an earlier change" not in notes[r9],
          f"note: {notes[r9]!r}")
    print(f"  (for reference, the note on S09 reads: {notes[r9]!r})")

    check("the stored originals are unchanged", state["stored"] == round_a["stored"],
          f"got {state['stored']}")
    check("the lock was given back", not os.path.exists(lock_path))
    round_b = state

    print("\nScenarios 6 to 8, resolved")
    lines_6b = cell_of("S06B").split("\n")
    deleted_by_hand = "\n".join(lines_6b[:-1])            # the CARS-2 line, but not the separator above it
    ws3.update(range_name=f"G{r6a}", values=[["Add: wiat\nAdd: cars"]])
    ws3.update(range_name=f"G{r7}", values=[["Add: cars\nChange: wisc to wais"]])
    ws3.update(range_name=f"G{r8}", values=[["Add: cars"]])
    ws3.update(range_name=f"E{r6b}", values=[[deleted_by_hand]])
    report = apply_live(stand_in3, work)
    state = look_at_rows(ws3, client3, [r6a, r6b, r7, r8, r9])
    table, notes = state["table"], state["notes"]
    cell_of = lambda cid: row_of(table, rows3[cid])["Services Requested"]
    was = lambda cid: row_of(round_b["table"], rows3[cid])["Services Requested"]

    print("  -- scenario 6: kept (the line is put back), or removed (deleted from the cell by hand)")
    check("S06A: Tests: already up to date, no change",
          "Tests: already up to date, no change" in case_block(report, "S06A"),
          case_block(report, "S06A")[:400])
    check("S06A: the note clears", notes[r6a] == "", f"note: {notes[r6a]!r}")
    check("S06A: the cell is unchanged", cell_of("S06A") == was("S06A"))
    check("S06B: the cell keeps just WIAT-III, with the leftover separator tidied",
          cell_of("S06B") == build_cell(separator, original_lines + [wiat4]), f"got {cell_of('S06B')!r}")
    check("S06B: Tests: cell rewritten (wording, markers or layout), no tests changed",
          "Tests: cell rewritten (wording, markers or layout), no tests changed" in case_block(report, "S06B"),
          case_block(report, "S06B")[:400])
    check("S06B: the note clears", notes[r6b] == "", f"note: {notes[r6b]!r}")

    print("  -- scenarios 7 and 8")
    check("S07: the note clears", notes[r7] == "", f"note: {notes[r7]!r}")
    check("S07: the cell is unchanged", cell_of("S07") == was("S07"))
    check("S08: the note clears", notes[r8] == "", f"note: {notes[r8]!r}")
    check("S08: the cell is unchanged", cell_of("S08") == was("S08"))
    # Its line now carries a marker, so the sweep may describe it as "from an earlier
    # change" and not as typed by hand. The flag must stay. Its wording may change.
    check("S09: its flag stays, since its comment still leaves that line out",
          "Add: 96130-CARS-2  (kept:" in notes[r9], f"note: {notes[r9]!r}")
    print(f"  (for reference, the note on S09 now reads: {notes[r9]!r})")
    check("the lock was given back", not os.path.exists(lock_path))


def check_moves(env):
    """Scenarios 15 and 16: rows that move, and a case moved to Finished."""
    sh, created, services_ws, work = env.sh, env.created, env.services_ws, env.work
    lock_path, plain, separator = env.lock_path, env.plain, env.separator
    original_lines = env.original_lines
    wisc_line, psych_line, autism_original_line = env.wisc_line, env.psych_line, env.autism_original_line
    wms_line, wiat4, cars4, cars5, wais_swapped = env.wms_line, env.wiat4, env.cars4, env.cars5, env.wais_swapped

    # --- Scenarios 15 and 16: rows move, and a case goes to Finished, on a tab of their own ---
    print("\nScenarios 15 and 16: a tab of their own, three cases with changes applied")
    ws4 = make_ongoing_tab(sh, created, PREFIX + "Ongoing 4")
    finished_ws = make_tab(sh, created, PREFIX + "Finished", FINISHED_HEADERS)
    stand_in4 = ScratchSpreadsheet({ONGOING_TAB: ws4, SERVICES_TAB: services_ws})
    client4 = SheetsMetadataClient(ws4)
    moving = [
        # Case ID, the comment. Out of order, so that sorting by Case ID moves them.
        ("S15B", "Add: cars"),
        ("S15C", "Add: wiat"),
        ("S15A", "Change: wisc to wais"),
    ]
    ids = [cid for cid, _ in moving]
    expected_cell = {
        "S15B": build_cell(separator, original_lines + [cars4]),
        "S15C": build_cell(separator, original_lines + [wiat4]),
        "S15A": build_cell(separator, [wais_swapped, psych_line, autism_original_line]),
    }
    ws4.update(range_name=f"A2:{rowcol_to_a1(1 + len(moving), len(ONGOING_HEADERS))}",
               values=[[cid, "John Doe", "", "", plain, "484.00", c] for cid, c in moving])
    for row, (cid, _) in enumerate(moving, start=2):
        set_case_tests(client4, row, cid, ORIGINAL_TESTS)
    report = apply_live(stand_in4, work)
    state = look_at_moved(ws4, client4)
    check("the report is only about these three cases",
          set(re.findall(r"^Case (\S+):", report, re.M)) == set(ids), report[:400])
    check("each case has its change applied",
          all(row_of(state["table"], state["where"][c])["Services Requested"] == expected_cell[c] for c in ids),
          f"got {[row_of(state['table'], state['where'][c])['Services Requested'] for c in ids]}")
    check("the lock was given back", not os.path.exists(lock_path))

    def moved_checks(label, report, state, remaining):
        """After rows have moved: nothing changes, nothing is flagged, and every original is on its own row."""
        where, table = state["where"], state["table"]
        check(f"{label}: every case says its tests are already up to date",
              all("Tests: already up to date, no change" in case_block(report, c) for c in remaining),
              report[:500])
        check(f"{label}: nothing is flagged", "NEEDS ATTENTION" not in report, report[:500])
        check(f"{label}: no cell has changed",
              all(row_of(table, where[c])["Services Requested"] == expected_cell[c] for c in remaining))
        check(f"{label}: no Comments cell has a note",
              all(state["notes"][c] == "" for c in remaining), f"notes: {state['notes']}")
        check(f"{label}: each stored original is on the row of its own Case ID",
              all(state["stored"].get(where[c]) == (c, ORIGINAL_TESTS) for c in remaining),
              f"got {state['stored']}, cases at {where}")
        check(f"{label}: the lock was given back", not os.path.exists(lock_path))

    print("\nScenario 15: rows that move keep their originals")
    ws4.insert_row([""] * len(ONGOING_HEADERS), index=2)          # a blank row above the first case
    report = apply_live(stand_in4, work)
    state = look_at_moved(ws4, client4)
    check("the cases moved down one row, in the same order",
          state["where"] == {"S15B": 3, "S15C": 4, "S15A": 5}, f"got {state['where']}")
    check("the blank row has no stored original", 2 not in state["stored"], f"got {state['stored']}")
    moved_checks("after inserting a row", report, state, ids)

    ws4.sort((1, "asc"), range=f"A3:G{2 + len(moving)}")           # the cases, by Case ID
    report = apply_live(stand_in4, work)
    state = look_at_moved(ws4, client4)
    check("the cases are now in Case ID order, below the blank row",
          [state["table"][r - 1][0] for r in (2, 3, 4, 5)] == ["", "S15A", "S15B", "S15C"],
          f"got {[state['table'][r - 1][0] for r in (2, 3, 4, 5)]}")
    moved_checks("after sorting by Case ID", report, state, ids)

    print("\nScenario 16: moving a case to Finished")
    date_completed = "11/10/2026 01:00 PM"
    move_case_to_finished(ws4, finished_ws, "S15B", extra_fields={"Date Completed": date_completed})
    report = apply_live(stand_in4, work)
    state = look_at_moved(ws4, client4)
    finished_table = finished_ws.get_all_values()
    remaining = ["S15A", "S15C"]
    check("the Ongoing row is gone, and the rows below moved up",
          [state["table"][r - 1][0] for r in (3, 4)] == remaining and "S15B" not in state["where"]
          and len(rows_by_id(state["table"])) == 2, f"got {state['where']}")
    check("the case is in Finished, with its Date Completed",
          len(finished_table) > 1 and finished_table[1][0] == "S15B"
          and finished_table[1][FINISHED_HEADERS.index("Date Completed")] == date_completed,
          f"got {finished_table[1:]}")
    check("its stored original went with its row",
          all(cid != "S15B" for cid, _ in state["stored"].values()) and len(state["stored"]) == 2,
          f"got {state['stored']}")
    check("a sweep afterwards does not mention the case", "S15B" not in report, report[:400])
    moved_checks("after the move", report, state, remaining)


def check_interrupted_moves(env):
    """Scenario 19: a move interrupted after its write can be run again, and refuses when Finished disagrees."""
    sh, created, services_ws, work = env.sh, env.created, env.services_ws, env.work
    lock_path, plain = env.lock_path, env.plain
    ws = make_ongoing_tab(sh, created, PREFIX + "Ongoing 6")
    finished_ws = make_tab(sh, created, PREFIX + "Finished 2", FINISHED_HEADERS)
    stand_in = ScratchSpreadsheet({ONGOING_TAB: ws, SERVICES_TAB: services_ws})
    client = SheetsMetadataClient(ws)

    print("Scenario 19: a move interrupted after it writes to Finished, and run again")
    ids = ["S19A", "S19B", "S19C", "S19D"]
    date_completed = "11/12/2026 01:00 PM"
    extras = {"Date Completed": date_completed}
    date_column = FINISHED_HEADERS.index("Date Completed")
    ws.update(range_name=f"A2:{rowcol_to_a1(1 + len(ids), len(ONGOING_HEADERS))}",
              values=[[cid, "John Doe", "Dr. Doe", "J. Smith", plain, "484.00", "Add: cars"] for cid in ids])
    for row, cid in enumerate(ids, start=2):
        set_case_tests(client, row, cid, ORIGINAL_TESTS)

    def row_at(table, number):
        """A Finished row, padded to the headers: Sheets can return a row short when its last cells are blank."""
        row = list(table[number - 1]) if 0 < number <= len(table) else []
        return row + [""] * (len(FINISHED_HEADERS) - len(row))

    def in_finished(table, cid):
        return [n for n, row in enumerate(table, start=1) if n > 1 and row and row[0].strip() == cid]

    def finished_row(cid, price="484.00"):
        return [cid, "John Doe", "Dr. Doe", "J. Smith", price, "", ""]

    def stored_ids():
        return [cid for cid, _ in stored_with_ids(client).values()]

    def try_move(ongoing, finished, cid):
        """Runs the real mover. Returns (the row it returns, None) or (None, the error it raises)."""
        try:
            return move_case_to_finished(ongoing, finished, cid, extra_fields=extras), None
        except Exception as error:      # what it raised is what a check looks at
            return None, error

    check("the four cases are in Ongoing, each with its original stored",
          set(rows_by_id(ws.get_all_values())) == set(ids) and sorted(stored_ids()) == sorted(ids),
          f"stored {stored_ids()}")

    print("  -- S19A: interrupted after the write, then run again")
    _, error = try_move(FailsToDelete(ws), finished_ws, "S19A")
    finished = finished_ws.get_all_values()
    check("S19A: the first attempt fails at the delete, as arranged",
          isinstance(error, RuntimeError) and "API error" in str(error), f"got {error!r}")
    check("S19A: it is in Finished once, with its Date Completed",
          in_finished(finished, "S19A") == [2] and row_at(finished, 2)[date_column] == date_completed,
          f"got {finished[1:]}")
    check("S19A: it is still in Ongoing, with its original stored",
          "S19A" in rows_by_id(ws.get_all_values()) and "S19A" in stored_ids())

    warnings = LogCatcher("app.sheet_case_mover")
    try:
        returned, error = try_move(ws, finished_ws, "S19A")
    finally:
        warnings.close()
    finished = finished_ws.get_all_values()
    check("S19A, run again: it returns the row it was already on", returned == 2,
          f"got {returned}, error {error!r}")
    check("S19A, run again: it is in Finished once, not twice", in_finished(finished, "S19A") == [2],
          f"got {in_finished(finished, 'S19A')}")
    check("S19A, run again: it has left Ongoing, and its original went with its row",
          "S19A" not in rows_by_id(ws.get_all_values()) and "S19A" not in stored_ids())
    check("S19A, run again: it says it completed an interrupted move",
          any("was already in Finished (row 2), so no row was added" in m for m in warnings.messages),
          f"warnings: {warnings.messages}")

    print("  -- S19B: a Finished row with a blank Date Completed is filled in, and nothing else is touched")
    finished_ws.update(range_name="A3:G3", values=[finished_row("S19B")])
    before = row_at(finished_ws.get_all_values(), 3)
    warnings = LogCatcher("app.sheet_case_mover")
    try:
        returned, error = try_move(ws, finished_ws, "S19B")
    finally:
        warnings.close()
    after = finished_ws.get_all_values()
    check("S19B: it returns the row it was on", returned == 3, f"got {returned}, error {error!r}")
    check("S19B: the blank Date Completed is filled in", row_at(after, 3)[date_column] == date_completed,
          f"got {row_at(after, 3)}")
    check("S19B: no other cell of that row changed",
          all(row_at(after, 3)[i] == before[i] for i in range(len(FINISHED_HEADERS)) if i != date_column))
    check("S19B: it has left Ongoing, and is in Finished once",
          "S19B" not in rows_by_id(ws.get_all_values()) and in_finished(after, "S19B") == [3])
    check("S19B: the warning says what was filled in",
          any("Filled in: Date Completed." in m for m in warnings.messages), f"warnings: {warnings.messages}")

    print("  -- S19C: a Finished row that disagrees makes it refuse, and change nothing")
    finished_ws.update(range_name="A4:G4", values=[finished_row("S19C", price="250.00")])
    finished_before, ongoing_before = finished_ws.get_all_values(), ws.get_all_values()
    _, error = try_move(ws, finished_ws, "S19C")
    refusal = str(error) if isinstance(error, ValueError) else None
    check("S19C: it refuses, naming the Price", refusal is not None and "different values for: Price" in refusal,
          f"got {refusal!r}")
    check("S19C: Finished is unchanged", finished_ws.get_all_values() == finished_before)
    check("S19C: it is still in Ongoing, unchanged, with its original stored",
          ws.get_all_values() == ongoing_before and "S19C" in stored_ids())

    print("  -- S19D: a case in Finished twice makes it refuse, and change nothing")
    finished_ws.update(range_name="A5:G6", values=[finished_row("S19D"), finished_row("S19D")])
    finished_before, ongoing_before = finished_ws.get_all_values(), ws.get_all_values()
    _, error = try_move(ws, finished_ws, "S19D")
    refusal = str(error) if isinstance(error, ValueError) else None
    check("S19D: it refuses, naming the rows", refusal is not None and "appears 2 times in Finished (rows 5, 6)" in refusal,
          f"got {refusal!r}")
    check("S19D: Finished is unchanged", finished_ws.get_all_values() == finished_before)
    check("S19D: it is still in Ongoing, unchanged, with its original stored",
          ws.get_all_values() == ongoing_before and "S19D" in stored_ids())

    print("  -- a sweep afterwards")
    report = apply_live(stand_in, work)
    state = look_at_moved(ws, client)
    check("the cases that were moved are not in the report",
          "S19A" not in report and "S19B" not in report, report[:400])
    check("the two that were refused are still swept as usual",
          all("add 96130-CARS-2" in case_block(report, c) for c in ("S19C", "S19D")), report[:500])
    check("their stored originals are on their own rows",
          all(state["stored"].get(state["where"].get(c)) == (c, ORIGINAL_TESTS) for c in ("S19C", "S19D")),
          f"got {state['stored']}, cases at {state['where']}")
    check("the lock was given back", not os.path.exists(lock_path))


# The scenarios share sweeps, so they run in groups: (name, the scenarios in it, its function).
GROUPS = [
    ("chain", (1, 2, 3), check_chain),
    ("flags", (4, 5, 10), check_flags),
    ("nothing-stored", (11, 12, 13, 14), check_nothing_stored),
    ("rewrites", (6, 7, 8, 9), check_rewrites),
    ("moves", (15, 16), check_moves),
    ("interrupted-move", (19,), check_interrupted_moves),
]


def choose_groups(only):
    """
    The groups to run, in their usual order. `only` is a comma-separated list of
    scenario numbers and group names, or None for all of them. Raises ValueError
    for something that is neither.
    """
    if not only:
        return list(GROUPS)
    wanted = set()
    for token in (t.strip() for t in only.split(",") if t.strip()):
        matches = [g for g in GROUPS if token == g[0] or (token.isdigit() and int(token) in g[1])]
        if not matches:
            known = ", ".join(str(n) for n in sorted(n for g in GROUPS for n in g[1]))
            raise ValueError(f"'{token}' is not a scenario number or a group name. "
                             f"Scenarios: {known}. Groups: {', '.join(g[0] for g in GROUPS)}.")
        wanted.update(g[0] for g in matches)
    return [g for g in GROUPS if g[0] in wanted]


def describe_groups():
    lines = ["The scenarios share sweeps, so they run in groups:"]
    for name, scenarios, _ in GROUPS:
        lines.append(f"  {name:15} scenarios {', '.join(str(n) for n in scenarios)}")
    lines.append("Use --only with scenario numbers or group names, for example: --only 6,15  or  --only moves")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Live check of the sweep, on scratch tabs in the sandbox sheet.")
    parser.add_argument("--only", help="run only these scenarios or groups, comma-separated (see --list)")
    parser.add_argument("--list", action="store_true", help="show the groups and exit, without touching Google")
    args = parser.parse_args([] if argv is None else argv)

    if args.list:
        print(describe_groups())
        return 0
    try:
        chosen = choose_groups(args.only)
    except ValueError as error:
        print(error)
        return 2

    sh = get_client()
    print(f"Sandbox spreadsheet: {sh.title}")
    print(f"This run creates tabs whose names start with '{PREFIX.strip()}' and deletes "
          "them at the end. It touches nothing else.")
    print("It keeps to Google's request limit, so it takes a few minutes.")
    print("Running: " + "; ".join(f"{name} (scenarios {', '.join(str(n) for n in nums)})"
                                  for name, nums, _ in chosen) + "\n")
    asked = {t.strip() for t in (args.only or "").split(",") if t.strip().isdigit()}
    for name, nums, _ in chosen:
        also = [str(n) for n in nums if str(n) not in asked]
        if asked and also and any(str(n) in asked for n in nums):
            print(f"  (the scenarios in '{name}' share their sweeps, so {', '.join(also)} run too)")

    client = getattr(sh, "client", None)
    if client is None or not hasattr(client, "request"):
        print("  (could not find the Google client to pace, so a long run may hit the request limit)")
    else:
        install_pacing(client, Pacer())

    remove_leftovers(sh)
    created = []
    try:
        services_ws = make_tab(sh, created, SCRATCH_SERVICES, SERVICES_HEADERS, rows=30)
        write_table(services_ws, [SERVICES_HEADERS] + SERVICES)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            env = build_env(sh, created, services_ws, work)
            print("Setup: the stand-in spreadsheet and the scratch Services tab")
            check_setup(env)
            for name, nums, function in chosen:
                print(f"\n=== {name}: scenarios {', '.join(str(n) for n in nums)} ===")
                function(env)
    finally:
        for tab in created:
            sh.del_worksheet(tab)
        print("\nScratch tabs deleted.")

    left = [w.title for w in sh.worksheets() if w.title.startswith(PREFIX)]
    check("no scratch tab is left behind", left == [], f"left {left}")
    print(f"\n{sum(results)} of {len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))