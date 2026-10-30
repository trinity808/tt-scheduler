"""
Sweeps the Ongoing tab, applying OA's daily comment updates
(Provider, Psychometrist, and test changes) and flagging comments that
need attention.

Reads the whole tab in one call rather than one lookup per row,
same reasoning as find_case_row() elsewhere in this codebase: avoids
hitting Sheets API rate limits when scanning many cases in a single
sweep.

Rows without a Case ID are skipped entirely, comment and all. OA may
keep a reusable comment template in spare rows, and that isn't meant
to apply to any case.

Comments are left untouched after processing. Since this may be run
repeatedly against the same unchanged comment, Provider and
Psychometrist are only written if they'd actually change. Each run
records what happened to each field (updated, or already set), so
the console reports real changes rather than just what the comment
says.

Test changes (Add and Change) are resolved through the lookup when one
is given, so unknown or ambiguous tests are flagged on the comment
like any other problem. They are applied to the Services Requested
cell only when a metadata client is also given, because applying them
needs each case's stored original tests (see case_metadata.py). Without
one, they are reported but not applied, exactly as before. The rules for
what the cell becomes live in services_update.py.

A case's tests are looked at only when its comment has test changes or
its cell already shows applied changes (a line ending in "(added)" or
"(changed from ...)"). The second matters when OA clears or rewrites a
comment: the changes it once asked for are still in the cell, so the
case is checked and flagged instead of silently drifting. A comment
that is empty and has no applied changes in the cell is skipped.

When nothing is stored for a case and its cell is clean, the cell is
adopted as the original. That original is saved BEFORE the cell is
written, so a failed save leaves the cell alone. The other order could
leave a changed cell with no original, which can never be repaired.

Price is never touched.

run_sweep() is the single entry point: it loads the Services tab,
flags any problems on it, and sweeps the Ongoing tab with the lookup.

With dry_run, everything is worked out and reported but nothing is
written: no field, no Services Requested cell, no saved original, and
no flag or note on any tab. The functions here write by default, since
that is their job. The command line is the opposite: it previews unless
told to write.

Run from the command line:
    python -m app.apply_comment_updates          preview, writes nothing
    python -m app.apply_comment_updates --live   apply for real
    add --pause to wait for Enter at the end ("Press Enter to close"),
    for a double-clicked shortcut whose window would otherwise vanish.
It writes logs/sweep.log, and logs/sweep_error.log for failures: when it
started and in which mode, the whole report as one record (a warning if any
case needs attention), and when it finished, with counts. A failure is logged
with its traceback, the usual alert email is sent if emails are enabled, and
the error is raised again, so the process still exits with an error. These are
separate from the DOCX pipeline's files, since two processes must not write to
one rotating file. The report is also printed, as always.

It never waits unless --pause is given. Whether standard input "is a
terminal" can't be trusted to decide this: on Windows an empty input
counts as one, and so does the console a scheduled task is given. A
scheduled run must not use --pause. Even with it, a closed or empty
input is ignored and doesn't crash the run.

Anything worth attention is flagged directly on the Comments cell
(highlight + note): the parser's unrecognized lines, and any problem
applying tests. The flag is cleared automatically once the comment is
fixed and everything applies cleanly, since it reflects the comment's
current state, not a one-time event.
"""

import argparse
import logging
import sys
import time
from dataclasses import dataclass

from gspread.utils import rowcol_to_a1

from app.case_metadata import (
    original_tests_for_row,
    read_all_case_tests,
    set_case_tests,
)
from app.comment_parser import parse_comment
from app.config_loader import load_config
from app.health_monitor import notify_error, setup_logging
from app.services_cell import parse_services_cell
from app.services_tab import ServicesTab, flag_services_tab, load_services_tab
from app.services_update import build_final_services
from app.sheet_client import get_header_map, update_case_field


ONGOING_TAB = "Ongoing"
SWEEP_LOG = "sweep.log"
SWEEP_ERROR_LOG = "sweep_error.log"
SERVICES_COLUMN = "Services Requested"


# Orange, distinct from the yellow used to flag duplicate cases in
# ongoing_row_mapper.py, so a row can carry both flags without them
# reading as the same kind of issue.
COMMENT_ISSUE_COLOR = {"red": 1.0, "green": 0.8, "blue": 0.6}
# Plain white, the assumed "no flag" state. Doesn't attempt to
# restore whatever the sheet's actual prior formatting was, a
# simplification worth knowing about if the sheet ever uses banding
# or a non-white theme.
CLEAR_COLOR = {"red": 1.0, "green": 1.0, "blue": 1.0}


def flag_comment_issues(worksheet, cell, unrecognized_lines):
    """
    Highlights the given cell and attaches a note listing each issue
    if unrecognized_lines is non-empty. Clears the highlight and note
    if it's empty.
    """
    if unrecognized_lines:
        worksheet.format(cell, {"backgroundColor": COMMENT_ISSUE_COLOR})
        note_text = "Comment needs attention:\n" + "\n".join(unrecognized_lines)
        worksheet.update_note(cell, note_text)
    else:
        worksheet.format(cell, {"backgroundColor": CLEAR_COLOR})
        worksheet.clear_note(cell)


