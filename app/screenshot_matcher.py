"""
Match Dropbox screenshot submissions to cases in the Ongoing Google Sheet.

Flow:
1. Read each case from the Ongoing sheet.
2. Parse the case's scheduled date and locate the matching Dropbox date folder.
3. Match the Provider folder using the Provider value from Google Sheets.
4. Convert Patient Name to Dropbox's LastNameFirstName format and match the
   claimant folder.
5. Confirm that the claimant folder contains a screenshot/image.
6. Read the screenshot's Dropbox server_modified timestamp.
7. Convert the UTC timestamp to America/Phoenix for Date Completed.
8. Move the matched case from Ongoing to Finished using the shared
   move_case_to_finished() helper.

Matching rules:
- Provider and claimant names are compared after normalization.
- Matching ignores capitalization, spaces, hyphens, underscores, apostrophes,
  commas, and other punctuation.
- Matching is exact after normalization; fuzzy matching is not used.
- Missing matches are skipped rather than guessed.
- Ambiguous matches are skipped rather than choosing one automatically.
- The Dropbox year/root folder is configured through DROPBOX_YEAR_FOLDER.

The matcher defaults to dry-run mode. The script
scripts/check_screenshot_to_finished.py must be run with --live to make
changes to Google Sheets.
"""

import re
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import dropbox
import gspread
from dotenv import load_dotenv

from apis.dropbox_api import (
    get_dropbox_client, 
    list_folder_entries,
)
from apis.dropbox_metadata import get_file_upload_time
from app.sheet_case_mover import move_case_to_finished


load_dotenv()


SHEET_ID = os.getenv("GOOGLE_SHEETS_ID")

SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "config/service_account.json",
)

def get_year_folder():
    """Return the configured Dropbox year folder."""
    year_folder = os.getenv("DROPBOX_YEAR_FOLDER")

    if not year_folder:
        raise ValueError(
            "DROPBOX_YEAR_FOLDER is missing from .env"
        )

    return year_folder

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".heic",
    ".webp",
}

def normalize_name(value):
    """
    Converts a name into a lowercase alphanumeric comparison key.

    Examples:
        KhannaAnand   -> khannaanand
        KHANNA ANAND  -> khannaanand
        Khanna-Anand  -> khannaanand
        Khanna_Anand  -> khannaanand
        Khanna, Anand -> khannaanand
    """

    value = str(value).strip().lower()

    # Remove everything except letters and numbers
    value = re.sub(
        r"[^a-z0-9]",
        "",
        value,
    )

    return value


def patient_name_to_claimant_folder(patient_name):
    """
    Convert Google Sheets Patient Name to
    Dropbox LastNameFirstName format.

    Supports:
    John Smith -> SmithJohn
    John Michael Smith -> SmithJohn
    John Michael David Smith -> SmithJohn
    Khanna, Anand Michael -> KhannaAnand
    """
    if patient_name is None:
        return ""

    patient_name = str(patient_name).strip()

    if not patient_name:
        return ""

    # Format: LastName, FirstName MiddleNames
    if "," in patient_name:
        last_name, remainder = patient_name.split(",", 1)
        parts = remainder.strip().split()

        if not last_name.strip() or not parts:
            return ""

        first_name = parts[0]

    else:
        # Format: FirstName MiddleNames LastName
        parts = patient_name.split()

        if len(parts) < 2:
            return ""

        first_name = parts[0]
        last_name = parts[-1]

    return normalize_name(last_name + first_name)


# =========================================================
# DATE HELPERS
# =========================================================

