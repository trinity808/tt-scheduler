
import os
import sys
from datetime import datetime
from urllib.parse import quote

import msal
import requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from reminder_rules import decide_reminders, get_arizona_today


ONGOING_TAB = "Ongoing"
FINISHED_TAB = "Finished"

GOOGLE_SHEETS_SCOPE = [
    "https://www.googleapis.com/auth/spreadsheets.readonly"
]

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


def parse_exam_date(value):
    """Convert a Google Sheets date into a Python date."""
    formats = [
        "%m/%d/%Y",
        "%Y-%m-%d",
        "%m-%d-%Y",
        "%m/%d/%y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue

    raise ValueError(
        f"Could not understand Exam Date '{value}'. "
        "Use MM/DD/YYYY format."
    )


def get_sheets_service():
    """Connect to Google Sheets."""
    load_dotenv()

    spreadsheet_id = os.getenv("GOOGLE_SHEETS_ID")

    credentials_file = os.getenv(
        "GOOGLE_SERVICE_ACCOUNT_FILE",
        "config/service_account.json",
    )

    if not spreadsheet_id:
        raise RuntimeError(
            "GOOGLE_SHEETS_ID is missing from .env."
        )

    if not os.path.isabs(credentials_file):
        credentials_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            credentials_file,
        )

    if not os.path.isfile(credentials_file):
        raise FileNotFoundError(
            f"Google service account file not found: {credentials_file}"
        )

    credentials = Credentials.from_service_account_file(
        credentials_file,
        scopes=GOOGLE_SHEETS_SCOPE,
    )

    service = build(
        "sheets",
        "v4",
        credentials=credentials,
        cache_discovery=False,
    )

    return service, spreadsheet_id


def get_tab_rows(service, spreadsheet_id, tab_name):
    """Read a sheet tab and return its rows as dictionaries."""
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A:ZZ",
        )
        .execute()
    )

    values = result.get("values", [])

    if not values:
        raise RuntimeError(
            f"The {tab_name} tab is empty."
        )

    headers = [
        str(value).strip().lower()
        for value in values[0]
    ]

    rows = []

    for values_row in values[1:]:
        row = {}

        for index, header in enumerate(headers):
            row[header] = (
                str(values_row[index]).strip()
                if index < len(values_row)
                else ""
            )

        rows.append(row)

    return rows


def get_case_from_sheets(case_id):
    """Find the case in Ongoing and check Finished."""
    service, spreadsheet_id = get_sheets_service()

    ongoing_rows = get_tab_rows(
        service,
        spreadsheet_id,
        ONGOING_TAB,
    )

    finished_rows = get_tab_rows(
        service,
        spreadsheet_id,
        FINISHED_TAB,
    )

    # Do not process cases already in Finished.
    for row in finished_rows:
        if row.get("case id", "").strip() == case_id:
            raise RuntimeError(
                f"Case {case_id} is already in the Finished tab."
            )

    # Find the case in Ongoing.
    for row in ongoing_rows:
        if row.get("case id", "").strip() != case_id:
            continue

        exam_date_text = row.get("exam date", "").strip()

        if not exam_date_text:
            raise RuntimeError(
                f"Exam Date is empty for case {case_id}."
            )

        return {
            "case_id": case_id,
            "patient_name": row.get("patient name", ""),
            "exam_date": parse_exam_date(exam_date_text),
            "provider_email": row.get(
                "provider-email", ""
            ).strip(),
            "psychometrist_email": row.get(
                "psychometrist-email", ""
            ).strip(),
        }

    raise RuntimeError(
        f"Could not find case {case_id} in the {ONGOING_TAB} tab."
    )


def ask_yes_no(question):
    """Ask a yes/no question."""
    while True:
        answer = input(question).strip().lower()

        if answer in ("y", "yes"):
            return True

        if answer in ("n", "no"):
            return False

        print("Please enter Y or N.")


def get_recipient(case, reminder):
    """Choose the recipient based on the reminder type."""
    recipient_role = reminder.get("recipient_role")

    if recipient_role == "psychometrist":
        recipient = case["psychometrist_email"]

        if not recipient or "@" not in recipient:
            raise RuntimeError(
                "Psychometrist-Email is missing or invalid. "
                "Add the email address to the Ongoing sheet."
            )

        return recipient

    if recipient_role == "provider":
        recipient = case["provider_email"]

        if not recipient or "@" not in recipient:
            raise RuntimeError(
                "Provider-Email is missing or invalid."
            )

        return recipient

    raise RuntimeError(
        f"Unknown reminder recipient role: {recipient_role}"
    )


def get_staff_cc_emails():
    """Read staff CC addresses from .env."""
    load_dotenv()

    raw_emails = os.getenv(
        "STAFF_CC_EMAILS", ""
    ).strip()

    if not raw_emails:
        return []

    return [
        email.strip()
        for email in raw_emails.split(",")
        if email.strip()
    ]


def get_microsoft_access_token():
    """
    Authenticate TT Scheduler using application credentials.

    This uses the approved Microsoft Graph Application permission.
    No interactive Microsoft sign-in is required.
    """
    load_dotenv()

    tenant_id = os.getenv(
        "MICROSOFT_TENANT_ID", ""
    ).strip()

    client_id = os.getenv(
        "MICROSOFT_CLIENT_ID", ""
    ).strip()

    client_secret = os.getenv(
        "MICROSOFT_CLIENT_SECRET", ""
    ).strip()

    if not tenant_id:
        raise RuntimeError(
            "MICROSOFT_TENANT_ID is missing from .env."
        )

    if not client_id:
        raise RuntimeError(
            "MICROSOFT_CLIENT_ID is missing from .env."
        )

    if not client_secret:
        raise RuntimeError(
            "MICROSOFT_CLIENT_SECRET is missing from .env."
        )

    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        authority=(
            f"https://login.microsoftonline.com/{tenant_id}"
        ),
        client_credential=client_secret,
    )

    token_result = app.acquire_token_for_client(
        scopes=GRAPH_SCOPE
    )

    access_token = token_result.get("access_token")

    if not access_token:
        raise RuntimeError(
            "Microsoft authentication failed: "
            + token_result.get(
                "error_description",
                token_result.get("error", "Unknown error"),
            )
        )

    return access_token