def _services_outcome(status, update=None, detail="", before=None):
    """
    What happened to a case's tests. status is one of:
      not_applied        no metadata client, so test changes are only reported
      no_column          the sheet has no Services Requested column
      none               nothing to do for this case
      unusable_original  a stored original exists but can't be trusted
      refused            the cell can't be changed safely (see the update's flags)
      store_failed       the original couldn't be saved, so the cell was left alone
      unchanged          the cell already matches
      updated            the cell was rewritten (in a dry run: would be)

    "before" is the cell's lines as they were, so a report can say what
    this run changed and not only what the cell says now.
    """
    return {"status": status, "update": update, "detail": detail, "before": before}


def _apply_services(
    worksheet, row_number, case_id, changes, cell_text, lookup, metadata, stored,
    dry_run=False,
):
    """Applies one case's test changes. Returns (outcome, flags)."""
    entry, problem = original_tests_for_row(stored, row_number, case_id)
    if problem:
        return (
            _services_outcome("unusable_original", detail=problem),
            [f"{SERVICES_COLUMN}  (not changed: {problem})"],
        )

    # No entry (and no problem) means nothing was stored for this case.
    original = entry.tests if entry else None
    update = build_final_services(original, changes, cell_text, lookup)
    flags = list(update.flags)
    before = parse_services_cell(cell_text).lines

    if update.status == "refused":
        return _services_outcome("refused", update, before=before), flags

    # Save an adopted original first. If this fails the cell stays as it is.
    if update.original_to_store is not None and not dry_run:
        try:
            set_case_tests(metadata, row_number, case_id, update.original_to_store)
        except Exception as error:
            flags.append(
                f"{SERVICES_COLUMN}  (not changed: this case's original tests "
                f"couldn't be saved: {error})"
            )
            return _services_outcome("store_failed", update, str(error), before), flags

    if update.status == "update":
        if not dry_run:
            update_case_field(worksheet, row_number, SERVICES_COLUMN, update.text)
        return _services_outcome("updated", update, before=before), flags

    return _services_outcome("unchanged", update, before=before), flags


def apply_comment_updates(worksheet, lookup=None, metadata=None, dry_run=False):
    """
    Reads every case row's Comments cell, parses it, applies
    Provider/Psychometrist where present and changed, applies test
    changes to the Services Requested cell, and flags the Comments cell
    if anything needs attention.

    `lookup` is a ServiceLookup (see service_lookup.py). With one,
    Add and Change lines are resolved to real test codes; without one,
    they're flagged as "services list not loaded". Provider and
    Psychometrist don't depend on it.

    `metadata` is a client for the hidden per-row storage (see
    case_metadata.py). With one, test changes are applied to the
    Services Requested cell. Without one, they're only reported.

    With `dry_run`, nothing is written, but every outcome is worked out
    and returned as if it had been. "updated" then means "would be".

    Returns a list of dicts, one per case row that was looked at:
        {
            "case_id": "15419897",
            "parsed": ParsedComment(...),
            "fields": {
                "Provider": ("updated", old_value, new_value),
                "Psychometrist": ("unchanged", old_value, new_value),
            },
            "flags": ["...everything flagged on the Comments cell, in order..."],
            "services": {"status": "updated", "update": ServicesUpdate, "detail": ""},
        }
    "fields" only contains fields the comment actually mentioned.
    "services" says what happened to the case's tests; see
    _services_outcome() for the statuses.
    """
    header_map = get_header_map(worksheet)

    for required in ("Comments", "Case ID", "Provider", "Psychometrist"):
        if required not in header_map:
            raise ValueError(f"'{required}' column not found in sheet headers")

    all_values = worksheet.get_all_values()
    comments_idx = header_map["Comments"] - 1
    case_id_idx = header_map["Case ID"] - 1
    provider_idx = header_map["Provider"] - 1
    psychometrist_idx = header_map["Psychometrist"] - 1
    services_idx = header_map[SERVICES_COLUMN] - 1 if SERVICES_COLUMN in header_map else None

    applying = metadata is not None and services_idx is not None
    stored = read_all_case_tests(metadata) if applying else None

    results = []

    for row_number, row in enumerate(all_values[1:], start=2):
        case_id = row[case_id_idx].strip()
        if not case_id:
            continue

        comment_text = row[comments_idx]
        cell_text = row[services_idx] if services_idx is not None and services_idx < len(row) else ""
        has_applied_changes = applying and parse_services_cell(cell_text).has_markers()

        # An empty comment is normally skipped. But when earlier test
        # changes still show in the cell, the comment that asked for them
        # is gone, so the case is looked at. To ignore those instead,
        # delete the "and not has_applied_changes" part.
        if not comment_text.strip() and not has_applied_changes:
            continue

        parsed = parse_comment(comment_text, lookup)

        field_outcomes = {}
        for field, new_value, idx in (
            ("Provider", parsed.provider, provider_idx),
            ("Psychometrist", parsed.psychometrist, psychometrist_idx),
        ):
            if not new_value:
                continue
            old_value = row[idx] if idx < len(row) else ""
            if new_value == old_value:
                field_outcomes[field] = ("unchanged", old_value, new_value)
            else:
                if not dry_run:
                    update_case_field(worksheet, row_number, field, new_value)
                field_outcomes[field] = ("updated", old_value, new_value)

        flags = list(parsed.unrecognized_lines)

        if metadata is None:
            services = _services_outcome("not_applied")
        elif services_idx is None:
            services = _services_outcome("no_column")
        elif not parsed.service_changes and not has_applied_changes:
            services = _services_outcome("none")
        else:
            services, services_flags = _apply_services(
                worksheet, row_number, case_id, parsed.service_changes,
                cell_text, lookup, metadata, stored, dry_run,
            )
            flags += services_flags

        if not dry_run:
            comments_cell = rowcol_to_a1(row_number, header_map["Comments"])
            flag_comment_issues(worksheet, comments_cell, flags)

        results.append({
            "case_id": case_id,
            "parsed": parsed,
            "fields": field_outcomes,
            "flags": flags,
            "services": services,
        })

    return results


