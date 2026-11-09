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
Services tab, scenario 1 (previewed, applied, applied again), and scenarios 2
and 3, which edit the comment and sweep again.

Run from the project root:
    python -m scripts.check_sweep_live
"""

import collections
import contextlib
import functools
import io
import logging
import os
import sys
import tempfile
import time

import gspread
from gspread.exceptions import APIError
from gspread.utils import rowcol_to_a1

from app.apply_comment_updates import ONGOING_TAB, main as sweep_main
from app.case_metadata import read_all_case_tests, set_case_tests
from app.services_cell import ServiceLine, render_services_cell
from app.services_tab import SERVICES_TAB, load_services_tab
from app.sheet_client import get_client
from app.sheets_metadata_client import SheetsMetadataClient


PREFIX = "SweepCheck "
SCRATCH_ONGOING = PREFIX + "Ongoing"
SCRATCH_SERVICES = PREFIX + "Services"

ONGOING_HEADERS = [
    "Case ID", "Patient Name", "Provider", "Psychometrist",
    "Services Requested", "Price", "Comments",
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

# One case per scenario: (scenario number, Case ID, the original tests, the comment).
CASES = [
    (1, "S01", ["96101-WISC-V", "90791", "96130-AUTISM"],
     "Provider: Dr. Doe\nPsychometrist: J. Smith\nAdd: wms"),
]

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    line = f"  [{'OK' if ok else 'FAILED'}] {label}"
    if not ok and detail:
        line += f"  ->  {detail}"
    print(line)


# --- Keeping to Google's request limit ---------------------------------


class Pacer:
    """Waits, when it has to, so that no more than `limit` requests go out in any `window` seconds."""

    def __init__(self, limit=45, window=60.0, clock=time.monotonic, sleep=time.sleep, say=print):
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


def install_pacing(client, pacer, retries=3, pause=30.0, sleep=time.sleep, say=print):
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


def run_checks(sh, ws, services_ws, work):
    comments_cell = rowcol_to_a1(2, ONGOING_HEADERS.index("Comments") + 1)
    lock_path = os.path.join(work, "sweep.lock")

    print("1. Scratch tabs, and the cases written to them")
    write_table(services_ws, [SERVICES_HEADERS] + SERVICES)
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

    print("\n2. The stand-in spreadsheet hands the sweep only scratch tabs")
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

    print("\n3. The scratch Services tab, read by the sweep's own code")
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

    print("\n4. Scenario 1, previewed: it must say what would change, and write nothing")
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

    print("\n5. Scenario 1, applied for real")
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

    print("\n6. Scenario 1, applied again: it must change nothing")
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

    print("\n7. Scenario 2, Tuesday: the provider is edited and a Change line is added")
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

    print("\n8. Scenario 2, Wednesday: one more line, Add: cars")
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

    print("\n9. Scenario 3: changing back, Change: wais to autism")
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


def main():
    sh = get_client()
    print(f"Sandbox spreadsheet: {sh.title}")
    print(f"This run creates tabs whose names start with '{PREFIX.strip()}' and deletes "
          "them at the end. It touches nothing else.")
    print("It keeps to Google's request limit, so it takes a few minutes.\n")

    client = getattr(sh, "client", None)
    if client is None or not hasattr(client, "request"):
        print("  (could not find the Google client to pace, so a long run may hit the request limit)")
    else:
        install_pacing(client, Pacer())

    remove_leftovers(sh)
    created = []
    try:
        ws = sh.add_worksheet(title=SCRATCH_ONGOING, rows=20, cols=len(ONGOING_HEADERS))
        created.append(ws)
        services_ws = sh.add_worksheet(title=SCRATCH_SERVICES, rows=30, cols=len(SERVICES_HEADERS))
        created.append(services_ws)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as work:
            run_checks(sh, ws, services_ws, work)
    finally:
        for tab in created:
            sh.del_worksheet(tab)
        print("\nScratch tabs deleted.")

    left = [w.title for w in sh.worksheets() if w.title.startswith(PREFIX)]
    check("no scratch tab is left behind", left == [], f"left {left}")
    print(f"\n{sum(results)} of {len(results)} checks passed.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())