def parse_schedule_datetime(value):
    """
    Converts the Google Sheet Date & Time value into
    a Python datetime.

    Handles examples such as:

        May 4th, 2026 08:00 AM MST
        May 4, 2026 08:00 AM
        May 4th, 2026
        05/04/2026 08:00 AM
        05/04/2026
    """

    value = str(value).strip()

    if not value:
        return None

    # Remove ordinal suffixes:
    #
    # 1st -> 1
    # 2nd -> 2
    # 3rd -> 3
    # 4th -> 4
    value = re.sub(
        r"(\d{1,2})(st|nd|rd|th)",
        r"\1",
        value,
        flags=re.IGNORECASE,
    )

    # Remove common timezone abbreviation at the end.
    #
    # Example:
    # May 4, 2026 08:00 AM MST
    #
    # becomes:
    # May 4, 2026 08:00 AM
    value = re.sub(
        r"\s+[A-Z]{2,5}$",
        "",
        value,
    ).strip()

    formats = [
        "%B %d, %Y %I:%M %p",
        "%b %d, %Y %I:%M %p",

        "%B %d, %Y",
        "%b %d, %Y",

        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %I:%M:%S %p",

        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %H:%M:%S",

        "%m/%d/%Y",

        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",

        "%Y-%m-%d",
    ]

    for date_format in formats:
        try:
            return datetime.strptime(
                value,
                date_format,
            )
        except ValueError:
            continue

    print(
        f"Could not parse Date & Time value: {value!r}"
    )

    return None


def find_child_folder(
    dbx,
    parent_path,
    possible_names,
):
    """
    Finds a direct child folder matching one of the
    possible folder names.

    Comparison ignores capitalization, spaces,
    hyphens and punctuation.
    """

    wanted_names = {
        normalize_name(name)
        for name in possible_names
    }

    entries = list_folder_entries(
        parent_path,
    )

    for entry in entries:

        if not isinstance(
            entry,
            dropbox.files.FolderMetadata,
        ):
            continue

        if normalize_name(entry.name) in wanted_names:
            return entry.path_display

    return None

def find_date_folder(
    dbx,
    scheduled_datetime,
):
    """
    Finds the Dropbox date folder using the confirmed
    folder structure:

        /2026/MM-YYYY/MM-DD-YYYY

    Example:

        May 4, 2026

        /2026/05-2026/05-04-2026
    """

    year = scheduled_datetime.year
    month = scheduled_datetime.month
    day = scheduled_datetime.day
    year_folder = get_year_folder()

    if year != 2026:
        print(
            f"SKIP: Schedule year is {year}, "
            f"but Dropbox root is {year_folder}."
        )
        return None

    month_name = (
        f"{month:02d}-{year}"
    )

    date_name = (
        f"{month:02d}-{day:02d}-{year}"
    )

    print(
        "Expected month folder:",
        month_name,
    )

    month_folder = find_child_folder(
        dbx,
        year_folder,
        [month_name],
    )

    if not month_folder:
        print(
            "Could not find month folder:",
            month_name,
        )
        return None

    print(
        "Found month folder:",
        month_folder,
    )

    date_folder = find_child_folder(
        dbx,
        month_folder,
        [date_name],
    )

    if not date_folder:
        print(
            "Could not find date folder:",
            date_name,
        )
        return None

    return date_folder

def find_images_in_claimant_folder(
    dbx,
    claimant_folder_path,
):
    """
    Finds screenshot/image files anywhere underneath
    the claimant's folder.

    This supports both:

        /SmithJohn/screenshot.png

    and:

        /SmithJohn/Screenshot/screenshot.png
    """

    entries = list_folder_entries(
        claimant_folder_path,
         recursive=True,
    )

    image_files = []

    for entry in entries:

        if not isinstance(
            entry,
            dropbox.files.FileMetadata,
        ):
            continue

        _, extension = os.path.splitext(
            entry.name
        )

        if extension.lower() not in IMAGE_EXTENSIONS:
            continue

        image_files.append(
            entry.path_display
        )

    return image_files

