"""
Turns the result of run_sweep() into plain text for the console.

Kept apart from the sweep itself so the wording can be tested without
capturing console output: format_report() takes a SweepReport (see
apply_comment_updates.py) and returns a string.

Order: a preview banner if nothing was written, problems with the Services
tab, then each case, then a summary.

For each case it says what this run changed, not what the cell says now:
which tests were added, swapped, restored, or removed compared with the cell
as it was. A case that was already up to date says so. The Comments cell's
own note carries the full detail of anything flagged.

In a preview (a dry run) the same report is written in the conditional,
"would be updated", and ends with the command that applies it for real.

A result built before tests could be applied, with no "services" or "flags"
entry, still formats: its test changes are listed as not applied.

The numbers in the summary come from summary_counts(), so anything else that
reports a run, such as the log line, can use the same numbers and never
disagree with the report.
"""

from dataclasses import dataclass, field

APPLY_COMMAND = "python -m app.apply_comment_updates --live"

_SERVICES_FLAG_NOTES = {
    "flagged": "(noted on the Services tab)",
    "unchanged": "(already noted on the Services tab)",
    "skipped": "(couldn't note this on the Services tab: "
               "cell A1 already has someone else's note)",
    "dry_run": "(dry run: not noted on the Services tab)",
    "no_tab": "",  # the problem itself already says the tab is missing
}


def _services_section(report):
    problems = report.services.problems
    if problems:
        lines = ["Services tab needs attention:"]
        lines += [f"  - {problem}" for problem in problems]
        note = _SERVICES_FLAG_NOTES.get(report.services_flag, "")
        if note:
            lines.append(f"  {note}")
        return lines
    if report.services_flag == "cleared":
        return ["Services tab problems are fixed, so its note was cleared."]
    return []


def _describe_change(change):
    if change.action == "change":
        return f"change {change.replaces} to {change.code}"
    return f"{change.action} {change.code}"


def _changes_this_run(before, after):
    """
    What differs between the cell as it was and as it is now, as plain
    phrases. A new test in the place of one that went is a swap, a new test
    at the end is an add, and a test that went with nothing in its place is
    a removal. Anything else new is an original test coming back.
    """
    before_codes = [line.code for line in before]
    after_codes = [line.code for line in after]
    unpaired_gone = [code for code in before_codes if code not in after_codes]

    phrases = []
    for line in after:
        if line.code in before_codes:
            continue
        place = after_codes.index(line.code)
        old = before_codes[place] if place < len(before_codes) else None
        if old in unpaired_gone:
            phrases.append(f"change {old} to {line.code}")
            unpaired_gone.remove(old)
        elif line.marker == "added":
            phrases.append(f"add {line.code}")
        else:
            phrases.append(f"restore {line.code}")
    phrases += [f"remove {code}" for code in unpaired_gone]
    return phrases


def _flags(result):
    return result.get("flags", result["parsed"].unrecognized_lines)


def _status(result):
    services = result.get("services")
    return services["status"] if services else "not_applied"


def _saved_original(result):
    """The original tests this run saved (or would save) for a case, or None."""
    services = result.get("services")
    if not services or services["status"] == "store_failed":
        return None
    update = services["update"]
    return update.original_to_store if update is not None else None


def _test_lines(result, dry_run):
    parsed = result["parsed"]
    status = _status(result)

    if status == "not_applied":
        if not parsed.service_changes:
            return []
        lines = ["  Test changes found (not applied to the sheet yet):"]
        lines += [f"    {_describe_change(c)}" for c in parsed.service_changes]
        return lines

    services = result["services"]
    update = services["update"]
    lines = []

    if status == "updated":
        phrases = _changes_this_run(services["before"] or [], update.lines)
        if phrases:
            lines.append("  Tests that would be updated:" if dry_run else "  Tests updated:")
            lines += [f"    {phrase}" for phrase in phrases]
        else:
            lines.append(
                "  Tests: cell would be rewritten (wording, markers or layout), no tests change"
                if dry_run
                else "  Tests: cell rewritten (wording, markers or layout), no tests changed"
            )
    elif status == "unchanged" and parsed.service_changes and not _flags(result):
        lines.append("  Tests: already up to date, no change")

    saved = _saved_original(result)
    if saved is not None:
        verb = "would be saved" if dry_run else "saved"
        lines.append(f"  Original tests {verb} from the sheet: " + ", ".join(saved))

    return lines


