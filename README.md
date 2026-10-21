# Trinity Dropbox Scheduler

Automated Python scheduler for Trinity Tree PDF appointment packets. The app reads incoming PDFs from Dropbox, extracts appointment and claimant details, generates local report DOCX files from Word templates, updates the correct daily schedule DOCX, uploads the schedule back to Dropbox, and moves processed PDFs out of the incoming folder.

The project also has a newer second part: tracking each case in a Google Sheet. It reads the notes that Trinity Tree's office assistant (OA) writes in the sheet's Comments column, keeps the provider, psychometrist, and test information up to date, and flags anything it can't understand directly on the sheet. The sheet runs alongside the DOCX pipeline and does not replace it. The two are not connected yet: `main.py` does not write to the sheet. See [Tracking Sheet](#tracking-sheet).

## What It Does

### DOCX pipeline (`main.py`)

- Connects to Dropbox using long-lived refresh-token authentication.
- Finds PDF files in a configured Dropbox input folder.
- Downloads each PDF into a local working folder.
- Extracts key scheduling data from the first pages of each PDF:
  - case ID
  - authorization number
  - patient name
  - date of birth and age
  - claimant phone number
  - assigned doctor (from Trinity Tree's letterhead, the same on every packet, not a per-case provider)
  - examiner initials (the SSA's own claims examiner, not Trinity Tree's psychometrist)
  - service date, time, code, description, and amount
  - allegations
- Selects the adult or child report template based on the configured age cutoff.
- Fills Word template placeholders and writes generated reports to a local folder.
- Creates or updates a same-day schedule file such as `Schedule_May_4_2026.docx`.
- Keeps the schedule sorted by appointment time.
- Uploads the updated schedule DOCX to Dropbox.
- Moves processed PDFs into the configured Dropbox processed folder.
- Writes rotating logs and can send email alerts on failures.
- Uses a simple lock file so overlapping runs do not process the same files at once.

### Google Sheet tracking

- Adds each extracted case as a row on the sheet's `Ongoing` tab. A repeated PDF updates that row in place and flags it as a duplicate.
- Reads OA's notes in the Comments column (`Provider:`, `Psychometrist:`, `Add:`, `Change:`) and fills in the Provider and Psychometrist columns.
- Resolves short test names such as `wms` or `wisc` into full service codes through a `Services` tab. Unknown or ambiguous names are flagged. Test changes are recognized and reported, but not yet written to the sheet.
- Flags problems directly on the sheet, with a note and, for cases, a colored cell, and prints a plain-language report after each run.
- Moves a finished case from the `Ongoing` tab to the `Finished` tab.

## Project Structure

```text
.
├── apis/
│   ├── dropbox_api.py            # Dropbox auth and file operations
│   └── dropbox_metadata.py       # When Dropbox received a file (server time, UTC)
├── app/
│   ├── apply_comment_updates.py  # The sweep: applies OA's comments to the Ongoing tab (run_sweep)
│   ├── comment_parser.py         # Parses the Comments column format
│   ├── config_loader.py          # Loads config/settings.json
│   ├── file_utils.py             # Unused, kept from the original project
│   ├── health_monitor.py         # Logging, startup checks, email alerts
│   ├── ongoing_row_mapper.py     # Maps an extracted PDF to an Ongoing row, adds or updates it
│   ├── pdf_extractor.py          # PDF parsing and data extraction
│   ├── schedule_generator.py     # Daily schedule DOCX creation/update
│   ├── service_format.py         # The shared pattern for a service code
│   ├── service_lookup.py         # Turns typed test names into service codes
│   ├── services_tab.py           # Loads the Services tab and flags problems on it
│   ├── sheet_case_mover.py       # Moves a case from Ongoing to Finished
│   ├── sheet_client.py           # Google Sheets connection and cell helpers
│   ├── state_manager.py          # Unused, kept from the original project
│   ├── sweep_report.py           # Plain-language console report for a sweep
│   └── template_filler.py        # Report template placeholder replacement
├── config/
│   └── settings.json             # Runtime folders and scheduler settings
├── docs/
│   └── trinity_scheduler_process_guide.pdf
├── scripts/
│   └── check_sheet_connection.py # Live check against your own sandbox sheet
├── templates/                    # Local only, not in the repository (see below)
├── testing pdfs/                 # Local only, not in the repository (see below)
├── tests/                        # pytest suite
├── .env.example                  # Environment variable template
├── get_refresh_token.py          # Dropbox refresh-token setup helper
├── main.py                       # DOCX pipeline entry point
├── requirements.txt              # Python dependencies
├── requirements-dev.txt          # requirements.txt plus pytest
├── run_scheduler.sh              # macOS/local shell runner
└── scheduler local enabler steps.docx   # macOS scheduling guide
```

Runtime folders such as `data/`, `logs/`, `reports/`, `.venv/`, `__pycache__/`, and `.env` stay out of GitHub.

`templates/` and `testing pdfs/` are deliberately not in the repository either, since they may contain private claimant data. Ask a teammate for copies and place them at the project root. `main.py` needs the templates to run, and `tests/test_local_pipeline.py` skips itself when either folder is missing.

## Requirements

- Python 3.10 or newer (developed and tested on 3.11)
- Windows or macOS. Scheduling is covered for both below. The macOS runner (`run_scheduler.sh`) has not been tested by the current team.
- Dropbox app credentials and a refresh token with access to the folders in use
- For the Google Sheet tracking only: a Google Cloud service account with a JSON key file, and a Google Sheet shared with it
- Microsoft Word, LibreOffice, or another DOCX-compatible editor for viewing generated documents
- The `templates/` folder (adult and child report templates), which is not in the repository. Ask a teammate for a copy. Or get it from the legacy repo.

Python packages are listed in `requirements.txt`. `requirements-dev.txt` adds `pytest` for running the tests.

While developing, use your own Dropbox app and your own copy of the sheet. Do not point a development setup at Trinity Tree's production Dropbox or sheet.

## Setup

Run everything from the project root. Clone the repository, then create and activate a virtual environment.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If PowerShell refuses to run the activation script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.

**Windows (Git Bash):**

```bash
python -m venv .venv
source .venv/Scripts/activate
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

`.venv/` is already ignored by git. The macOS runner `run_scheduler.sh` activates a folder named `venv`, so if you use that script, either create `venv` instead or edit the script.

Install the dependencies. Developers should use the second line, which also installs `pytest`:

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

Create your local environment file from the template, then fill it in:

```bash
cp .env.example .env          # Git Bash or macOS
copy .env.example .env        # PowerShell
```

`.env.example` lists every variable with comments. In short:

| Variable | What it is | Needed for |
|---|---|---|
| `DROPBOX_APP_KEY`, `DROPBOX_APP_SECRET`, `DROPBOX_REFRESH_TOKEN` | Credentials from your Dropbox app (see Dropbox App Setup) | Anything that touches Dropbox |
| `DROPBOX_INPUT_FOLDER`, `DROPBOX_PROCESSED_PDF_FOLDER`, `DROPBOX_SCHEDULE_FOLDER` | The Dropbox folders the DOCX pipeline reads from and writes to | `main.py` |
| `GOOGLE_SHEETS_ID` | The ID in your sheet's URL, between `/d/` and `/edit` | The tracking sheet |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to the service account key, `config/service_account.json` by default | The tracking sheet |
| `SMTP_*`, `ALERT_TO_EMAIL` | Optional failure emails | Only when `email_notifications_enabled` is `true` in `config/settings.json` |

The SMTP settings use a plain username and password, which works with providers such as Gmail (with an app password). It does not work with Microsoft 365 mailboxes, which no longer accept that login.

### Dropbox folder paths

The three folder variables are personal to your Dropbox app, which is why they live in `.env` and not in `config/settings.json`. What they look like depends on the app's access type:

- **App folder access:** short paths, relative to your app's folder. For example `/incoming_pdfs`.
- **Full Dropbox access:** full paths from the root of the account. For example `/Apps/<your app name>/incoming_pdfs`.

If one is missing, the startup check stops the run with a message naming it.

### Google Sheets setup

Only needed for the tracking sheet.

1. Create a Google Cloud project and enable the **Google Sheets API** and the **Google Drive API**.
2. Create a service account in that project and download a JSON key. Save it as `config/service_account.json`. It is already in `.gitignore`. Never commit it.
3. Create a spreadsheet with tabs named exactly `Ongoing`, `Finished`, and `Services`, with the header rows described under [Tracking Sheet](#tracking-sheet).
4. Share the spreadsheet with the service account's email address (it ends in `iam.gserviceaccount.com`), as Editor. A service account has no access to anything, even in its own project, until the sheet is shared with it.
5. Copy the sheet's ID from its URL into `GOOGLE_SHEETS_ID` in `.env`.
6. Check the connection:

```bash
python scripts/check_sheet_connection.py
```

It prints the tabs it found and each tab's headers.

## Dropbox App Setup

1. Create a Dropbox app in the [Dropbox App Console](https://www.dropbox.com/developers/apps/create) with **Scoped access**.
2. Choose the access type:
   - **App folder** is the simplest for a personal development setup. Everything lives under `Apps/<your app name>/` in your Dropbox.
   - **Full Dropbox** is needed to see folders outside the app folder, such as a folder shared in from another account. The access type cannot be changed after the app is created. To switch, delete the app and create a new one.
3. On the Permissions tab, enable `files.metadata.read`, `files.content.write`, and `files.content.read`, then submit.
4. Copy the app key and app secret from the Settings tab into `.env`.
5. Generate a refresh token:

```bash
python get_refresh_token.py
```

6. Open the authorization URL the script prints and approve access in Dropbox.
7. Paste the authorization code back into the terminal.
8. Copy the printed `DROPBOX_REFRESH_TOKEN=...` value into `.env`.
9. In Dropbox, create the three folders the pipeline uses (for example `incoming_pdfs`, `processed_pdfs`, and `schedules`), inside `Apps/<your app name>/` for an app-folder app. Set the three folder variables in `.env` to match, following the path rules above.

## Configuration

Settings are split in two, by one rule: anything that is the same for everyone lives in `config/settings.json`, which is committed. Anything that differs between machines or people lives in `.env`, which is not. That's why Dropbox folder paths, credentials, and the sheet ID are in `.env` (see [Setup](#setup)).

`config/settings.json` holds the shared runtime settings:

```json
{
  "base_folder": "data",
  "adult_age_cutoff": 18,
  "max_files_per_run": 200,
  "adult_template_path": "templates/adult_report_template.docx",
  "child_template_path": "templates/child_report_template.docx",
  "generated_docs_folder": "reports",
  "health_check": {
    "log_folder": "logs",
    "email_notifications_enabled": false,
    "email_subject_prefix": "[Trinity Scheduler]"
  }
}
```

Settings:

- `base_folder`: local working folder for downloaded PDFs, local schedules, and processed PDFs.
- `adult_age_cutoff`: age used to choose the adult or child report template.
- `max_files_per_run`: maximum number of PDFs to process in one scheduler run.
- `adult_template_path`: path to the adult DOCX template.
- `child_template_path`: path to the child DOCX template.
- `generated_docs_folder`: local folder for filled report documents.
- `health_check.log_folder`: folder for scheduler logs.
- `health_check.email_notifications_enabled`: turns failure emails on or off. Off by default.
- `health_check.email_subject_prefix`: prefix added to failure email subjects.

Relative paths resolve from the project root, so run everything from there.

## Template Placeholders

The report templates are DOCX files in `templates/`, which is not in the repository (see [Project Structure](#project-structure)). The scheduler replaces these placeholders wherever they appear in paragraphs, tables, headers, or footers:

```text
{{FULL_NAME}}
{{FIRST_NAME}}
{{First_name}}
{{First_Name}}
{{first_name}}
{{LAST_NAME}}
{{CASE_NUMBER}}
{{DOB}}
{{DATE_OF_EXAMINATION}}
{{REFERRED_BY}}
{{ALLEGATIONS}}
{{EXAMINER}}
{{DOCTOR_FULL_NAME}}
```

`{{EXAMINER}}` and `{{DOCTOR_FULL_NAME}}` are intentionally rendered in red by the current template filler.

## Running Manually

With the virtual environment active, from the project root:

```bash
python main.py
```

This runs the DOCX pipeline only. It does not touch the Google Sheet.

On startup, the scheduler checks that the required settings and the three Dropbox folder variables in `.env` are present, that the template files exist, and that the generated documents folder can be created. If anything is missing, the run stops with a message naming it, before any PDF is processed.

A `scheduler.lock` file stops two runs from overlapping. It is removed when a run ends. If a run was killed hard and left the file behind, the next run exits saying another run is active. Delete the file once you're sure nothing is running.

## Running With the Shell Script (macOS)

`run_scheduler.sh` is a runner for local scheduled runs on macOS. The current team has not tested it. Anyone who works on a Mac is welcome to correct this section and the one below.

```bash
chmod +x run_scheduler.sh
./run_scheduler.sh
```

Before using it on another machine, edit the `cd` line so it points to that machine's project folder:

```bash
cd "/path/to/trinity-dropbox-scheduler"
```

The script activates a virtual environment folder named `venv` (not `.venv`, which the Setup section uses), runs `main.py`, appends normal output to `logs/scheduler.log`, and appends errors to `logs/scheduler_error.log`. Create `venv`, or edit the script to match.

## Scheduling on macOS

This section has not been tested by the current team. The repository includes a Word guide, `scheduler local enabler steps.docx`, for this setup. A typical launchd setup looks like this:

1. Make `run_scheduler.sh` executable.
2. Create a launch agent plist in `~/Library/LaunchAgents/`.
3. Point the plist program argument to the full path of `run_scheduler.sh`.
4. Configure the interval or calendar schedule.
5. Load the plist with `launchctl`.
6. Check `logs/launchd.log`, `logs/launchd_error.log`, `logs/scheduler.log`, and `logs/scheduler_error.log`.

Example launchd commands:

```bash
launchctl load ~/Library/LaunchAgents/com.trinity.scheduler.plist
launchctl unload ~/Library/LaunchAgents/com.trinity.scheduler.plist
launchctl list | grep trinity
```