def find_claimant_submission(
    dbx,
    date_folder,
    expected_provider,
    expected_claimant,
):
    """
    Searches Dropbox in this order:

        Date
        -> Provider
        -> Claimant
        -> Screenshot

    Provider and claimant names are normalized before
    comparison so capitalization, spaces, hyphens,
    apostrophes, etc. do not affect matching.
    """

    expected_provider_key = normalize_name(
        expected_provider
    )

    expected_claimant_key = normalize_name(
        expected_claimant
    )

    matches = []

    # =====================================================
    # STEP 1: FIND PROVIDER FOLDER
    # =====================================================

    provider_entries = list_folder_entries(
        date_folder,
    )

    matching_provider = None

    for provider_entry in provider_entries:

        if not isinstance(
            provider_entry,
            dropbox.files.FolderMetadata,
        ):
            continue

        provider_key = normalize_name(
            provider_entry.name
        )

        if provider_key == expected_provider_key:
            matching_provider = provider_entry
            break

    if not matching_provider:
        print(
            "NO MATCH: Provider folder not found."
        )
        print(
            "Expected Provider:",
            expected_provider,
        )
        return []

    provider_name = (
        matching_provider.name
    )

    provider_path = (
        matching_provider.path_display
    )

    print(
        "Matched Provider folder:",
        provider_name,
    )

    # =====================================================
    # STEP 2: FIND CLAIMANT UNDER THAT PROVIDER
    # =====================================================

    claimant_entries = list_folder_entries(
        provider_path,
    )

    for claimant_entry in claimant_entries:

        if not isinstance(
            claimant_entry,
            dropbox.files.FolderMetadata,
        ):
            continue

        claimant_folder_name = (
            claimant_entry.name
        )

        claimant_key = normalize_name(
            claimant_folder_name
        )

        if claimant_key != expected_claimant_key:
            continue

        print(
            "Matched claimant folder:",
            claimant_folder_name,
        )

        # =================================================
        # STEP 3: CHECK FOR SCREENSHOT
        # =================================================

        screenshots = (
            find_images_in_claimant_folder(
                dbx,
                claimant_entry.path_display,
            )
        )

        if not screenshots:
            print(
                "Claimant matched, but no "
                "screenshot was found."
            )
            continue

        screenshots.sort()

        matches.append(
            {
                "provider": provider_name,
                "claimant": claimant_folder_name,
                "claimant_path": (
                    claimant_entry.path_display
                ),
                "screenshot_path": (
                    screenshots[0]
                ),
                "screenshot_count": (
                    len(screenshots)
                ),
            }
        )

    return matches    