@dataclass
class SweepReport:
    results: list          # per-case results from apply_comment_updates()
    services: ServicesTab  # what loading the Services tab produced
    services_flag: str     # what flag_services_tab() did: "flagged", "cleared",
                           # "unchanged", "skipped" or "no_tab". In a dry run it
                           # is "dry_run" (nothing was noted), or "no_tab".
    dry_run: bool = False  # True if nothing was written


def run_sweep(spreadsheet, metadata=None, dry_run=False):
    """
    One full pass: load the Services tab, flag any problems on it, then
    sweep the Ongoing tab with the resulting lookup.

    Pass a metadata client to apply test changes to the sheet. Without
    one they are only reported. With dry_run, nothing is written
    anywhere, the Services tab included.

    A Services tab that can't be used never stops the sweep. Provider
    and Psychometrist still apply, and test lines are flagged as
    "services list not loaded". A missing Ongoing tab does stop it
    (WorksheetNotFound), since there's nothing to sweep.
    """
    services = load_services_tab(spreadsheet)
    if dry_run:
        services_flag = "no_tab" if services.worksheet is None else "dry_run"
    else:
        services_flag = flag_services_tab(services.worksheet, services.problems)
    results = apply_comment_updates(
        spreadsheet.worksheet(ONGOING_TAB), services.lookup, metadata, dry_run
    )
    return SweepReport(
        results=results, services=services, services_flag=services_flag, dry_run=dry_run
    )


def main(argv=None, spreadsheet=None, metadata=None, pause=None, config=None):
    """
    The command line: sweep the Ongoing tab, print a report, and log the run.

    Previews by default, and writes only with --live. `spreadsheet`,
    `metadata`, and `config` are for tests; left out, they are the real ones.
    `pause` decides whether to wait for Enter at the end; left out, it follows
    the --pause flag, which is off unless given.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Apply OA's comments to the Ongoing tab. Previews what would "
            "change by default."
        )
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Actually write the changes. Without this, nothing is written.",
    )
    parser.add_argument(
        "--pause",
        action="store_true",
        help="Wait for Enter before closing, so a double-clicked window stays open.",
    )
    args = parser.parse_args(argv)
    mode = "live" if args.live else "preview"

    # Logging comes first, so even a failure to read the settings or to reach
    # Google leaves a trace.
    config_problem = None
    if config is None:
        try:
            config = load_config()
        except Exception as error:
            config, config_problem = {}, error
    logger = setup_logging(config, SWEEP_LOG, SWEEP_ERROR_LOG)
    if config_problem:
        logger.warning(
            "Could not read config/settings.json (%s), so the default log "
            "folder is used.",
            config_problem,
        )
    logger.info("Sweep started (%s)", mode)
    started = time.monotonic()

    try:
        if spreadsheet is None:
            from app.sheet_client import get_client

            spreadsheet = get_client()
        if metadata is None:
            from app.sheets_metadata_client import SheetsMetadataClient

            metadata = SheetsMetadataClient(spreadsheet.worksheet(ONGOING_TAB))

        from app.sweep_report import format_report, summary_counts, summary_line

        report = run_sweep(spreadsheet, metadata, dry_run=not args.live)
        text = format_report(report)

        flagged = summary_counts(report).flagged
        if flagged:
            logger.warning(
                "Sweep report (%s), %s case(s) need attention:\n%s", mode, len(flagged), text
            )
        else:
            logger.info("Sweep report (%s):\n%s", mode, text)
        logger.info(summary_line(report, time.monotonic() - started))

        print(text)
    except Exception as error:
        logger.exception("Sweep failed (%s)", mode)
        notify_error("Sweep failed", f"The sweep failed ({mode}): {error}", config, sys.exc_info())
        raise

    if pause is None:
        pause = args.pause
    if pause:
        try:
            input("\nPress Enter to close...")
        except EOFError:
            pass  # nothing to read from (closed or empty input): just finish


if __name__ == "__main__":
    main()