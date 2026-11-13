
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ARIZONA = ZoneInfo("America/Phoenix")


def get_day_number(exam_date: date, today: date) -> int:
    """Day 0 is the exam date."""
    return (today - exam_date).days


def get_arizona_today() -> date:
    return datetime.now(ARIZONA).date()


def get_day8_date(exam_date: date) -> date:
    """Move the Day 8 reminder to Friday if it falls on a weekend."""
    scheduled_date = exam_date + timedelta(days=8)

    if scheduled_date.weekday() == 5:
        scheduled_date -= timedelta(days=1)
    elif scheduled_date.weekday() == 6:
        scheduled_date -= timedelta(days=2)

    return scheduled_date


def get_arizona_date(timestamp: datetime) -> date:
    if timestamp.tzinfo is None:
        raise ValueError("Submission timestamp must be timezone-aware.")

    return timestamp.astimezone(ARIZONA).date()


def decide_reminders(
    exam_date: date,
    today: date,
    submitted_at: datetime | None = None,
    materials_uploaded: bool | None = None,
    report_submitted: bool | None = None,
) -> list[dict]:
    """
    Return reminders eligible for this case today.

    recipient_role:
        psychometrist = testing materials reminders
        provider = report reminders
    """

    day_number = get_day_number(exam_date, today)

    if day_number < 0:
        return []

    reminders = []

    # Day 1 and Day 3: testing materials reminders.
    if materials_uploaded is False:

        if day_number == 1:
            reminders.append({
                "key": "DAY_1_MATERIALS",
                "day": 1,
                "recipient_role": "psychometrist",
                "subject": "Reminder: Testing Materials Upload",
                "message": (
                    "Please upload the behavioral observations "
                    "and testing materials for this case."
                ),
            })

        elif day_number == 3:
            reminders.append({
                "key": "DAY_3_MATERIALS_OVERDUE",
                "day": 3,
                "recipient_role": "psychometrist",
                "subject": "Overdue: Testing Materials",
                "message": (
                    "The behavioral observations and testing materials "
                    "are still outstanding. Please upload them as soon "
                    "as possible."
                ),
            })

    # A Dropbox submission timestamp confirms report submission.
    if submitted_at is not None:
        report_submitted = True

    # Do not send report reminders if submission status is unknown
    # or the report has already been submitted.
    if report_submitted is not False:
        return reminders

    if day_number == 5:
        reminders.append({
            "key": "DAY_5_PROGRESS",
            "day": 5,
            "recipient_role": "provider",
            "subject": "DDS Report Progress Check-In",
            "message": (
                "I am checking in on the progress of the DDS report. "
                "Please provide an update on its current status."
            ),
        })

    elif today == get_day8_date(exam_date):
        reminders.append({
            "key": "DAY_8_DUE_SOON",
            "day": 8,
            "recipient_role": "provider",
            "subject": "DDS Report Due in Two Days",
            "message": (
                "This is a reminder that the DDS report is due "
                "in two days. Please ensure it is completed on time."
            ),
        })

    elif day_number == 10:
        reminders.append({
            "key": "DAY_10_DUE_TODAY",
            "day": 10,
            "recipient_role": "provider",
            "subject": "DDS Report Due Today",
            "message": (
                "The DDS report is due today. Please submit the "
                "completed report by the end of the day."
            ),
        })

    elif day_number >= 11:
        reminders.append({
            "key": "DAY_11_OVERDUE",
            "day": 11,
            "recipient_role": "provider",
            "subject": "Overdue: DDS Report",
            "message": (
                "The DDS report deadline has passed and the report "
                "is still outstanding. Please provide an update "
                "and submit it as soon as possible."
            ),
            "cc_required": True,
        })

    return reminders
