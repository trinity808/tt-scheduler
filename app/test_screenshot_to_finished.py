import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import dropbox
import gspread
from dotenv import load_dotenv

from apis.dropbox_api import get_dropbox_client
from apis.dropbox_metadata import get_file_upload_time


load_dotenv()


# =========================================================
# CONFIG
# =========================================================

SHEET_ID = os.getenv("GOOGLE_SHEETS_ID")

SERVICE_ACCOUNT_FILE = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE",
    "config/service_account.json",
)

DROPBOX_YEAR_FOLDER = "/2026"

# IMPORTANT:
# True  = test only; nothing moves
# False = actually moves rows Ongoing -> Finished
DRY_RUN = False


IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".heic",
    ".webp",
}


# =========================================================
# GENERAL HELPERS
# =========================================================

def normalize(value):
    """
    Removes spaces/punctuation and converts to lowercase.

    Examples:
        SmithJohn -> smithjohn
        Smith-John -> smithjohn
        Smith John -> smithjohn
    """

    return re.sub(
        r"[^a-zA-Z0-9]",
        "",
        str(value),
    ).lower()


def patient_name_to_claimant_folder(patient_name):
    """
    Converts the Patient Name from Google Sheets into
    Dropbox's LastNameFirstName format.

    Examples:
        John Smith -> SmithJohn
        John A. Smith -> SmithJohn
        Smith, John -> SmithJohn
    """

    patient_name = str(patient_name).strip()

    if not patient_name:
        return ""

    # Example:
    # Smith, John
    if "," in patient_name:
        last_name, remainder = patient_name.split(",", 1)

        remainder_parts = remainder.strip().split()

        if not remainder_parts:
            return ""

        first_name = remainder_parts[0]

        return f"{last_name.strip()}{first_name}"

    # Example:
    # John Smith
    # John A. Smith
    parts = patient_name.split()

    if len(parts) < 2:
        return ""

    first_name = parts[0]
    last_name = parts[-1]

    return f"{last_name}{first_name}"


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


# =========================================================
# DROPBOX HELPERS
# =========================================================

def list_folder_entries(dbx, folder_path):
    """
    Lists everything directly inside one Dropbox folder.
    Handles Dropbox pagination.
    """

    result = dbx.files_list_folder(
        folder_path
    )

    entries = list(result.entries)

    while result.has_more:
        result = dbx.files_list_folder_continue(
            result.cursor
        )

        entries.extend(
            result.entries
        )

    return entries