def _case_section(result, dry_run):
    flags = _flags(result)
    test_lines = _test_lines(result, dry_run)
    lines = [f"Case {result['case_id']}:"]

    if not result["fields"] and not flags and not test_lines:
        lines.append("  No Provider/Psychometrist in comment")

    for field, (status, old, new) in result["fields"].items():
        if status == "updated" and old:
            verb = "would be updated" if dry_run else "updated"
            lines.append(f"  {field}: {verb} from {old} to {new}")
        elif status == "updated":
            verb = "would be set" if dry_run else "set"
            lines.append(f"  {field}: {verb} to {new}")
        else:
            lines.append(f"  {field}: already {new}, no change")

    lines += test_lines

    if flags:
        lines.append("  NEEDS ATTENTION:")
        lines += [f"    {line}" for line in flags]

    return lines


@dataclass
class SummaryCounts:
    comments: int = 0         # cases looked at
    fields_updated: int = 0   # Provider/Psychometrist values set (or that would be)
    changes_found: int = 0    # test changes found but not applied (no metadata client)
    tests_updated: int = 0    # cases whose tests were updated (or would be)
    originals_saved: int = 0  # original test lists saved (or that would be)
    flagged: list = field(default_factory=list)  # Case IDs needing attention


def summary_counts(report):
    """The numbers behind a report's summary. Preview or live, they count alike."""
    counts = SummaryCounts(comments=len(report.results))

    for result in report.results:
        counts.fields_updated += sum(
            1 for status, _, _ in result["fields"].values() if status == "updated"
        )
        status = _status(result)
        if status == "not_applied":
            counts.changes_found += len(result["parsed"].service_changes)
        if status == "updated":
            counts.tests_updated += 1
        if _saved_original(result) is not None:
            counts.originals_saved += 1
        if _flags(result):
            counts.flagged.append(result["case_id"])

    return counts


def format_report(report):
    dry_run = getattr(report, "dry_run", False)
    sections = []

    if dry_run:
        sections.append(["DRY RUN: nothing was written to the sheet."])

    services = _services_section(report)
    if services:
        sections.append(services)

    if any(_status(result) == "no_column" for result in report.results):
        sections.append([
            "Test changes can't be applied: the Ongoing tab has no "
            "'Services Requested' column."
        ])

    if not report.results:
        sections.append(["No rows had a comment to process."])
        return "\n\n".join("\n".join(section) for section in sections)

    for result in report.results:
        sections.append(_case_section(result, dry_run))

    counts = summary_counts(report)
    flagged = counts.flagged
    summary_lines = [
        f"{counts.comments} comment(s) processed, "
        f"{counts.fields_updated} field(s) {'would be updated' if dry_run else 'updated'}, "
        f"{len(flagged)} flagged for attention."
    ]
    if counts.changes_found:
        summary_lines.append(
            f"{counts.changes_found} test change(s) found, not applied to the sheet yet."
        )
    if counts.tests_updated:
        summary_lines.append(
            f"{counts.tests_updated} case(s) {'would have' if dry_run else 'had'} their tests updated."
        )
    if counts.originals_saved:
        summary_lines.append(
            f"{counts.originals_saved} original list(s) {'would be saved' if dry_run else 'saved'}."
        )
    if flagged:
        summary_lines.append("Flagged cases: " + ", ".join(flagged))
    if dry_run:
        summary_lines.append(f"To apply this for real, run: {APPLY_COMMAND}")
    sections.append(summary_lines)

    return "\n\n".join("\n".join(section) for section in sections)


def summary_line(report, seconds):
    """
    One line saying what a run did, for a log. It uses the same counts as the
    report's summary, so the two can't disagree.
    """
    counts = summary_counts(report)
    dry_run = getattr(report, "dry_run", False)

    parts = [
        f"{counts.comments} comment(s)",
        f"{counts.fields_updated} field(s) {'would be updated' if dry_run else 'updated'}",
        f"{len(counts.flagged)} flagged",
    ]
    if counts.changes_found:
        parts.append(f"{counts.changes_found} test change(s) found, not applied")
    if counts.tests_updated:
        parts.append(
            f"{counts.tests_updated} case(s) with tests "
            f"{'that would be updated' if dry_run else 'updated'}"
        )
    if counts.originals_saved:
        parts.append(
            f"{counts.originals_saved} original list(s) "
            f"{'that would be saved' if dry_run else 'saved'}"
        )
    parts.append(f"{seconds:.1f}s")

    mode = "preview" if dry_run else "live"
    return f"Sweep finished ({mode}): " + ", ".join(parts)