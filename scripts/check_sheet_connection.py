"""
Live check: confirms the service account can authenticate and read
both tabs of the Google Sheet set in .env.

Not part of the pytest suite, since it needs real credentials and
network access. Run from the project root:
    python scripts/check_sheet_connection.py
"""

import os
from dotenv import load_dotenv
import gspread

load_dotenv()

SHEET_ID = os.getenv("GOOGLE_SHEETS_ID")
SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")


def main():
    if not SHEET_ID or not SERVICE_ACCOUNT_FILE:
        raise SystemExit(
            "Missing GOOGLE_SHEETS_ID or GOOGLE_SERVICE_ACCOUNT_FILE — check .env"
        )

    gc = gspread.service_account(filename=SERVICE_ACCOUNT_FILE)
    sh = gc.open_by_key(SHEET_ID)

    print(f"Opened spreadsheet: {sh.title}")
    print(f"Worksheets found: {[ws.title for ws in sh.worksheets()]}")

    for tab_name in ["Ongoing", "Finished"]:
        ws = sh.worksheet(tab_name)
        headers = ws.row_values(1)
        print(f"\n'{tab_name}' tab headers: {headers}")


if __name__ == "__main__":
    main()