def list_folder_recursive(dbx, folder_path):
    """
    Recursively lists everything inside a Dropbox folder.
    """

    result = dbx.files_list_folder(
        folder_path,
        recursive=True,
    )

    entries = list(result.entries)

    while result.has_more:
        result = dbx.files_list_folder_continue(
            result.cursor
        )

        entries.extend(
            result.entries
        )

    return entries


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
        normalize(name)
        for name in possible_names
    }

    entries = list_folder_entries(
        dbx,
        parent_path,
    )

    for entry in entries:

        if not isinstance(
            entry,
            dropbox.files.FolderMetadata,
        ):
            continue

        if normalize(entry.name) in wanted_names:
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

    if year != 2026:
        print(
            f"SKIP: Schedule year is {year}, "
            f"but Dropbox root is {DROPBOX_YEAR_FOLDER}."
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
        DROPBOX_YEAR_FOLDER,
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

    entries = list_folder_recursive(
        dbx,
        claimant_folder_path,
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
    expected_claimant,
):
    """
    Searches ONLY inside the correct date folder.

    Expected structure:

        /DateFolder
            /ProviderName
                /LastNameFirstName
                    /Screenshot

    ProviderName is taken directly from the Dropbox
    folder hierarchy.

    Returns all matches so the program can avoid
    guessing if the same claimant appears twice.
    """

    expected_claimant_key = normalize(
        expected_claimant
    )

    matches = []

    # -----------------------------------------------------
    # Provider folders
    # -----------------------------------------------------

    provider_entries = list_folder_entries(
        dbx,
        date_folder,
    )

    for provider_entry in provider_entries:

        if not isinstance(
            provider_entry,
            dropbox.files.FolderMetadata,
        ):
            continue

        provider_name = provider_entry.name
        provider_path = provider_entry.path_display

        # -------------------------------------------------
        # Claimant folders
        # -------------------------------------------------

        claimant_entries = list_folder_entries(
            dbx,
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

            if (
                normalize(claimant_folder_name)
                != expected_claimant_key
            ):
                continue

            # ---------------------------------------------
            # Screenshot check
            # ---------------------------------------------

            screenshots = (
                find_images_in_claimant_folder(
                    dbx,
                    claimant_entry.path_display,
                )
            )

            if not screenshots:
                continue

            # Sort to make selection consistent.
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


# =========================================================
# MAIN WORKFLOW
# =========================================================

def move_matches():
    """
    Workflow:

    1. Read Ongoing rows.
    2. Read Date & Time.
    3. Find matching Dropbox date folder.
    4. Convert Patient Name -> LastNameFirstName.
    5. Search that date for claimant.
    6. Verify screenshot exists.
    7. Get Provider from Dropbox hierarchy.
    8. Get screenshot upload time from Dropbox metadata.
    9. Move Ongoing -> Finished.
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

    # Row 1 contains headers.
    # Actual data starts at row 2.
    for sheet_row_number, row in enumerate(
        rows,
        start=2,
    ):

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
        # Find claimant and screenshot
        # =================================================

        submissions = (
            find_claimant_submission(
                dbx,
                date_folder,
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

        sheet_provider = str(
            row.get("Provider", "")
        ).strip()

        if (
            sheet_provider
            and normalize(sheet_provider)
            != normalize(dropbox_provider)
        ):

            print(
                "NOTE: Provider in Google Sheet "
                "differs from Dropbox."
            )

            print(
                "Google Sheet Provider:",
                sheet_provider,
            )

            print(
                "Dropbox Provider:",
                dropbox_provider,
            )

            print(
                "Dropbox Provider will be used "
                "in Finished."
            )

        print(
            "*** MATCH FOUND ***"
        )

        matches_to_move.append(
            {
                "row_number": (
                    sheet_row_number
                ),
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

    if DRY_RUN:

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

        print(
            "\nIf everything above is correct, "
            "change:"
        )

        print(
            "DRY_RUN = True"
        )

        print(
            "to:"
        )

        print(
            "DRY_RUN = False"
        )

        return

    # =====================================================
    # ACTUALLY MOVE RECORDS
    # =====================================================

    # Delete from bottom to top.
    # This prevents row numbers from changing
    # while records are being removed.
    matches_to_move.sort(
        key=lambda item: item[
            "row_number"
        ],
        reverse=True,
    )

    print("\n================================")
    print("MOVING RECORDS")
    print("================================")

    for match in matches_to_move:

        row = match["row"]

        # Finished columns:
        #
        # Case ID
        # Patient Name
        # Phone
        # Psychometrist
        # Provider
        # Price
        # Payment Status
        # Date Completed

        finished_row = [
            row.get(
                "Case ID",
                "",
            ),

            row.get(
                "Patient Name",
                "",
            ),

            row.get(
                "Phone",
                "",
            ),

            row.get(
                "Psychometrist",
                "",
            ),

            # Provider from Dropbox
            match["provider"],

            row.get(
                "Price",
                "",
            ),

            # Payment Status
            # Leave blank for now.
            "",

            # Date Completed
            # Dropbox screenshot upload time
            match[
                "upload_time"
            ].strftime(
                "%m/%d/%Y %I:%M %p"
            ),
        ]

        # -------------------------------------------------
        # SAFETY:
        #
        # Add to Finished FIRST.
        # Only delete Ongoing if append succeeds.
        # -------------------------------------------------

        finished.append_row(
            finished_row,
            value_input_option="USER_ENTERED",
        )

        ongoing.delete_rows(
            match["row_number"]
        )

        print(
            "MOVED:",
            row.get(
                "Case ID",
                "",
            ),
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
            match[
                "upload_time"
            ].strftime(
                "%m/%d/%Y %I:%M %p"
            ),
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


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    move_matches()