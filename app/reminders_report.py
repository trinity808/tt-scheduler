"""
Turns a RunPlan (see reminders.py) into plain text for the console and the log.

Kept apart from the planning so the wording can be tested without capturing
console output: format_report() takes a RunPlan and returns a string.

Order: a banner saying what kind of run it was, the reminders that would go out (or,
after a live run, those that were sent, failed, or weren't attempted), anything
needing attention, reminders held back, reminders already sent, notes about the
sheet, then a summary. Email addresses never appear, only names.

Three kinds of run, each with its own banner: a preview (nothing sent, nothing
written), a live run (sent to the providers and logged), and a live test (everything
sent to the test address, nothing logged).

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
    sent: int = 0
    failed: int = 0
    not_attempted: int = 0


def summary_counts(plan, result=None):
    return SummaryCounts(
        looked_at=plan.looked_at,
        to_send=len(plan.to_send),
        problems=len(plan.problems),
        already_sent=len(plan.already_sent),
        not_sent=len(plan.not_sent),
        finished=plan.finished,
        nothing_due=plan.nothing_due,
        sent=len(result.sent) if result else 0,
        failed=len(result.failed) if result else 0,
        not_attempted=len(result.not_attempted) if result else 0,
    )


def _who(planned):
    name = planned.case.provider_name
    return f"to {name}" if name else "to the provider"


BANNERS = {
    "preview": "PREVIEW: nothing was sent, and nothing was written to the sheet.",
    "live": "LIVE: the reminders below were sent to the providers and written to the Reminders Sent tab.",
    "live test": (
        "LIVE TEST: every email went to the test address, marked [TEST]. "
        "Nothing was written to the Reminders Sent tab."
    ),
}


def _reminder_lines(planned, show_text, copied=True):
    cc = ", director copied" if planned.reminder.cc_director and copied else ""
    lines = [
        f"  Case {planned.case.case_id}: {planned.reminder.label} {_who(planned)}{cc} "
        f"(exam {_long_date(planned.exam_date)})",
        f"    Subject: {planned.subject}",
    ]
    if show_text:
        lines += [f"    | {line}" if line else "    |" for line in planned.body.split("\n")]
    return lines


def _delivery_lines(result, show_text, mode):
    """What a live run did, in place of the preview's "Would send"."""
    lines = []
    if result.sent:
        lines.append(f"Sent ({len(result.sent)}):")
        for planned in result.sent:
            lines += _reminder_lines(planned, show_text, copied=mode != "live test")
        lines.append("")
    else:
        lines += ["Sent: nothing today.", ""]

    if result.failed:
        lines.append(f"Failed to send ({len(result.failed)}), to be tried again on the next run:")
        lines += [
            f"  - Case {planned.case.case_id}: {planned.reminder.label} {_who(planned)} ({reason})"
            for planned, reason in result.failed
        ]
        lines.append("")

    if result.log_problem:
        lines += [f"STOPPED: {result.log_problem}", ""]
    if result.not_attempted:
        lines.append(f"Not attempted ({len(result.not_attempted)}):")
        lines += [
            f"  - Case {planned.case.case_id}: {planned.reminder.label} {_who(planned)}"
            for planned in result.not_attempted
        ]
        lines.append("")
    return lines


def format_report(plan, show_text=False, today_given=False, result=None, mode="preview"):
    """
    The report. `result` is a SendResult after a live run, and `mode` is "preview",
    "live" or "live test" (see BANNERS). A preview takes neither.
    """
    lines = [
        f"Reminders for {_long_date(plan.today)} (Arizona time)"
        + (", the date given with --today" if today_given else ""),
        BANNERS[mode],
        "",
    ]

    if result is not None:
        lines += _delivery_lines(result, show_text, mode)
    elif plan.to_send:
        lines.append(f"Would send ({len(plan.to_send)}):")
        for planned in plan.to_send:
            lines += _reminder_lines(planned, show_text)
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

    counts = summary_counts(plan, result)
    if result is None:
        outcome = f"{counts.to_send} reminder(s) would be sent"
    else:
        outcome = _outcome(counts)
    lines.append(
        f"{counts.looked_at} open case(s) looked at: {outcome}, "
        f"{counts.problems} need attention, {counts.already_sent} already sent, "
        f"{counts.not_sent} held back, {counts.finished} in Finished (skipped), "
        f"{counts.nothing_due} with nothing due."
    )
    return "\n".join(lines)


def _outcome(counts):
    """"3 reminder(s) sent, 1 failed" after a live run."""
    text = f"{counts.sent} reminder(s) sent"
    if counts.failed:
        text += f", {counts.failed} failed"
    if counts.not_attempted:
        text += f", {counts.not_attempted} not attempted"
    return text


def summary_line(plan, seconds, result=None, mode="preview"):
    c = summary_counts(plan, result)
    outcome = f"{c.to_send} would be sent" if result is None else _outcome(c).replace("reminder(s) ", "")
    return (
        f"Reminders finished ({mode}) in {seconds:.1f}s: {c.looked_at} open case(s), "
        f"{outcome}, {c.problems} need attention, "
        f"{c.already_sent} already sent, {c.not_sent} held back, {c.finished} in Finished, "
        f"{c.nothing_due} with nothing due"
    )