def move_matches(dry_run=True):
    """
    Match completed Dropbox screenshot submissions to Ongoing cases.

    For each Ongoing case, locate the matching Dropbox date, provider,
    claimant, and screenshot. The screenshot upload timestamp is converted
    from UTC to America/Phoenix and used as Date Completed.

    When dry_run is True, matching results are displayed but Google Sheets
    are not modified. When dry_run is False, matched cases are moved from
    Ongoing to Finished using move_case_to_finished().

    Args:
        dry_run: If True, report matches without modifying Google Sheets.
            Defaults to True.
    """

    if not SHEET_ID:
        raise ValueError(
            "GOOGLE_SHEETS_ID is missing from .env"
        )

    # -----------------------------------------------------
    # Dropbox connection
    # -----------------------------------------------------

    dbx = get_dropbox_client()

    # -----------------------------------------------------
    # Google Sheets connection
    # -----------------------------------------------------

    gc = gspread.service_account(
        filename=SERVICE_ACCOUNT_FILE
    )

    spreadsheet = gc.open_by_key(
        SHEET_ID
    )

    ongoing = spreadsheet.worksheet(
        "Ongoing"
    )

    finished = spreadsheet.worksheet(
        "Finished"
    )

    rows = ongoing.get_all_records()

    matches_to_move = []

    print("\n================================")
    print("CHECKING ONGOING RECORDS")
    print("================================")

    for row in rows:

        case_id = str(
            row.get("Case ID", "")
        ).strip()

        patient_name = str(
            row.get("Patient Name", "")
        ).strip()

        date_time_value = str(
            row.get("Date & Time", "")
        ).strip()

        print("\n--------------------------------")

        print(
            f"Case ID: {case_id or '[blank]'}"
        )

        print(
            f"Patient: {patient_name or '[blank]'}"
        )

        print(
            f"Date & Time: "
            f"{date_time_value or '[blank]'}"
        )

        # -------------------------------------------------
        # Patient must exist
        # -------------------------------------------------

        if not patient_name:

            print(
                "SKIP: Patient Name is blank."
            )

            continue

        # =================================================
        # STEP 1
        # Parse Date & Time from Ongoing
        # =================================================

        scheduled_datetime = (
            parse_schedule_datetime(
                date_time_value
            )
        )

        if not scheduled_datetime:

            print(
                "SKIP: Could not understand "
                "Date & Time."
            )

            continue

        print(
            "Parsed schedule date:",
            scheduled_datetime.strftime(
                "%m/%d/%Y"
            ),
        )

        print(
            "Parsed schedule time:",
            scheduled_datetime.strftime(
                "%I:%M %p"
            ),
        )

        # =================================================
        # STEP 2
        # Find matching Dropbox date folder
        # =================================================

        try:
            date_folder = find_date_folder(
                dbx,
                scheduled_datetime,
            )

        except dropbox.exceptions.ApiError as error:

            print(
                "SKIP: Dropbox error while "
                "looking for date folder."
            )

            print(error)

            continue

        if not date_folder:

            print(
                "SKIP: Matching Dropbox "
                "date folder was not found."
            )

            continue

        print(
            "Dropbox date folder:",
            date_folder,
        )

        # =================================================
        # STEP 3
        # Patient -> LastNameFirstName
        # =================================================

        expected_claimant = (
            patient_name_to_claimant_folder(
                patient_name
            )
        )

        if not expected_claimant:

            print(
                "SKIP: Could not create "
                "LastNameFirstName."
            )

            continue

        print(
            "Expected claimant folder:",
            expected_claimant,
        )

        # =================================================
        # STEP 4
        # Get Provider from Google Sheets
        # =================================================

        sheet_provider = str(
            row.get("Provider", "")
        ).strip()

        if not sheet_provider:
            print(
                "SKIP: Provider is blank "
                "in Google Sheets."
            )
            continue

        print(
            "Expected Provider folder:",
            sheet_provider,
        )

        # =================================================
        # STEP 5
        # Find claimant and screenshot
        # =================================================

        submissions = (
            find_claimant_submission(
                dbx,
                date_folder,
                sheet_provider,
                expected_claimant,
            )
        )

        if not submissions:

            print(
                "NO MATCH: No claimant folder "
                "with a screenshot was found "
                "for this date."
            )

            continue

        # -------------------------------------------------
        # Safety:
        # Same claimant under multiple providers
        # -------------------------------------------------

        if len(submissions) > 1:

            print(
                "SKIP: More than one matching "
                "claimant submission was found."
            )

            for submission in submissions:

                print(
                    "Provider:",
                    submission["provider"],
                )

                print(
                    "Claimant:",
                    submission["claimant"],
                )

                print(
                    "Screenshot:",
                    submission[
                        "screenshot_path"
                    ],
                )

            continue

        submission = submissions[0]

        # =================================================
        # STEP 5
        # Provider comes from Dropbox hierarchy
        # =================================================

        dropbox_provider = submission[
            "provider"
        ]

        print(
            "Dropbox Provider:",
            dropbox_provider,
        )

        print(
            "Dropbox claimant:",
            submission["claimant"],
        )

        print(
            "Screenshot:",
            submission[
                "screenshot_path"
            ],
        )

        if (
            submission["screenshot_count"]
            > 1
        ):

            print(
                "NOTE:",
                submission[
                    "screenshot_count"
                ],
                "image files found."
            )

            print(
                "Using:",
                submission[
                    "screenshot_path"
                ],
            )

        # =================================================
        # STEP 6
        # Get screenshot upload timestamp from Dropbox
        # =================================================

        try:
            upload_time_utc = (
                get_file_upload_time(
                    submission[
                        "screenshot_path"
                    ]
                )
            )

        except Exception as error:

            print(
                "SKIP: Could not retrieve "
                "screenshot metadata."
            )

            print(error)

            continue

        # Dropbox metadata helper returns UTC.
        # Convert to Arizona time for display/storage.
        upload_time_arizona = (
            upload_time_utc.astimezone(
                ZoneInfo(
                    "America/Phoenix"
                )
            )
        )

        print(
            "Dropbox upload date/time:",
            upload_time_arizona.strftime(
                "%m/%d/%Y %I:%M %p"
            ),
        )

        # -------------------------------------------------
        # Show Provider difference if there is one.
        #
        # Dropbox provider remains authoritative here.
        # -------------------------------------------------

        print(
            "*** MATCH FOUND ***"
        )

        matches_to_move.append(
            {
                "row": row,
                "provider": (
                    dropbox_provider
                ),
                "screenshot_path": (
                    submission[
                        "screenshot_path"
                    ]
                ),
                "upload_time": (
                    upload_time_arizona
                ),
            }
        )

    # =====================================================
    # NO MATCHES
    # =====================================================

    if not matches_to_move:

        print("\n================================")
        print("NO RECORDS TO MOVE")
        print("================================")

        return

    # =====================================================
    # MATCH SUMMARY
    # =====================================================

    print("\n================================")
    print("MATCH SUMMARY")
    print("================================")

    for match in matches_to_move:

        row = match["row"]

        print(
            "Case ID:",
            row.get(
                "Case ID",
                "",
            ),
        )

        print(
            "Patient:",
            row.get(
                "Patient Name",
                "",
            ),
        )

        print(
            "Provider:",
            match["provider"],
        )

        print(
            "Date Completed:",
            match[
                "upload_time"
            ].strftime(
                "%m/%d/%Y %I:%M %p"
            ),
        )

        print(
            "Screenshot:",
            match["screenshot_path"],
        )

        print("--------------------------------")

    # =====================================================
    # DRY RUN SAFETY
    # =====================================================

    if dry_run:

        print("\n================================")
        print("DRY RUN COMPLETE")
        print("================================")

        print(
            f"{len(matches_to_move)} "
            "record(s) WOULD be moved."
        )

        print(
            "No Google Sheet rows were changed."
        )

        print("\nTo perform the actual move, run ")
        print("python -m scripts.check_screenshot_to_finished --live")

        return

    # =====================================================
    # ACTUALLY MOVE RECORDS
    # =====================================================

    print("\n================================")
    print("MOVING RECORDS")
    print("================================")

    for match in matches_to_move:

        row = match["row"]

        case_id = str(
            row.get("Case ID", "")
        ).strip()

        if not case_id:
            print(
                "SKIP: Case ID is blank for",
                row.get("Patient Name", ""),
            )
            continue

        date_completed = match[
            "upload_time"
        ].strftime(
            "%m/%d/%Y %I:%M %p"
        )

        move_case_to_finished(
            ongoing_ws=ongoing,
            finished_ws=finished,
            case_id=case_id,
            extra_fields={
                "Date Completed": date_completed,
            },
        )

        print(
            "MOVED:",
            case_id,
            "-",
            row.get(
                "Patient Name",
                "",
            ),
        )

        print(
            "Provider:",
            match["provider"],
        )

        print(
            "Date Completed:",
            date_completed,
        )

    # =====================================================
    # COMPLETE
    # =====================================================

    print("\n================================")
    print("DONE")
    print("================================")

    print(
        f"{len(matches_to_move)} "
        "record(s) moved from "
        "Ongoing to Finished."
    )