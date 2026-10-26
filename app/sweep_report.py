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
"""

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
                "  Tests: descriptions or markers would be rewritten, no tests change"
                if dry_run
                else "  Tests: descriptions or markers rewritten, no tests changed"
            )
    elif status == "unchanged" and parsed.service_changes and not _flags(result):
        lines.append("  Tests: already up to date, no change")

    if update is not None and update.original_to_store is not None and status != "store_failed":
        verb = "would be saved" if dry_run else "saved"
        lines.append(f"  Original tests {verb} from the sheet: " + ", ".join(update.original_to_store))

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

    fields_updated = 0
    changes_found = 0
    tests_updated = 0
    originals_saved = 0
    flagged = []

    for result in report.results:
        sections.append(_case_section(result, dry_run))
        fields_updated += sum(
            1 for status, _, _ in result["fields"].values() if status == "updated"
        )
        status = _status(result)
        if status == "not_applied":
            changes_found += len(result["parsed"].service_changes)
        if status == "updated":
            tests_updated += 1
        services_result = result.get("services")
        update = services_result["update"] if services_result else None
        if update is not None and update.original_to_store is not None and status != "store_failed":
            originals_saved += 1
        if _flags(result):
            flagged.append(result["case_id"])

    summary = (
        f"{len(report.results)} comment(s) processed, "
        f"{fields_updated} field(s) {'would be updated' if dry_run else 'updated'}, "
        f"{len(flagged)} flagged for attention."
    )
    if changes_found:
        summary += f" {changes_found} test change(s) found, not applied to the sheet yet."
    if tests_updated:
        summary += f" {tests_updated} case(s) {'would have' if dry_run else 'had'} their tests updated."
    if originals_saved:
        summary += f" {originals_saved} original list(s) {'would be saved' if dry_run else 'saved'}."
    summary_lines = [summary]
    if flagged:
        summary_lines.append("Flagged cases: " + ", ".join(flagged))
    if dry_run:
        summary_lines.append(f"To apply this for real, run: {APPLY_COMMAND}")
    sections.append(summary_lines)

    return "\n\n".join("\n".join(section) for section in sections)