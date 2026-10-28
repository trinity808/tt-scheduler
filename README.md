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
- Finds the screenshot a provider has submitted for each open case in Dropbox, and moves the case to the `Finished` tab with the screenshot's time as its completion date. This is a separate command and previews by default.
- Saves each case's original list of tests as hidden data on its row when the case is added.

## Project Structure

```text
.
├── apis/
│   ├── dropbox_api.py            # Dropbox auth and file operations
│   └── dropbox_metadata.py       # When Dropbox received a file (server time, UTC)
├── app/
│   ├── apply_comment_updates.py  # The sweep: applies OA's comments to the Ongoing tab (run_sweep)
│   ├── case_metadata.py          # Saves each case's original test list as hidden data on its row (the rules)
│   ├── comment_parser.py         # Parses the Comments column format
│   ├── config_loader.py          # Loads config/settings.json
│   ├── file_utils.py             # Unused, kept from the original project
│   ├── health_monitor.py         # Logging, startup checks, email alerts
│   ├── ongoing_row_mapper.py     # Maps an extracted PDF to an Ongoing row, adds or updates it
│   ├── pdf_extractor.py          # PDF parsing and data extraction
│   ├── schedule_generator.py     # Daily schedule DOCX creation/update
│   ├── screenshot_matcher.py     # Finds a provider's submitted screenshot in Dropbox and moves the case to Finished
│   ├── service_format.py         # The shared pattern for a service code
│   ├── service_lookup.py         # Turns typed test names into service codes
│   ├── services_tab.py           # Loads the Services tab and flags problems on it
│   ├── sheet_case_mover.py       # Moves a case from Ongoing to Finished
│   ├── sheet_client.py           # Google Sheets connection and cell helpers
│   ├── sheets_metadata_client.py # The Google Sheets calls behind case_metadata.py
│   ├── state_manager.py          # Unused, kept from the original project
│   ├── sweep_report.py           # Plain-language console report for a sweep
│   └── template_filler.py        # Report template placeholder replacement
├── config/
│   └── settings.json             # Runtime folders and scheduler settings
├── docs/
│   └── trinity_scheduler_process_guide.pdf
├── scripts/
│   ├── check_case_metadata.py    # Live check of the stored original lists, on a scratch tab
│   ├── check_screenshot_to_finished.py # Runs the screenshot matcher (preview by default)
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
| `DROPBOX_YEAR_FOLDER` | The Dropbox folder that holds the year's month folders, such as `/2026`, written the way the folder paths below are | The screenshot matcher (see [Screenshot Matcher](#screenshot-matcher)) |
| `SMTP_*`, `ALERT_TO_EMAIL` | Optional failure emails | Only when `email_notifications_enabled` is `true` in `config/settings.json` |

The SMTP settings use a plain username and password, which works with providers such as Gmail (with an app password). It does not work with Microsoft 365 mailboxes, which no longer accept that login.

### Dropbox folder paths

The three folder variables are personal to your Dropbox app, which is why they live in `.env` and not in `config/settings.json`. What they look like depends on the app's access type:

- **App folder access:** short paths, relative to your app's folder. For example `/incoming_pdfs`.
- **Full Dropbox access:** full paths from the root of the account. For example `/Apps/<your app name>/incoming_pdfs`.

If one is missing, the startup check stops the run with a message naming it.

`DROPBOX_YEAR_FOLDER` follows the same path rules. It is used only by the screenshot matcher, and it is checked when the matcher looks for a date folder, so the startup check doesn't mention it.

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
   - **Full Dropbox** is needed to see folders outside the app folder, such as a folder shared in from another account. The access type cannot be changed after the app is created. To switch, delete the app and create a new one. The screenshot matcher reads folders that normally sit outside an app folder, so it usually needs this.
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

Settings are split in two, by one rule: anything that is the same for everyone lives in `config/settings.json`, which is committed. Anything that differs between machines or people lives in `.env`, which is not. That's why Dropbox folder paths (including the screenshot matcher's year folder), credentials, and the sheet ID are in `.env`(see [Setup](#setup)).

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

## Scheduling on Windows

Windows Task Scheduler can run the DOCX pipeline (`main.py`) on a repeating schedule. This schedules the pipeline only. The sheet tracking is not scheduled yet.

1. Open Task Scheduler and choose **Create Task**. Use this, not the Basic Task wizard, which hides settings you need.
2. **General:** give it a name. "Run only when user is logged on" is the option this has been tested with.
3. **Triggers:** New, then begin the task **On a schedule**, Daily. Tick **Repeat task every** and pick an interval, for a duration of **Indefinitely**. Testing used 5 minutes. The right interval for real use is still to be decided.
4. **Actions:** New, **Start a program**, and fill in the three fields separately:
   - **Program/script:** the full path to the virtual environment's Python, for example `C:\Codes\tt-scheduler\.venv\Scripts\python.exe`
   - **Add arguments:** `main.py`
   - **Start in:** the project root, for example `C:\Codes\tt-scheduler`
5. **Conditions:** on a laptop, untick **Start the task only if the computer is on AC power**, otherwise the task quietly doesn't run on battery. This has not been tested.
6. Save, then right-click the task and choose **Run** to try it now, without waiting for the trigger.

Nothing appears on screen when it runs, even when it works. Check `logs/scheduler.log` for a fresh entry, and `logs/scheduler_error.log` for errors. For the exit code of the last run, open the task's **History** tab. If it's empty, click **Enable All Tasks History** in the right-hand pane first.

Mistakes made while setting this up, and what each looks like:

- **The program and its arguments in one field.** The task fails to start at all. They belong in two separate fields.
- **The wrong environment folder name,** such as `venv` where it's `.venv`. The task fails to start. Open the action and check the path.
- **A missing "Start in" folder.** The program starts from a system folder, and every relative path (settings, templates, logs) breaks.
- **Dropbox folder paths in `.env` that don't match the app's access type.** The run starts but fails at its first Dropbox call with a `not_found` error in `logs/scheduler_error.log`. See [Dropbox folder paths](#dropbox-folder-paths).

Not tested: "Run whether user is logged on or not", running while the computer is asleep, and the wake-the-computer option.

## Processing Flow

1. `main.py` loads `config/settings.json`.
2. Logging is configured by `app/health_monitor.py`.
3. A `scheduler.lock` file is created to prevent overlapping runs.
4. Dropbox PDFs are listed from the folder in `DROPBOX_INPUT_FOLDER`.
5. Each PDF is downloaded into `data/incoming_pdfs/`.
6. `app/pdf_extractor.py` extracts scheduling data.
7. `app/template_filler.py` fills the adult or child report template.
8. The existing same-day schedule DOCX is downloaded from Dropbox if present.
9. `app/schedule_generator.py` creates or updates the local schedule DOCX.
10. The updated schedule is uploaded to the folder in `DROPBOX_SCHEDULE_FOLDER`.
11. The original PDF is moved to the folder in `DROPBOX_PROCESSED_PDF_FOLDER`.
12. Successes and failures are written to logs.
13. The lock file is removed when the run exits.

This flow does not write to the Google Sheet. See [Tracking Sheet](#tracking-sheet) for how cases get onto it.

## Output Files

Generated report documents are saved to `generated_docs_folder` from `config/settings.json`, which is `reports/` by default.

Schedule files are created locally under `data/schedules/` and uploaded to the Dropbox schedule folder. Processed PDFs are moved in Dropbox to the processed folder.

## Logs and Alerts

Logs are written to:

```text
logs/scheduler.log
logs/scheduler_error.log
```

On macOS, the launchd setup also writes `logs/launchd.log` and `logs/launchd_error.log`.

The Python logger writes the first two itself, in rotating files with a 2 MB limit and 5 backups. Each line looks like this:

```text
2026-09-22 17:25:42,003 | INFO | trinity_scheduler | Run complete.
```

Only logger output reaches these files. Some low-level messages in `apis/dropbox_api.py`, such as the upload and move confirmations, are plain `print` calls. They appear on the console but not in the log, and the run's own log lines already record the same steps. The macOS shell script also appends the console output to the same files, which has not been tested.

When email alerts are enabled in `config/settings.json`, a failed PDF or a fatal run failure sends an email using the SMTP values in `.env`. They are off by default. The SMTP settings use a plain username and password, so a Gmail account needs an app password. Microsoft 365 mailboxes no longer accept that kind of login and would need a different approach.

## Tracking Sheet

> **Status:** the Google Sheet tracking is in development. It runs alongside the DOCX pipeline, nothing in it is scheduled yet, and the layout below is a working draft that will change as the pieces come together. It will be revisited after it has run in production.
<!-- Revisit this whole section once the sheet is proven in production and OA signs off. -->

The sheet has three tabs. Tab names must match exactly, and every tab has its headers in row 1.

### Tabs and columns

**Ongoing**: one row per open case.

```text
Case ID | Patient Name | Phone | DOB | Adult | Child | Date & Time | Services Requested | Allegations | Psychometrist | Provider | Price | Comments
```

**Finished**: cases that have been completed.

```text
Case ID | Patient Name | Phone | Psychometrist | Provider | Price | Payment Status | Date Completed
```

**Services**: the list of tests, used to turn short names into full codes.

```text
Code | Aliases | Name | Price
```

The code finds columns by their header name, not their position, so columns can be reordered or added without changing anything. Renaming a header the code uses breaks that feature. These are the ones it depends on:

| Tab | Headers the code needs |
|---|---|
| Ongoing | `Case ID` everywhere. `Provider`, `Psychometrist`, and `Comments` for the sweep. The other headers are filled from the PDF if present and left blank if not. |
| Finished | `Case ID`. Any header that also exists in Ongoing is copied across. `Payment Status` and `Date Completed` are filled only by whatever moves the case, and are blank otherwise. |
| Services | `Code` and `Aliases`. `Name` and `Price` are for people and are not read. |

These columns are a draft and may change. If you rename or remove one, search the repository for the header's name to find what uses it, for example `git grep -n "Payment Status"`.

Who fills what on the Ongoing tab: everything up to `Allegations`, plus `Price`, comes from the PDF. `Psychometrist` and `Provider` are filled by the sweep from the Comments column. `Comments` is written by OA. `Price` is the total printed on the PDF. Final billed amounts are set separately on the billing request forms and are not tracked here.

### Adding a case

```bash
python -m app.ongoing_row_mapper
```

It asks for the path to a PDF on your computer, shows the row it would write, and asks for confirmation. It takes a local file path, not a Dropbox path. Nothing connects Dropbox to the sheet yet, so cases are added this way.

- **A new case** goes into the first row after the last row that has a Case ID. Template text that OA has pre-filled in empty rows below the data does not push it down, and is kept. A blank row in the middle of the data is not filled.
- **A repeated Case ID** updates that row in place instead of adding a second one. Everything taken from the PDF is refreshed, including `Price`, so a hand edit to those columns is overwritten. `Psychometrist`, `Provider`, and `Comments` are left alone. The row turns yellow with a note, so the repeat is not silent.

The case's original tests, the codes taken from the PDF, are also saved on its row as hidden data (see [Stored original tests](#stored-original-tests)). A repeated PDF saves them again, so they always match the refreshed Services Requested cell. If saving fails, the row has still been written, the mapper says so, and running it again is safe. It updates the same row and tries again.

### Comments format
 
This part is written so it can be shared with OA on its own.
 
OA writes notes about a case in its **Comments** cell. Put each item on its own line (Ctrl+Enter, or Cmd+Enter on a Mac, starts a new line inside a cell), or separate items with a semicolon. Capitalization doesn't matter.
 
```text
Provider: Dr. Doe
Psychometrist: J. Smith
Add: WMS, VABS
Change: CTONI to WAIS
```
 
- `Provider:` and `Psychometrist:` fill the Provider and Psychometrist columns.
- `Add:` lists tests to add, separated by commas.
- `Change:` replaces one test with another. Name both, for example `Change: WISC to WIAT`. Several can be listed, separated by commas.
- A line left blank is ignored, so a copy of the empty template can be filled in with only what applies:
```text
Provider:
Psychometrist:
Add:
Change:
```
 
Tests are written by short name, such as `wms`, `WISC-V`, or `Psychiatric Exam`. Spaces, capitalization, and punctuation don't matter. A name that isn't in the Services tab, or that matches two tests, is flagged so it can be corrected.
 
`Price:` and `+Service:`, from an earlier draft of this format, are no longer recognized.
 
**What happens to the tests**
 
`Add:` and `Change:` lines change the case's **Services Requested** cell the next time the sweep runs:
 
- An added test goes at the end of the list, and its line ends with `(added)`.
- A swapped test takes the place of the one it replaces, and its line ends with `(changed from <the old test's code>)`.
- Lines from the original PDF stay exactly as they were.
Changes are applied in the order they are written, so a swap has to name a test the case has at that point. `Add: wiat` followed by `Change: wiat to wppsi` works. The other way round does not.
 
**Edit the comment, don't rewrite it.** Keep the earlier lines and add new ones at the bottom. Every time it runs, the system starts again from the PDF's original list and applies every line in the comment, so earlier lines are never applied twice and are never flagged.
 
If a line is deleted from the comment, the test it added is **kept**, not removed, because tests are only ever added or swapped. The Comments cell turns orange to say so. To keep the test, put the line back in the comment. To remove it, delete its line from the Services Requested cell.
 
If a change can't be applied, for example adding a test the case already has, or swapping a test the case doesn't have, that line is skipped and flagged, and the rest of the comment still applies.
 
Please don't type into the Services Requested cell. A line typed there is kept but flagged, and a line the system can't read stops all updates to that case's tests until it is fixed or removed.
 
Price is never changed.
 
If a line can't be used, the Comments cell turns orange and gets a note saying what was wrong and what was done with that line. See [Flags on the sheet](#flags-on-the-sheet). Fix the comment, and the orange clears itself the next time the sweep runs.

### Running the sweep
 
```bash
python -m app.apply_comment_updates            # preview: shows what would change, writes nothing
python -m app.apply_comment_updates --live     # applies the changes
```
 
Like every command that changes the sheet, it previews by default. Run the preview first, read the report, and then run it again with `--live`. The `--live` report says the same things in the past tense.
 
Add `--pause` to wait for Enter before the window closes, for a shortcut that is double-clicked. Never use `--pause` in a scheduled task, which would wait for a key nobody presses.
 
One run does this, in order:
 
1. Loads the Services tab, and flags any problems on it (not in a preview).
2. Reads every case's saved original tests, in one call.
3. Goes through the cases. For each one with a comment, or with applied changes still showing in its cell:
   1. Fills in `Provider` and `Psychometrist` where the comment names someone different from what is there. An unchanged value is never rewritten, and the comment is the source of truth for these two columns, so a different name typed straight into the column is replaced the next time the sweep runs.
   2. Applies the comment's test changes to the `Services Requested` cell (see [How test changes are applied](#how-test-changes-are-applied)).
   3. Flags the Comments cell if anything needs attention, and clears the flag when nothing does.
4. Prints a plain-language report.
**Which cases it looks at**
 
- A row with no Case ID is skipped entirely, comment and all, so a template kept in a spare row is left alone.
- A case's tests are looked at only when its comment has test changes, or its Services Requested cell already shows applied changes (a line ending in `(added)` or `(changed from ...)`). The second case catches a comment that was cleared while its changes are still in the cell. Otherwise the cell is left alone, even if someone edited it.
- A case with an empty comment and a plain cell is skipped.
Running it again and again is safe. A value that wouldn't change isn't rewritten, and a rerun on an unchanged comment writes nothing.
 
**When something is missing**
 
- If the Services tab is missing or unusable, `Provider` and `Psychometrist` still apply, and `Add:` and `Change:` lines are flagged "services list not loaded". Test changes are not applied.
- If the Ongoing tab has no `Services Requested` column, test changes can't be applied, and the report says so once.
- If a case's saved original tests belong to a different case (for example after a row was cut and pasted), that case's tests are left alone and flagged. Its other updates still apply.
- If a case's original tests need saving first and the save fails, its cell is left alone and flagged. The original is always saved before the cell is written, so a changed cell never exists without it.
**The report**
 
```text
Services tab needs attention:
  - Row 5: 'Beery VMI' isn't a valid service code (five digits, e.g. 96101-WISC-V), so it was skipped
  (noted on the Services tab)
 
Case 15419897:
  Provider: set to Dr. Doe
  Tests updated:
    change 96130-AUTISM to 96101-WAIS-IV
    add 96101-WMS-IV
  Original tests saved from the sheet: 96101-WISC-V, 90791, 96130-AUTISM
 
Case 15419898:
  NEEDS ATTENTION:
    Add: 96101-WISC-V  (skipped: 96101-WISC-V is already on this case)
 
2 comment(s) processed, 1 field(s) updated, 1 flagged for attention.
1 case(s) had their tests updated.
1 original list(s) saved.
Flagged cases: 15419898
```
 
- It describes what **this run** changed, compared with the cell as it was, not what the cell says now. A case that was already current says `Tests: already up to date, no change`.
- `Original tests saved from the sheet` means nothing was stored for that case, so its clean cell was taken as the original (see [Stored original tests](#stored-original-tests)).
- A preview opens with `DRY RUN: nothing was written to the sheet.`, uses "would be" throughout, skips the note on the Services tab and says so, and ends with the command that applies it.
The report is printed on the console only. Nothing is written to a log file, so the report of a scheduled run would be lost. This is listed under Known gaps.

### How test changes are applied
 
For people maintaining the code. The rules are in three modules that work on plain data and are tested without Google Sheets, and the sweep wires them to the sheet:
 
| Module | What it does |
|---|---|
| `app/services_cell.py` | Reads and writes the Services Requested cell's text: the numbered lines, the separator rows, and the markers |
| `app/service_changes.py` | Applies a comment's changes to a case's original tests, in order, and works out each line's marker |
| `app/services_update.py` | Decides what the cell should become, and whether it is safe to change it at all |
| `app/apply_comment_updates.py` | Reads the sheet, calls the above for each case, and writes the results |
 
**The cell is output only.** A case's tests are always worked out as the **stored original tests plus every change in the comment, in order**. The cell is never the starting point. That is why reruns are safe, why an appended comment never re-flags earlier changes, and why a mistake can't compound. The cell is read for only three things: the wording of each line, any line nothing explains, and the safety checks below.
 
**Each change is checked before it is applied,** against the list as built so far:
 
- `Add: X` needs X not to be on the case already.
- `Change: X to Y` needs X to be on the case, and Y not to be.
A change that doesn't fit is skipped with a reason, and the rest still apply.
 
**Markers** describe the net result against the PDF's list, however many changes it took:
 
| Situation | Marker |
|---|---|
| A test from the PDF | none, wherever it ends up |
| A test not on the PDF that took the place of one that is, through one swap or several | `(changed from <that PDF test>)` |
| Any other test not on the PDF | `(added)` |
| A swap back to the original test | none, and it returns to its place |
 
A swapped test keeps its position. An added test goes at the end.
 
**Descriptions** come from the cell's own line for that code if it has one, so the PDF's wording is never replaced. Failing that, from the Services tab's `Name`, and failing that the line shows only the code.
 
**The sweep refuses to change a cell,** writing nothing and flagging the case, when:
 
- the cell has a line it can't read, such as a note typed under a test, because writing over it would lose what someone typed
- nothing is stored for the case and the cell already shows applied changes, because adopting it as the original would make those changes permanent
- nothing is stored, the cell is empty, and the comment has changes, because there is nothing to adopt
**Adopting a cell.** When nothing is stored for a case and its cell is clean (every line readable, no markers), the cell is the PDF's list, so it is saved as the original. The save happens before the cell is written.
 
**Lines nothing explains.** A line in the cell that neither the original nor the comment accounts for is **kept**, and the comment is flagged. This happens when OA rewrites a comment from scratch or types a line in by hand. A comment counts as accounting for a test if it mentions it anywhere, even to swap it away and back, so an explicit change back is respected. A kept swap stays in its place. If a kept change no longer fits the new comment, it is **dropped**, and the flag says so. Nothing leaves the cell without a message.
 
**Other details.**
 
- A PDF line deleted from the cell by hand comes back whenever the case is updated, because the cell is output.
- The cell is rewritten only when its content would change. A difference in spacing alone doesn't count.
- The functions `run_sweep` and `apply_comment_updates` write by default, since that is their job, and take `dry_run=True` to preview. The command line does the opposite and previews unless given `--live`.

### Moving a case to Finished

```bash
python -m app.sheet_case_mover
```

It asks for a Case ID, and optionally a Payment Status and a Date Completed. The case is written to the Finished tab, in the first row after the last case, and only then deleted from Ongoing. If the write fails, the case is still in Ongoing. Moving cases is manual for now.

The case's stored original tests are deleted with its Ongoing row, since the case no longer needs them.

### Stored original tests

When a case is added, the codes of the tests on its PDF are saved on its Ongoing row as hidden data, using Google Sheets "developer metadata". It doesn't show in any cell or column. It exists so that a case's current tests can be worked out from its original list plus OA's comment, instead of from the Services Requested cell, which gets overwritten.

- **What is stored:** the Case ID and the ordered list of test codes, for example `{"case_id":"15419897","tests":["96101-WISC-V","90791"]}`. Only codes, never descriptions, so each case takes about 80 characters. Sheets caps the hidden data on a tab at 30,000 characters, which is room for roughly 350 open cases.
- **It follows the row.** It stays with its row when rows are inserted or sorted, and is deleted with the row when the case moves to Finished.
- **It checks itself.** Each entry carries its Case ID. If an entry no longer matches the Case ID in its row, for example after a row was cut and pasted, it is not trusted. When a new case is added to a row that still has an entry from an old case, the old entry is replaced.
- **Who can read it:** any Google Cloud project with access to the spreadsheet, so a future change of service account doesn't strand the entries.
- **Cases added before this existed** have nothing stored. Running the mapper again with the case's PDF stores it, and flags the row as a repeat.

To see what is stored for each row:

```bash
python -c "from app.sheet_client import get_client; from app.case_metadata import read_all_case_tests; from app.sheets_metadata_client import SheetsMetadataClient; ws = get_client().worksheet('Ongoing'); s = read_all_case_tests(SheetsMetadataClient(ws)); [print(row, e.case_id, e.tests) for row, e in sorted(s.by_row.items())]"
```

The rules are in `app/case_metadata.py` and the Google Sheets calls are in `app/sheets_metadata_client.py`, so the rules can be tested without Google.

### The Services tab
 
Each row is one test. A row's `Code` is the code as the authorization PDF prints it, including the edition, for example `96101-WISC-V` (five digits, then letters, digits, or hyphens). A row answers to:
 
- its full code, `96101-WISC-V`
- the part after the five digits, `WISC-V`, which is worked out automatically
- anything in the `Aliases` column, separated by commas, such as `WISC`
The `Name` column holds the text the PDF prints after the code, for example `Wechsler Intelligence Scale for Children - Ages 6 to 16 years`. It is the description on any line the sweep adds or restores, so it should be the full text, copied from a PDF. Line breaks and runs of spaces in it are collapsed to single spaces. A blank `Name` means the line shows only the code. Typing a `Name` into a comment doesn't work, since it is only for reading. The `Price` column isn't read.
 
Editing the tab needs no code change. The sweep reads it fresh each run. The tab is edited by people, so a problem in one row doesn't stop the rest:
 
- A row whose `Code` isn't a valid service code is skipped and reported. A test with no billing code can't be listed yet.
- A name that points to more than one row is reported and not accepted until it's fixed, for example an alias that matches another test's abbreviation.
- A missing tab, an empty tab, or a missing `Code` or `Aliases` header is reported, and the sweep carries on without test names.
Problems appear in the report, and as a note on cell A1 of the Services tab. The note clears itself once the table is fixed. A note someone else has put on A1 is never overwritten, and the report says so instead. In a preview, the note is not written.

### Flags on the sheet
 
| What you see | What it means | Does it clear itself? |
|---|---|---|
| An orange Comments cell, with a note | A line in the comment couldn't be used, or something about the case's tests needs attention. Each line of the note says what and why. | Yes, once the comment is fixed and the sweep runs again |
| A yellow row, with a note on the Case ID cell saying "Duplicate entry received" | The same case arrived again. Details from the PDF were refreshed, and Provider, Psychometrist, and Comments were left alone. | No. Clear it by hand, or it goes when the case moves to Finished. |
| A note on the top-left cell of the Services tab | A problem with the Services table. | Yes, once the table is fixed and the sweep runs again |
 
**What the lines in a Comments note mean**
 
| The line says | Meaning, and what to do |
|---|---|
| `... (skipped: unknown test 'wisk', did you mean WISC?)` | A test name isn't in the Services tab. Fix the spelling, or add the test to the tab. |
| `Add: X  (skipped: X is already on this case)` | The case already has that test. Delete the line. |
| `Change: X to Y  (skipped: X isn't on this case)` | The test to swap isn't on the case at that point. Fix the line, or its order. |
| `... (kept: the cell has it from an earlier change, but this comment no longer includes it. ...)` | A change was applied before, and its line is gone from the comment. The test stays. Put the line back to keep it as a change, or delete the test's line from the cell to remove it. |
| `... (kept: the cell has this line, but it isn't in the PDF's list or this comment. ...)` | Someone typed a line into the cell. It stays. Add it to the comment to keep it as a change, or delete it from the cell. |
| `... (dropped: ...)` | An earlier change conflicts with the new comment, so it was removed from the cell. The reason is in the line. |
| `Services Requested  (not changed: ...)` | The case's tests were left alone. The reason says why: a line in the cell can't be read, the original tests weren't saved and the cell is already changed, the case has no tests recorded, the saved original belongs to a different case, or saving the original failed. |
 
A comment that was cleared completely, while its changes still show in the cell, also gets an orange Comments cell, with `kept` lines, although the cell is empty. Put the lines back, or delete the tests from the cell, and it clears.
 
The sheet's own flags only ever set a background color and a note. They never change text color, so OA's blue and red markings for attended and no-show cases are left alone.
 
To clear a duplicate flag: select the row, then Fill color, then Reset, then right-click the Case ID cell and choose Delete note. Don't use "Clear formatting", which also removes the text colors.
 
If a case needs removing, delete its row. Clearing its cells leaves the fill and notes behind, and a stale hidden entry too.
 
A flagged comment that is deleted entirely, when nothing has been applied to the case, keeps its orange fill and note. That is a known gap.

## Screenshot Matcher

Finds the screenshot a provider has submitted for each open case in Dropbox, and moves the case from `Ongoing` to `Finished`. It is a separate command. It is not part of `main.py` or the sweep, and nothing runs it automatically yet.

### What it does

For each case on the Ongoing tab, it:

1. Reads the date in the `Date & Time` column and finds that day's folder in Dropbox.
2. Finds the provider's folder inside it, using the `Provider` column.
3. Finds the claimant's folder inside that, named from the `Patient Name` column in the form `LastNameFirstName`. "Anand Khanna" becomes `KhannaAnand`.
4. Checks that the claimant's folder, or any folder inside it, contains an image.
5. Takes the screenshot's last-modified time on Dropbox, converts it from UTC to Arizona time, and uses it as `Date Completed`.
6. Moves the case to Finished, the same way `python -m app.sheet_case_mover` does.

A case can only match when its `Provider` column is filled in. The sweep fills it from OA's comment, so the sweep needs to have run first.

### Folder structure it expects

```text
<DROPBOX_YEAR_FOLDER>/
  MM-YYYY/                      for example 09-2026
    MM-DD-YYYY/                 for example 09-21-2026
      <Provider>/               for example Dr. Doe
        <LastNameFirstName>/    for example KhannaAnand
          <screenshot>          an image anywhere inside this folder, subfolders included
                                (.png, .jpg, .jpeg, .heic or .webp)
```

Months and days are zero-padded. Names are compared after ignoring capitalization, spaces, hyphens, underscores, apostrophes, commas, and other punctuation, so `Khanna, Anand` and `KhannaAnand` are the same name, and `Dr. Doe` matches `dr doe`. Beyond that the match is exact, never fuzzy.

### Running it

```bash
python -m scripts.check_screenshot_to_finished           # preview, writes nothing
python -m scripts.check_screenshot_to_finished --live    # moves the cases
```

The preview prints each case it looked at, `MATCH FOUND` or `SKIP` with the reason, and a summary of what would move. Run it first and check the matches, since a wrong match moves a real case. Like every command that changes the sheet, it only writes with `--live`.

It needs `DROPBOX_YEAR_FOLDER` in `.env`, and the `tzdata` package, which is in `requirements.txt`, since Windows has no timezone database of its own.

### What it skips

It skips a case and says why, instead of guessing, when:

- the date folder, the provider folder, or the claimant folder isn't found
- the claimant's folder has no image file anywhere inside it (`.png`, `.jpg`, `.jpeg`, `.heic` or `.webp`), which is normal for a provider who hasn't submitted yet. Any other file type doesn't count
- more than one claimant folder matches, in which case it prints the candidates
- the scheduled year isn't 2026 (see the limits below)

A skipped case stays in Ongoing.

### Date Completed

It is the screenshot's last-modified time on Dropbox, not the time the move ran, shown in Arizona time, for example `10/19/2026 03:35 PM`. If a provider replaces the screenshot later, the time changes with it. If the claimant's folder holds more than one image, it uses the one that comes first when the full file paths are sorted alphabetically, with capital letters before lowercase. That is decided by the names, not by time, so it isn't necessarily the newest or the oldest, and the time can come from any of them. The time the file first arrived isn't used. Whether it should be is still to be decided with the team. Payment Status is left blank.

### Known limits

- It only handles 2026: a case scheduled in any other year is skipped, because the year is written into the code and `DROPBOX_YEAR_FOLDER` points at a single year's folder. Cases outlive their year (a December exam can be submitted in January), so both years' folders will need to be reachable at once. How to do that depends on what the app can see in production, and is still to be decided. Today the app is shared only the `2026` folder, because storage space limited what could be shared.
- Skipped cases are reported only on the console. A case with a misspelled provider or folder name just stays in Ongoing.
- Its name matching has no automated tests yet, so the preview is the check.
- If a move fails after the case is written to Finished but before it is deleted from Ongoing, running it again can add the case to Finished a second time.
- Any image file in the claimant's folder counts as a submission, and its name isn't checked, because screenshot names vary by device. In practice the folder holds only the screenshot, so several images should be rare. If there are several, it picks the first by file path in alphabetical order, which is arbitrary, so Date Completed could come from any of them. Treating several images as ambiguous, as it does for several matching folders, is a possible change.

## Testing

### Automated tests

Install the development dependencies, then run the suite from the project root:

```bash
pip install -r requirements-dev.txt
python -m pytest -v
```

Use `python -m pytest`, not plain `pytest`, so the project root is on the import path.

The tests run in memory. They need no Dropbox or Google credentials, because the Google Sheets code takes the sheet as a parameter and the tests hand it a stand-in from `tests/fakes.py`. They cover the comment parser, the Services lookup and its tab, the sweep, adding and moving cases, the console report, the schedule sort, and the PDF pipeline.

`tests/test_local_pipeline.py` runs the real extraction and report code against sample PDFs. It needs the local-only `templates/` and `testing pdfs/` folders and skips itself, with a reason, when they are missing.

A stand-in only behaves the way it was written to. It cannot show how real Google Sheets behaves, which is where bugs have turned up before. Anything that talks to the real sheet should also get a live check against your own sandbox sheet.

### Live checks

```bash
python scripts/check_sheet_connection.py
```

This opens the sheet set in `.env` and prints its tabs and their headers. It does not import from `app/`, so it runs directly.

```bash
python -m scripts.check_case_metadata
```

This runs the stored-original-tests code against the real Google Sheets API, on a scratch tab named `MetadataCheck` that it creates and deletes. It checks that entries can be saved, replaced, and read back, and that they follow their rows through inserts, sorts, and deletes. It also checks the tab with sheet ID 0 (the first tab created with a spreadsheet), which the API describes differently from every other tab. Run it after changing the storage code, and once against any new spreadsheet. It needs `-m`, because it imports from `app/`.

### Trying the DOCX pipeline by hand

Sample PDFs are in `testing pdfs/` (local only). To test end to end:

1. Put one sample PDF in the configured Dropbox input folder.
2. Run `python main.py`.
3. Confirm the PDF was moved to the processed Dropbox folder.
4. Confirm the matching schedule DOCX was uploaded to the Dropbox schedules folder.
5. Confirm the local report DOCX was created in `generated_docs_folder`.
6. Check `logs/scheduler.log` for extracted values and final counts.

To test extraction only, with no Dropbox involved:

```bash
python -c "from app.pdf_extractor import extract_schedule_data as e; [print(k, v, sep=': ') for k, v in e('testing pdfs/Test1-may4_2026.pdf').items()]"
```

### Trying the sheet by hand

Use your own sandbox sheet, never the production one.

1. Add a case: `python -m app.ongoing_row_mapper`, with a sample PDF.
2. Type a note in that case's Comments cell, for example `Provider: Dr. Doe`.
3. Run the sweep: `python -m app.apply_comment_updates`.
4. Check the Provider column and read the report.
5. Make a deliberate typo in the comment, such as `Provder:`, and run the sweep again. The Comments cell should turn orange with a note that suggests the right spelling.

## Troubleshooting

**`DROPBOX_APP_KEY not found in .env`** (or the secret, or the refresh token)

Make sure `.env` exists in the project root and contains the Dropbox credentials. If only the refresh token is missing, run `python get_refresh_token.py`, approve the app, and paste the token into `.env`.

**`Startup health check failed: Missing .env value: DROPBOX_INPUT_FOLDER`**

The message names what's missing. The three Dropbox folder variables, the settings in `config/settings.json`, and the template files are all checked before any PDF is processed. A `Template file not found` message means `templates/` is missing, since it isn't in the repository.

**`not_found` on the first Dropbox call**

A Dropbox folder variable doesn't match the app's access type. App folder apps use short paths such as `/incoming_pdfs`. Full Dropbox apps need the full path, such as `/Apps/<your app name>/incoming_pdfs`. See [Dropbox folder paths](#dropbox-folder-paths).

**`DROPBOX_YEAR_FOLDER is missing from .env`**

Add it to `.env`. Only the screenshot matcher uses it, and it is checked when the matcher looks for a date folder, so no other command needs it.

**`ZoneInfoNotFoundError`**

The timezone package isn't installed. Run `pip install -r requirements.txt`, which includes `tzdata`. Windows has no timezone database of its own.

**The screenshot matcher skips every case, or one you expected it to match**

Read the reason on the `SKIP` line. `Could not find month folder` or `date folder` usually means `DROPBOX_YEAR_FOLDER` points at the wrong place, or the month and day folders aren't zero-padded (`09-2026`, `09-21-2026`). A missing provider or claimant folder usually means a spelling difference. The `Provider` column must be filled in and must match the provider folder's name. `Claimant matched, but no screenshot was found` means the provider hasn't submitted yet, or the file isn't an image. `More than one matching claimant submission` means two folders normalize to the same name. Fix the names, then run the preview again.

**`ModuleNotFoundError: No module named 'app'`**

Run the script as a module from the project root: `python -m app.apply_comment_updates`, not `python app/apply_comment_updates.py`. Only `scripts/check_sheet_connection.py` runs directly.

**`Another scheduler run is already active. Exiting.`**

A previous run left `scheduler.lock` behind. If nothing is running, delete the file.

**The Windows scheduled task does nothing**

See the mistakes listed under [Scheduling on Windows](#scheduling-on-windows), and check the task's History tab.

**No PDFs are processed**

Confirm that `DROPBOX_INPUT_FOLDER` is correct and the folder contains files ending in `.pdf`.

**The wrong adult or child template is selected**

Check that the PDF contains a readable date of birth and appointment date. The app compares the claimant's age on the schedule date to `adult_age_cutoff`.

**Email notifications do not send**

Confirm `email_notifications_enabled` is `true`, the SMTP values are in `.env`, and the account allows app-password or SMTP access. Microsoft 365 mailboxes no longer accept a plain username and password.

**`FileNotFoundError` for `service_account.json`**

The key isn't where `GOOGLE_SERVICE_ACCOUNT_FILE` points. The default is `config/service_account.json`.

**A Google error saying permission denied, or `SpreadsheetNotFound`**

Usually the sheet isn't shared with the service account's email address, or `GOOGLE_SHEETS_ID` is wrong. An error saying an API "has not been used in project ... or it is disabled" means the Sheets API or the Drive API needs enabling in the Google Cloud project.

**A `ValueError` naming a column, such as `'Comments' column not found`**

A header the code needs is missing or has been renamed. See [Tabs and columns](#tabs-and-columns).

**`The row was written, but: Case ... was added at row ..., but its original tests couldn't be stored`**

The row is in the sheet, but the hidden copy of its original tests wasn't saved, usually because of a network or permissions error, shown after the colon. Nothing is lost. Run the mapper again with the same PDF, which updates the row and tries again. If it keeps failing, run `python -m scripts.check_case_metadata`.

**`A Case ID is needed to store a case's original tests`**

The PDF's Case ID couldn't be read, so nothing was written. Check the PDF.

**`No tab named 'Services' was found`**

The tab must be named exactly `Services`. The sweep still runs without it, and flags `Add:` and `Change:` lines as "services list not loaded".

**A cell or row is colored and has a note**

See [Flags on the sheet](#flags-on-the-sheet) for what each one means and how to clear it.

## Security

This repository is public. Treat everything committed to it as visible to anyone, permanently.

**Never commit:**

- `.env` or `config/service_account.json`, or any key, token, or password
- anything with claimant details: names, dates of birth, case IDs, phone numbers. That includes screenshots, sample files, and logs pasted into a pull request or issue.
- `templates/`, `testing pdfs/`, `data/`, `logs/`, generated reports, or `.venv/`
- real values in `.env.example`, which is for variable names only

Add files by name (`git add path/to/file`), not `git add .`, and read `git status` and `git diff --staged` before committing.

Use your own Dropbox app and your own copy of the sheet while developing. Production credentials belong only on the machine that runs production.

**If a secret is committed,** treat it as exposed even after deleting it, because the history keeps it and the repository is public. In this order:

1. Revoke it and make a new one. For Dropbox, regenerate the app secret and revoke the token in the App Console. For a Google service account, delete the key in the Cloud Console and create another. For SMTP, change the password.
2. Tell the team.
3. Only then, if needed, clean the history.

## Maintenance

- Update `requirements.txt` whenever dependencies change, and add any new environment variable to `.env.example` without a real value.
- Keep `run_scheduler.sh` paths in sync with the machine where it runs.
- If the PDF format changes, update the extraction logic in `app/pdf_extractor.py`.
- If the schedule table changes, update the headers and row generation in `app/schedule_generator.py`.
- If template placeholders change, update the replacements in `app/template_filler.py`.
- The Services tab can be edited with no code change. Add rows and aliases there.
- Test editions change over time, and a mismatch is the first thing to check when a test isn't recognized. Trinity Tree gives the current edition of a test even when a referral lists an older one, so a PDF can show an older code than the test actually given. How newer editions should appear in the Services tab is still being decided.
- The sheet layout is a draft. Check [Tabs and columns](#tabs-and-columns) before renaming a header.

**Known gaps**

- The DOCX pipeline and the sheet are not connected. Cases are added to the sheet by hand.
- Test changes are checked but not written into the sheet.
- A flagged comment whose text is deleted entirely keeps its orange fill and note.
- The sweep ends with a "Press Enter to close" prompt, which would hang a scheduled run.
- A PDF whose case ID can't be read is written to the sheet as a row with a blank Case ID.

### Contributing

**Branches and pull requests**

- Start from a fresh `main`: `git checkout main`, `git pull origin main`, then `git checkout -b <type>/<short-name>`, where the type is `feature`, `fix`, `docs`, or `chore`.
- Nothing is pushed straight to `main`. Every change goes through a pull request and needs an approval. Ask both other teammates to review, and whoever is free first can take it. Changes to shared files, such as `tests/fakes.py`, `.gitignore`, `requirements.txt`, `config/settings.json`, `app/sheet_client.py`, or anything touching credentials, should be seen by both.
- One concern per pull request. If unrelated changes pile up on a branch, split them into separate branches. Stack one branch on another only when the second really needs the first.
- Keep a pull request small enough to review. A very large change with no docstrings can't be reviewed.

**Before you push**

- Run `python -m pytest`. New behavior comes with tests. Write logic so it takes the worksheet or client as a parameter, so it can be tested with the stand-ins in `tests/fakes.py`.
- Anything that touches the real sheet or Dropbox also gets checked by hand against your sandbox, and a script in `scripts/` if the check is worth repeating.
- Don't name an imported function so it starts with `test`: pytest collects it as a test.
- Anything that moves or changes real data defaults to a dry run and needs an explicit flag, such as `--live`, to act.
- Don't fail silently. When something can't be used, say what and why: a flag on the sheet, or a message naming it. Don't guess.
- Put a docstring at the top of each module explaining the flow and the rules, and on each public function.
- Update this README when behavior changes.

**The pull request description** is written in markdown, with:

1. **Summary** and **Why**
2. **Changes**, file by file
3. **How to test**: the exact commands and what to expect
4. **Not in this PR**: what is deliberately left out, and any known limits

Say what you checked by hand, and name any behavior change.

**Reviewing**

- Separate what blocks the merge from what can follow. Say why something is a problem, not just that it is.
- Ask about code you haven't seen instead of assuming.
- Check a fix in the code before resolving its thread. A "request changes" review stays until the same reviewer approves.

## License

No license file is currently included, so by default all rights are reserved. Trinity Tree should decide whether to add one, since the repository is public.