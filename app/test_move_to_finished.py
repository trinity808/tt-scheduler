import os

import gspread
from dotenv import load_dotenv


load_dotenv()

SHEET_ID = os.getenv("GOOGLE_SHEETS_ID")
SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "config/service_account.json"
)


def move_case_to_finished(case_id):
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
    spreadsheet = gc.open_by_key(SHEET_ID)

    ongoing = spreadsheet.worksheet("Ongoing")
    finished = spreadsheet.worksheet("Finished")

    rows = ongoing.get_all_records()

    for index, row in enumerate(rows, start=2):
        if str(row.get("Case ID")).strip() == str(case_id).strip():

            finished_row = [
                row.get("Case ID", ""),
                row.get("Patient Name", ""),
                row.get("Phone", ""),
                row.get("Psychometrist", ""),
                row.get("Provider", ""),
                row.get("Price", ""),
                "",  # Payment Status
                "",  # Date Completed
            ]

            # Add to Finished first
            finished.append_row(finished_row)

            # Only delete from Ongoing after successful append
            ongoing.delete_rows(index)

            print(f"Moved Case ID {case_id} from Ongoing to Finished.")
            return

    print(f"Case ID {case_id} was not found in Ongoing.")


if __name__ == "__main__":
    case_id = input("Enter TEST Case ID to move: ").strip()
    move_case_to_finished(case_id)