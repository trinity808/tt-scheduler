"""
Turns the result of run_sweep() into plain text for the console.

Kept apart from the sweep itself so the wording can be tested without
capturing console output: format_report() takes a SweepReport (see
apply_comment_updates.py) and returns a string.

Order: problems with the Services tab first, since they affect every
case below, then each case, then a summary.

Test changes (Add and Change) are listed in plain words but described
as "not applied to the sheet yet", since applying them is a later step.
"""

_SERVICES_FLAG_NOTES = {
    "flagged": "(noted on the Services tab)",
    "unchanged": "(already noted on the Services tab)",
    "skipped": "(couldn't note this on the Services tab: "
               "cell A1 already has someone else's note)",
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


def _case_section(result):
    parsed = result["parsed"]
    lines = [f"Case {result['case_id']}:"]

    if not result["fields"] and not parsed.unrecognized_lines:
        lines.append("  No Provider/Psychometrist in comment")

    for field, (status, old, new) in result["fields"].items():
        if status == "updated" and old:
            lines.append(f"  {field}: updated from {old} to {new}")
        elif status == "updated":
            lines.append(f"  {field}: set to {new}")
        else:
            lines.append(f"  {field}: already {new}, no change")

    if parsed.service_changes:
        lines.append("  Test changes found (not applied to the sheet yet):")
        lines += [f"    {_describe_change(c)}" for c in parsed.service_changes]

    if parsed.unrecognized_lines:
        lines.append("  NEEDS ATTENTION:")
        lines += [f"    {line}" for line in parsed.unrecognized_lines]

    return lines


def format_report(report):
    sections = []

    services = _services_section(report)
    if services:
        sections.append(services)

    if not report.results:
        sections.append(["No rows had a comment to process."])
        return "\n\n".join("\n".join(section) for section in sections)

    fields_updated = 0
    changes_found = 0
    flagged = []

    for result in report.results:
        parsed = result["parsed"]
        sections.append(_case_section(result))
        fields_updated += sum(
            1 for status, _, _ in result["fields"].values() if status == "updated"
        )
        changes_found += len(parsed.service_changes)
        if parsed.unrecognized_lines:
            flagged.append(result["case_id"])

    summary = (
        f"{len(report.results)} comment(s) processed, "
        f"{fields_updated} field(s) updated, "
        f"{len(flagged)} flagged for attention."
    )
    if changes_found:
        summary += f" {changes_found} test change(s) found, not applied to the sheet yet."
    summary_lines = [summary]
    if flagged:
        summary_lines.append("Flagged cases: " + ", ".join(flagged))
    sections.append(summary_lines)

    return "\n\n".join("\n".join(section) for section in sections)