def send_reminder_email(
    recipient,
    reminder,
    case_id,
    exam_date,
):
    """Send a reminder through Microsoft Graph."""
    load_dotenv()

    sender_email = os.getenv(
        "MICROSOFT_SENDER_EMAIL", ""
    ).strip()

    if not sender_email or "@" not in sender_email:
        raise RuntimeError(
            "MICROSOFT_SENDER_EMAIL is missing or invalid in .env."
        )

    staff_cc = []

    if reminder.get("cc_required"):
        staff_cc = get_staff_cc_emails()

        if not staff_cc:
            raise RuntimeError(
                "This overdue reminder requires staff CC. "
                "Add STAFF_CC_EMAILS to your .env."
            )

    access_token = get_microsoft_access_token()

    message_text = (
        f"Case ID: {case_id}\n"
        f"Exam Date: {exam_date}\n"
        f"Reminder: {reminder['key']}\n"
        f"Reminder Day: {reminder['day']}\n\n"
        f"{reminder['message']}"
    )

    message = {
        "message": {
            "subject": reminder["subject"],
            "body": {
                "contentType": "Text",
                "content": message_text,
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": recipient
                    }
                }
            ],
            "ccRecipients": [
                {
                    "emailAddress": {
                        "address": email
                    }
                }
                for email in staff_cc
            ],
        },
        "saveToSentItems": True,
    }

    encoded_sender = quote(sender_email, safe="")

    response = requests.post(
        (
            "https://graph.microsoft.com/v1.0/"
            f"users/{encoded_sender}/sendMail"
        ),
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=message,
        timeout=30,
    )

    if response.status_code == 202:
        print("\nEmail accepted by Microsoft Graph.")
        print(f"From: {sender_email}")
        print(f"To: {recipient}")

        if staff_cc:
            print(f"CC: {', '.join(staff_cc)}")

    else:
        print(
            f"\nEmail failed: HTTP {response.status_code}"
        )
        print(response.text)
        response.raise_for_status()


def main():
    print("TT Scheduler - Reminder Test")
    print("=============================")

    # Ask for the case ID.
    case_id = input("\nEnter the Case ID: ").strip()

    if not case_id:
        raise RuntimeError("Case ID cannot be empty.")

    # Retrieve the case from Google Sheets.
    case = get_case_from_sheets(case_id)

    # Get the actual Arizona date.
    today = get_arizona_today()

    day_number = (today - case["exam_date"]).days

    print("\n========== CASE FOUND ==========")
    print(f"Case ID:             {case['case_id']}")
    print(f"Patient:             {case['patient_name']}")
    print(f"Exam Date:           {case['exam_date']}")
    print(f"Arizona Today:       {today}")
    print(f"Day Number:          {day_number}")
    print(
        f"Provider Email:      "
        f"{case['provider_email'] or 'Missing'}"
    )
    print(
        f"Psychometrist Email: "
        f"{case['psychometrist_email'] or 'Missing'}"
    )

    # Temporary manual status inputs.
    # Dropbox integration will replace these later.
    report_submitted = ask_yes_no(
        "\nHas the report been submitted? (Y/N): "
    )

    materials_uploaded = ask_yes_no(
        "Have the testing materials been uploaded? (Y/N): "
    )

    reminders = decide_reminders(
        exam_date=case["exam_date"],
        today=today,
        submitted_at=None,
        materials_uploaded=materials_uploaded,
        report_submitted=report_submitted,
    )

    if not reminders:
        print(
            "\nNo reminders are eligible for this case today."
        )
        return

    print("\n========== ELIGIBLE REMINDERS ==========")

    for index, reminder in enumerate(reminders, start=1):
        recipient = get_recipient(case, reminder)

        print(f"\n{index}. {reminder['key']}")
        print(f"Subject: {reminder['subject']}")
        print(f"Recipient role: {reminder['recipient_role']}")
        print(f"To: {recipient}")
        print(f"Message: {reminder['message']}")

        if reminder.get("cc_required"):
            staff_cc = get_staff_cc_emails()

            if not staff_cc:
                raise RuntimeError(
                    "This overdue reminder requires staff CC. "
                    "Add STAFF_CC_EMAILS to your .env."
                )

            print(f"CC: {', '.join(staff_cc)}")

    # Send the first eligible reminder only.
    reminder = reminders[0]
    recipient = get_recipient(case, reminder)

    print("\n========== EMAIL CONFIRMATION ==========")
    print(f"From: {os.getenv('MICROSOFT_SENDER_EMAIL', '').strip()}")
    print(f"To: {recipient}")
    print(f"Subject: {reminder['subject']}")

    confirmation = input(
        "\nType SEND to send this email: "
    ).strip()

    if confirmation != "SEND":
        print("Cancelled. No email was sent.")
        return

    send_reminder_email(
        recipient=recipient,
        reminder=reminder,
        case_id=case["case_id"],
        exam_date=case["exam_date"],
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)
