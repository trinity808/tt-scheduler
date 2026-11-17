"""
Turns a RunPlan (see reminders.py) into plain text for the console and the log.

Kept apart from the planning so the wording can be tested without capturing
console output: format_report() takes a RunPlan and returns a string.

Order: a banner saying nothing is sent, the reminders that would go out, anything
needing attention, reminders held back, reminders already sent, notes about the
sheet, then a summary. Email addresses never appear, only names.

The numbers in the summary come from summary_counts(), so the log line can use
the same numbers and never disagree with the report.
"""

from dataclasses import dataclass


def _long_date(day):
    return f"{day:%A}, {day:%B} {day.day}, {day.year}"


@dataclass
class SummaryCounts:
    looked_at: int
    to_send: int
    problems: int
    already_sent: int
    not_sent: int
    finished: int
    nothing_due: int


def summary_counts(plan):
    return SummaryCounts(
        looked_at=plan.looked_at,
        to_send=len(plan.to_send),
        problems=len(plan.problems),
        already_sent=len(plan.already_sent),
        not_sent=len(plan.not_sent),
        finished=plan.finished,
        nothing_due=plan.nothing_due,
    )


def _who(planned):
    name = planned.case.provider_name
    return f"to {name}" if name else "to the provider"


def format_report(plan, show_text=False, today_given=False):
    lines = [
        f"Reminders for {_long_date(plan.today)} (Arizona time)"
        + (", the date given with --today" if today_given else ""),
        "PREVIEW: nothing was sent, and nothing was written to the sheet.",
        "",
    ]

    if plan.to_send:
        lines.append(f"Would send ({len(plan.to_send)}):")
        for planned in plan.to_send:
            cc = ", director copied" if planned.reminder.cc_director else ""
            lines.append(
                f"  Case {planned.case.case_id}: {planned.reminder.label} {_who(planned)}{cc} "
                f"(exam {_long_date(planned.exam_date)})"
            )
            lines.append(f"    Subject: {planned.subject}")
            if show_text:
                lines += [f"    | {line}" if line else "    |" for line in planned.body.split("\n")]
        lines.append("")
    else:
        lines += ["Would send: nothing today.", ""]

    if plan.problems:
        lines.append(f"Needs attention ({len(plan.problems)}):")
        lines += [f"  - {problem}" for problem in plan.problems]
        lines.append("")

    if plan.not_sent:
        lines.append(f"Due but not sent ({len(plan.not_sent)}):")
        lines += [
            f"  - Case {case_id}: {reminder.label} ({reason})"
            for case_id, reminder, reason in plan.not_sent
        ]
        lines.append("")

    if plan.already_sent:
        lines.append(f"Already sent ({len(plan.already_sent)}):")
        lines += [
            f"  - Case {case_id}: {reminder.label}" for case_id, reminder in plan.already_sent
        ]
        lines.append("")

    if not plan.log_found:
        lines += [
            "There is no 'Reminders Sent' tab yet, so nothing counts as already sent.",
            "",
        ]
    if plan.notes:
        lines.append("About the sheet:")
        lines += [f"  - {note}" for note in plan.notes]
        lines.append("")

    counts = summary_counts(plan)
    lines.append(
        f"{counts.looked_at} open case(s) looked at: {counts.to_send} reminder(s) would be sent, "
        f"{counts.problems} need attention, {counts.already_sent} already sent, "
        f"{counts.not_sent} held back, {counts.finished} in Finished (skipped), "
        f"{counts.nothing_due} with nothing due."
    )
    return "\n".join(lines)


def summary_line(plan, seconds):
    c = summary_counts(plan)
    return (
        f"Reminders finished (preview) in {seconds:.1f}s: {c.looked_at} open case(s), "
        f"{c.to_send} would be sent, {c.problems} need attention, "
        f"{c.already_sent} already sent, {c.not_sent} held back, {c.finished} in Finished, "
        f"{c.nothing_due} with nothing due"
    )