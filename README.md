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
- Resolves short test names such as `wms` or `wisc` into full service codes through a `Services` tab, and applies `Add:` and `Change:` lines to the case's Services Requested cell, marking each added or swapped test. Unknown or ambiguous names, and changes that don't fit the case, are flagged.
- Flags problems directly on the sheet, with a note and, for cases, a colored cell, and prints a plain-language report after each run. The sweep previews by default and only writes with `--live`.
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
│   ├── apply_comment_updates.py  # The sweep: applies OA's comments, test changes included, to the Ongoing tab (run_sweep, and the command line)
│   ├── case_metadata.py          # Saves each case's original test list as hidden data on its row (the rules)
│   ├── comment_parser.py         # Parses the Comments column format
│   ├── config_loader.py          # Loads config/settings.json
│   ├── contact_lookup.py         # Matches the names in comments to the people in the Contacts tab
│   ├── contacts_tab.py           # Loads the Contacts tab, and notes its problems on the tab
│   ├── exam_date.py              # Reads the exam date out of the Date & Time text
│   ├── file_utils.py             # Unused, kept from the original project
│   ├── health_monitor.py         # Logging, startup checks, email alerts
│   ├── mail_sender.py            # Microsoft Graph: sign in and send one email
│   ├── ongoing_row_mapper.py     # Maps an extracted PDF to an Ongoing row, adds or updates it
│   ├── pdf_extractor.py          # PDF parsing and data extraction
│   ├── reminder_rules.py         # Dr.'s reminder schedule: when each goes out, and what it says
│   ├── reminders.py              # python -m app.reminders: previews today's reminders, or sends them (--live)
│   ├── reminders_report.py       # The reminders report as text
│   ├── reminders_send.py         # Sends the planned reminders one at a time and logs each
│   ├── reminders_sent_log.py     # Writes the Reminders Sent tab
│   ├── reminders_sheet.py        # Reads the Ongoing, Finished and Reminders Sent tabs
│   ├── run_lock.py               # A lock file with a stale limit, so two runs can't overlap
│   ├── schedule_generator.py     # Daily schedule DOCX creation/update
│   ├── screenshot_matcher.py     # Finds a provider's submitted screenshot in Dropbox and moves the case to Finished
│   ├── service_changes.py        # Applies a comment's test changes to a case's original tests, in order
│   ├── service_format.py         # The shared pattern for a service code
│   ├── service_lookup.py         # Turns typed test names into service codes
│   ├── services_cell.py          # Reads and writes the Services Requested cell's text and markers
│   ├── services_tab.py           # Loads the Services tab and flags problems on it
│   ├── services_update.py        # Decides what the cell should become, and when not to touch it
│   ├── sheet_case_mover.py       # Moves a case from Ongoing to Finished
│   ├── sheet_client.py           # Google Sheets connection and cell helpers
│   ├── sheets_metadata_client.py # The Google Sheets calls behind case_metadata.py
│   ├── state_manager.py          # Unused, kept from the original project
│   ├── sweep_report.py           # Plain-language console report for a sweep
│   └── template_filler.py        # Report template placeholder replacement
├── config/
│   └── settings.json             # Runtime folders and scheduler settings
├── docs/
│   ├── legacy_setup_notes.md               # Notes kept from the original project's setup guide
│   └── manual_check_apply_test_changes.md  # Step-by-step checks to run after changing the sweep
├── scripts/
│   ├── check_case_metadata.py    # Live check of the stored original lists, on a scratch tab
│   ├── check_screenshot_to_finished.py # Runs the screenshot matcher (preview by default)
│   ├── check_sweep_live.py       # Live check of the sweep on scratch tabs: scenarios 1 to 16 and 19
│   ├── check_reminders_live.py   # Live check of the reminders: scratch tabs, two test inboxes
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

Runtime folders such as `data/`, `logs/`, `reports/`, `.venv/`, `__pycache__/`, and `.env` stay out of GitHub, and so do the lock files (`scheduler.lock`, `sweep.lock`).

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

## Microsoft Graph Setup
 
The reminders are sent through Microsoft Graph (`sendMail`), as the support mailbox, by an
app registered in Microsoft Entra ID. No one signs in when it runs: the app signs in with its
own client secret and sends as the mailbox. Nothing here is needed to preview.
 
**Settings** in `.env` (names only, see `.env.example`; never commit values):
 
| Setting | What it is |
|---|---|
| `MICROSOFT_TENANT_ID` | The Microsoft 365 tenant (directory) the app is registered in |
| `MICROSOFT_CLIENT_ID` | The app registration's application (client) ID |
| `MICROSOFT_CLIENT_SECRET` | The app's client secret. Expires, see below |
| `MICROSOFT_SENDER_EMAIL` | The mailbox the emails are sent from |
| `STAFF_CC_EMAILS` | Copied on the Day 11 overdue notice (the Director), comma-separated |
| `REMINDERS_TEST_RECIPIENT` | Where `--live --test` sends everything |
| `REMINDERS_GO_LIVE` | `true` switches on sending to the providers. Keep it off until Dr. has approved the emails |
 
The app needs the **Mail.Send** permission, as an *application* permission, with admin
consent given for the tenant. It signs in to `https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token`
asking for `https://graph.microsoft.com/.default`.
 
**Who has what.** To be filled in:
 
| | |
|---|---|
| App registration (name, and who owns it) | TBA |
| Client secret expires on | TBA |
| Admin consent for Mail.Send given by | TBA |
| Sending mailbox | the account in `MICROSOFT_SENDER_EMAIL` (expected to be the support account; TBA) |
| Support account sign-in | 2-step verification is on Dr.'s phone |
 
**The secret expires.** When it does, every send fails at sign-in, with nothing sent and an
alert (`Microsoft sign-in failed`, usually `invalid_client`). Put a reminder in a calendar a
month before the date above. To replace it:
 
1. In the Microsoft Entra admin center, open the app registration, then Certificates &
   secrets, and choose New client secret.
2. Copy the secret's **Value** at once. It is shown only once.
3. Put it in `MICROSOFT_CLIENT_SECRET` in `.env` on every computer that runs the reminders.
4. Check it with `python -m app.reminders --live --test`.
5. Only then delete the old secret, and update the date above.
 
**Known limit.** Mail.Send as an application permission lets the app send as any mailbox in
the tenant, not only the support one. Limiting it to one mailbox needs a tenant administrator
(an application access policy in Exchange Online) and is still to do.
 
**Trying it.** `python -m app.reminders --live --test` sends every due reminder to
`REMINDERS_TEST_RECIPIENT`, marked `[TEST]`, and records nothing, so it can be repeated.

## Configuration

Settings are split in two, by one rule: anything that is the same for everyone lives in `config/settings.json`, which is committed. Anything that differs between machines or people lives in `.env`, which is not. That's why Dropbox folder paths (including the screenshot matcher's year folder), credentials, and the sheet ID are in `.env`(see [Setup](#setup)).

`config/settings.json` holds the shared runtime settings:

```json
{
  "base_folder": "data",
  "adult_age_cutoff": 18,
  "max_files_per_run": 200,
  "sweep_lock_stale_minutes": 15,
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
- `sweep_lock_stale_minutes`: how old, in minutes, a sweep's lock must be before the next live sweep assumes its owner died and takes it over. 15 if missing. Must be a positive number, or 15 is used and a warning is logged. See [Running the sweep](#running-the-sweep).
- `reminders_lock_stale_minutes`: the same, for the reminders' own `reminders.lock`: how old, in minutes, that lock must be before the next live reminders run assumes its owner died and takes it over. Optional, and not in the file by default. 15 if missing. Must be a positive number, or 15 is used and a warning is logged. See [Reminders](#reminders-who-is-emailed-today).
- `adult_template_path`: path to the adult DOCX template.
- `child_template_path`: path to the child DOCX template.
- `generated_docs_folder`: local folder for filled report documents.
- `health_check.log_folder`: folder for scheduler logs, the sweep's included.
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

Windows Task Scheduler can run the DOCX pipeline (`main.py`) on a repeating schedule. This schedules the pipeline only. See "Scheduling the sweep" at the end of this section for the sheet.

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

### Scheduling the sweep
 
The sweep (`python -m app.apply_comment_updates`) can be scheduled the same way, with different fields in the action. This has not been tried yet.
 
- **Program/script:** the full path to the virtual environment's Python, as above
- **Add arguments:** `-m app.apply_comment_updates --live`
- **Start in:** the project root. `-m` needs it to find `app/`, and the sweep reads `config/settings.json` from there.
 
Two rules. Never add `--pause`, which makes the run wait for a key nobody will press. And the arguments must include `--live`, since without it the command only previews and writes nothing.
 
Before scheduling it:
 
- **Read its log, not the console.** A scheduled run prints nowhere anyone sees. Its report goes to `logs/sweep.log`, and a failure also goes to `logs/sweep_error.log` and makes the task's Last Run Result non-zero. Check there after the first scheduled runs.
- **A skipped run looks like a success.** If a run finds the lock held it exits successfully, so Task Scheduler's Last Run Result stays green. Look for `Sweep skipped (live)` in `logs/sweep.log` to see how often that happens. A skip on every run means a lock is stuck, or a run takes longer than the interval.
- **The lock doesn't replace Task Scheduler's own setting** for a task that is still running when it is due again, on the task's Settings tab. Check it. The lock covers what that setting can't: a run started by hand during a scheduled one, and two separate tasks.
- **Run it by hand first,** as a preview and then with `--live`, against the sheet it will run on.
- **It is safe to repeat.** A rerun on an unchanged sheet writes nothing, so the interval can be short. How often OA's comments should be picked up is still to be decided.
 
The screenshot matcher is not scheduled either. If it is, the same two rules apply to `-m scripts.check_screenshot_to_finished --live`.

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

The sweep writes its own files in the same folder, so that two processes never share a rotating file. Rotating a file that another process has open can fail on Windows, and the record being written is lost.
 
```text
logs/sweep.log          everything from INFO up
logs/sweep_error.log    failures only
```
 
Each run logs when it started and in which mode (`live` or `preview`), the whole report as a single record, and a one-line finish with counts and how long it took:
 
```text
2026-10-30 11:33:05,209 | INFO | trinity_scheduler | Sweep finished (live): 6 comment(s), 0 field(s) updated, 2 flagged, 7.9s
```
 
A run with any flagged case is logged at WARNING and says how many, so those runs stand out. A failure is logged with its traceback, the alert email is sent if emails are enabled, and the command exits with an error, so Task Scheduler's Last Run Result shows it. If `config/settings.json` can't be read, the sweep logs a warning and uses a `logs/` folder in the folder it was started in.

A run that finds another live sweep running logs a single WARNING, `Sweep skipped (live): another sweep is running (...)`, with no start, report, or finish. A run that takes over a stale lock logs a WARNING, `Took over a stale sweep lock (...)`, before it starts.
 
The report has Case IDs, staff names, and test codes, and no claimant names. The screenshot matcher does not log. It prints its output, which includes patient names, to the console only. That is listed under Known gaps.

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
| Ongoing | `Case ID` everywhere. `Provider`, `Psychometrist`, and `Comments` for the sweep. `Services Requested` for applying test changes (without it the sweep still runs, and says test changes can't be applied). The other headers are filled from the PDF if present and left blank if not. |
| Finished | `Case ID`. Any header that also exists in Ongoing is copied across. `Payment Status` and `Date Completed` are filled only by whatever moves the case, and are blank otherwise. |
| Services | `Code` and `Aliases`. `Name` is read too, as the description on lines the sweep adds, and may be blank. `Price` is not read. |

These columns are a draft and may change. If you rename or remove one, search the repository for the header's name to find what uses it, for example `git grep -n "Payment Status"`.

Who fills what on the Ongoing tab: everything up to `Allegations`, plus `Price`, comes from the PDF. `Services Requested` also comes from the PDF, and the sweep adds to it when OA's comment asks for test changes. `Psychometrist` and `Provider` are filled by the sweep from the Comments column. `Comments` is written by OA. `Price` is the total printed on the PDF. Final billed amounts are set separately on the billing request forms and are not tracked here, and the sweep never changes `Price`.

### The Contacts tab: provider and psychometrist emails
 
The reminders need an email address for the provider and the psychometrist of each case. Nobody types addresses into comments. OA keeps writing names, and the sweep looks each name up in a tab and puts the address on the case's row, where the reminders read it.
 
It needs two things on the sheet:
 
- A tab named `Contacts`, with the columns `Name` and `Email`, and optionally `Role`, which is `Provider` or `Psychometrist`. Columns are found by header name, so their order doesn't matter.
- Two columns on the Ongoing tab, named `Provider Email` and `Psychometrist Email`. **Until they exist, the sweep does nothing about emails**, so adding them is what switches this on.
 
| Name | Email | Role |
|---|---|---|
| Dr. Jane Smith | jane.smith@example.test | Provider |
| John Doe | john.doe@example.test | Psychometrist |
 
The tab is set up once, with each person's name and address, and OA (or Dr.) adds a row when someone new starts. The real addresses live only in the sheet. They are never in the repository, the logs, the report, or a note.
 
**How OA writes names.** In the comment, as before:
 
```
Provider: Dr Smith
Psychometrist: John Doe
```
 
- **Provider:** `Dr` and the last name in full. Add the first initial (`Dr J Smith`) only if two providers share a last name. The sweep says so, and what to write, when it matters. An initial alone (`Dr S`) can't be matched.
- **Psychometrist:** the full name, `John Doe`. `J. Doe` also works while only one J. Doe is listed.
 
Names are matched ignoring capitals, accents, punctuation, a leading `Dr`, and middle names. `Role` keeps a provider `Smith` and a psychometrist `Smith` apart. A row with no role counts as both. A name that fits two people is never guessed.
 
**What the sweep does**
 
- For each case whose comment names a provider or a psychometrist, it fills the matching email column. It checks again on every run, so a corrected address in the tab reaches the row on the next sweep, and an address typed into the cell by hand is replaced by the tab's.
- A name it can't resolve is flagged on the Comments cell, in the usual orange note, and the email cell is left alone. The flag says what to do.
- If the provider has just **changed** and the new one can't be found, the old address is cleared, because it now belongs to someone else, and a reminder to the wrong person is worse than none.
- Problems with the tab itself (a row with no valid address, a name listed twice, a missing column) are noted on its `A1` cell, which clears itself once they are fixed. A missing or unusable `Contacts` tab never stops the sweep.
- The report says, for each case, whether the email was filled in, by the person's name, and the summary counts them. It never prints an address.

### Reminders: who is emailed today
 
Dr.'s schedule reminds each provider about the materials and the report for a DDS case.
The command **previews by default**: it prints who would be emailed and why, and sends
nothing and writes nothing to the sheet. With `--live` it sends the emails, and records each
one in the `Reminders Sent` tab.
 
| Day | Reminder | Goes out |
|---|---|---|
| 1 | Upload behavioral observations and testing materials (target: within 48 hours of the exam) | always |
| 3 | Materials overdue | only if materials aren't uploaded (not sent yet, see below) |
| 5 | Midpoint check-in on the report | until the report is in |
| 8 | Report due in 2 days | until the report is in |
| 10 | Report due today | until the report is in |
| 11 | Overdue notice, **director copied** | until the report is in |
 
Day 0 is the date of the exam, and deadlines are calendar days, so the report is due 10
days after it. Everything goes to the provider. A provider who meets every deadline gets at
most four reminders.
 
**Weekends.** Nothing is sent on a Saturday or Sunday. Day 8 and Day 10 warn before a
deadline, so on a weekend they go out the Friday before, and say the due date instead of
"today". Days 1, 3, 5 and 11 follow something that has already happened, so on a weekend they
go out the Monday after (Day 11 can't go earlier: nothing is overdue yet). If two reminders
land on one day, only the later one goes.
 
**What it needs on the sheet**
 
- The Ongoing tab's `Date & Time` (the exam date is read from it, and the time of day is
  ignored), `Provider` (for the greeting) and `Provider Email`, which the sweep fills in from
  the Contacts tab. Without the `Provider Email` column the command stops and says so.
- A `Finished` tab. A case listed there has its report in, and gets no reminders.
- A `Reminders Sent` tab. The command creates it the first time it sends, with the columns
  `Case ID`, `Reminder` (such as `DAY_5`), `Recipient` (the provider's name, never an
  address), `Sent On` and `Sent At` (Arizona time). A reminder listed there counts as already
  sent, so a second run the same day sends nothing new. A row added by hand only needs `Case
  ID` and `Reminder`. Without the tab, nothing counts as sent.
 
**Run it after the sweep and the screenshot matcher**, in this order, since the reminders
use what they leave on the sheet: the sweep fills each case's `Provider Email`, and the
matcher moves a case to Finished once its report is in. A case whose report is in but hasn't
been moved yet would still be reminded.
 
**Ways to run it**
 
```
python -m app.reminders                      # preview today's reminders (Arizona time)
python -m app.reminders --today 2026-11-23   # preview as if today were that date
python -m app.reminders --show-text          # also print each email's text
python -m app.reminders --live --test        # send them ALL to the test address
python -m app.reminders --live               # send them to the providers
```
 
Add `--pause` to wait for Enter at the end, for a double-clicked shortcut.
 
| | Emails go to | Writes `Reminders Sent` | Needs |
|---|---|---|---|
| Preview | nobody | no | nothing |
| `--live --test` | `REMINDERS_TEST_RECIPIENT`, marked `[TEST]`, nobody copied | no, so a real run afterwards still sends | the four Microsoft settings and `REMINDERS_TEST_RECIPIENT` in `.env` |
| `--live` | the providers (Day 11 also copies `STAFF_CC_EMAILS`) | yes, one row after each accepted email | the four Microsoft settings, `REMINDERS_GO_LIVE=true` and a valid `STAFF_CC_EMAILS` in `.env`, and no `--today` |
 
**`--live` alone does not email a provider.** Real sending is switched off until
`REMINDERS_GO_LIVE=true` is in `.env`. Leave it off until Dr. has approved the wording of the
emails, and try everything with `--live --test` first. `--today` works on a preview and with
`--test`, but not on a real send, which could only email providers as if it were another
day. A run that finds any of these unmet refuses to start, says why before it touches the
sheet, and exits with code 2. See [Microsoft Graph Setup](#microsoft-graph-setup) for the
settings.
 
The overdue notice must copy the Director. If `STAFF_CC_EMAILS` is empty it is listed under
"needs attention" and not sent, and the other reminders still go.
 
**While it sends.** Emails go one at a time, and it signs in to Microsoft once. With nothing
to send it doesn't sign in, and doesn't create the `Reminders Sent` tab. A row is written as
soon as Microsoft accepts an email. ("Accepted" is Microsoft's `202`: the email is on its
way, not proof it was delivered.)
 
- **A send fails.** It is listed under "Failed to send", not recorded, and the other
  reminders still go. Run the command again the same day and it is tried again. The run
  exits with code 1 and sends the usual alert email.
- **A row can't be written.** The run stops at once, so it can't send something it has no
  record of. The report says `STOPPED`, lists what it didn't attempt, and names the case and
  reminder to add to the `Reminders Sent` tab by hand. Without that row the next run would
  send it again.
 
**Two runs at once.** A live run takes `reminders.lock` in the folder it runs from. A second
live run started while it is going does nothing and says so. A lock older than 15 minutes is
taken over, and the limit can be changed with `reminders_lock_stale_minutes` in
`config/settings.json`. A preview takes no lock. The lock only covers one computer, so run
the reminders from one.
 
The report is headed `PREVIEW`, `LIVE` or `LIVE TEST`, so the kind of run can't be mistaken.
It lists, in this order: the reminders that were sent (or, in a preview, **would be sent**),
with the provider's name and the subject, never an address; any that **failed to send**, and
the `STOPPED` notice and what **wasn't attempted**; cases that **need attention** (the exam
date can't be read, no provider is named, the provider has no usable address, or there is
no Director address to copy); reminders due but **not sent** on purpose, with the reason;
reminders **already sent**; and a summary. It is also logged, in `logs/reminders.log` (and
`logs/reminders_error.log` for failures), apart from the sweep's and the pipeline's logs.
No address, secret or sign-in token is ever printed or logged.

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

**Two sweeps at once**
 
A live sweep takes a lock, the file `sweep.lock` in the folder it was started from, and gives it back when it finishes, even if it fails. A second live sweep that finds the lock held does nothing: it prints and logs one warning that names the holder, and exits **successfully**, since an overlap isn't an error.
 
```text
Another live sweep is already running (held by process 4321 (live) since 2026-11-02T10:00:05, 2.0 minutes old), so this run did nothing.
```
 
- **Previews take no lock** and are never blocked, because they write nothing.
- **A lock that is too old is taken over.** A run killed without a chance to clean up, by a power cut, a closed window, or `taskkill`, leaves its lock behind. So a lock older than `sweep_lock_stale_minutes` (15 by default, in `config/settings.json`) is assumed to belong to a dead run, and the next live sweep takes it over and logs a warning. The age is that of the file, not what is written in it.
- **The limit should be well above a normal run.** A run takes seconds. If runs ever get close to the limit, raise it. If it is too low, a slow but healthy run can be taken over. If it is too high, a crash blocks the sweep until the limit passes.
- **A lock that can't be created** (for example in a read-only folder) is a failure, not a skip. It is logged, the alert is sent, and the run exits with an error, because a missing lock would leave overlaps unprotected.

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
Every run, previews included, is also written to `logs/sweep.log`, labeled with its mode, so what a scheduled run did can be read afterwards. See [Logs and Alerts](#logs-and-alerts).

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

**If a move is interrupted.** The case is written to Finished before it is deleted from Ongoing, so a failure in between leaves it in both tabs. Running the same move again is safe. It finds the case already in Finished and adds no second row. It fills only the blank cells it has a value for, leaves everything else alone, logs a warning, and then deletes the case from Ongoing.
 
It refuses, changing nothing and leaving the case in Ongoing, when the Finished row isn't a clean match for what it would write:
 
- **A different value in a column it would write,** for example another Price or Date Completed. The error names the columns. If the Finished row is right, delete the case's row from Ongoing by hand. Otherwise fix the Finished row, then run the move again.
- **The case is in Finished more than once,** for example from a move that was retried before this was fixed. The error names the rows. Remove the extras first.

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
| `Provider Email  (not filled in: 'Dr Patel' isn't in the Contacts tab. ...)` | The name isn't in the `Contacts` tab. Add the person there, or check the spelling. The email cell is left alone. The same for `Psychometrist Email`. |
| `... 'Dr S' is too short to match. Write the name in full, for example 'Dr Smith')` | A single letter can't be matched. Write the last name in full. |
| `... 'Doe' could be John Doe, Jane Doe. Write more of the name, for example 'John Doe')` | The name fits more than one person, so none was chosen. Write more of it. |
| `... the Contacts tab couldn't be used, so emails can't be looked up)` | The tab is missing, empty, or has no `Name` or `Email` column. The tab's own note says which. |
| `... The old address was cleared)` | The provider or psychometrist changed to someone who couldn't be found, so the previous person's address was removed. |
 
**What the lines in a Comments note mean**
 
| The line says | Meaning, and what to do |
|---|---|
| `... (skipped: unknown test 'wisk', did you mean WISC?)` | A test name isn't in the Services tab. Fix the spelling, or add the test to the tab. |
| `Add: X  (skipped: X is already on this case)` | The case already has that test. Delete the line. |
| `Change: X to Y  (skipped: X isn't on this case)` | The test to swap isn't on the case at that point. Fix the line, or its order. |
| `... (kept: the cell has it from an earlier change, but this comment no longer includes it. ...)` | A change was applied before, and its line is gone from the comment. The test stays. Put the line back to keep it as a change, or delete the test's line from the cell to remove it. |
| `... (kept: the cell has this line, but it isn't in the PDF's list or this comment. ...)` | Someone typed a line into the cell. It stays. Add it to the comment to keep it as a change, or delete it from the cell. |
| `... (dropped: ...)` | An earlier change conflicts with the new comment, so it was removed from the cell. The reason is in the line. |
| `Services Requested  (not changed: ...)` | The case's tests were left alone. The reason says why: a line in the cell can't be read, the original tests weren't saved and the cell is already changed, the case has no tests recorded, the saved original belongs to a different case (`Row N: the stored test list is for case X, not Y, so it was ignored`), or saving the original failed. |
 
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
- More than one case matches the same screenshot (all conflicting cases are skipped).

A skipped case stays in Ongoing.

### Date Completed

It is the screenshot's last-modified time on Dropbox, not the time the move ran, shown in Arizona time, for example `10/19/2026 03:35 PM`. If a provider replaces the screenshot later, the time changes with it. If the claimant's folder holds more than one image, it uses the one that comes first when the full file paths are sorted alphabetically, with capital letters before lowercase. That is decided by the names, not by time, so it isn't necessarily the newest or the oldest, and the time can come from any of them. The time the file first arrived isn't used. Whether it should be is still to be decided with the team. Payment Status is left blank.

### Known limits

- It only handles 2026: a case scheduled in any other year is skipped, because the year is written into the code and `DROPBOX_YEAR_FOLDER` points at a single year's folder. Cases outlive their year (a December exam can be submitted in January), so both years' folders will need to be reachable at once. How to do that depends on what the app can see in production, and is still to be decided. Today the app is shared only the `2026` folder, because storage space limited what could be shared.
- Skipped cases are reported only on the console. A case with a misspelled provider or folder name just stays in Ongoing.
- - claimant name conversion has automated unit tests, but matching against actual Dropbox folders is not covered by automated tests; use the dry-run preview to verify folder matches
- A move that finds the case already in Finished with different values, or in Finished more than once, refuses with an error and changes nothing. The matcher doesn't catch that error per case, so a refusal ends the whole run, and the cases after it are not moved until the Finished row is fixed. A clean retry after an interruption no longer adds a duplicate row.
- Any image file in the claimant's folder counts as a submission, and its name isn't checked, because screenshot names vary by device. In practice the folder holds only the screenshot, so several images should be rare. If there are several, it picks the first by file path in alphabetical order, which is arbitrary, so Date Completed could come from any of them. Treating several images as ambiguous, as it does for several matching folders, is a possible change.

## Testing

### Automated tests

Install the development dependencies, then run the suite from the project root:

```bash
pip install -r requirements-dev.txt
python -m pytest -v
```

Use `python -m pytest`, not plain `pytest`, so the project root is on the import path.

The tests run in memory. They need no Dropbox or Google credentials, because the Google Sheets code takes the sheet as a parameter and the tests hand it a stand-in from `tests/fakes.py`. They cover the comment parser, the Services lookup and its tab, reading and writing the Services Requested cell, applying test changes, the sweep and its preview mode, adding and moving cases, the console report, the sweep's command line, the schedule sort, and the PDF pipeline.

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

This runs the stored-original-tests code against the real Google Sheets API, on a scratch tab named `MetadataCheck` that it creates and deletes. It checks that entries can be saved, replaced, and read back, and that they follow their rows through inserts, sorts, and deletes. It also checks the tab with sheet ID 0 (the first tab created with a spreadsheet), which the API describes differently from every other tab. Run it after changing the storage code, and once against any new spreadsheet. It needs `-m`, because it imports from `app/`. The reminders have their own live check, which sends real email to two addresses you choose: see [the live check of the reminders](#live-check-of-the-reminders).

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
2. Type a note in that case's Comments cell, for example:
 
```text
Provider: Dr. Doe
Add: wms
```
 
3. Preview the sweep: `python -m app.apply_comment_updates`. It writes nothing. Read the report, and check that the sheet didn't change.
4. Apply it: `python -m app.apply_comment_updates --live`. The Provider column is filled, the Services Requested cell has a new last line ending in `(added)`, and the Comments cell is not orange.
5. Run `--live` again. It should write nothing and say `Tests: already up to date, no change`.
6. Make a deliberate typo in the comment, such as `Provder:`, and run the sweep again. The Comments cell should turn orange with a note that suggests the right spelling.
 
For the full set of checks after a change to the sweep, including rewritten and cleared comments, rows that move, cases that can't be updated, and two sweeps running at once, follow [docs/manual_check_apply_test_changes.md](docs/manual_check_apply_test_changes.md). Scenarios 1 to 16 and 19 can also be run automatically, with [the live check](#live-check-of-the-sweep).

### Live check of the sweep
 
`scripts/check_sweep_live.py` runs scenarios 1 to 16 and 19 of the [manual check](docs/manual_check_apply_test_changes.md) automatically, against the real Google Sheets API, so a change to the sweep can be re-verified in one command. Use your own sandbox sheet, never production.
 
```bash
python -m scripts.check_sweep_live                 # everything
python -m scripts.check_sweep_live --list          # show the groups, and touch nothing
python -m scripts.check_sweep_live --only 6,15     # just these scenarios, with their groups
python -m scripts.check_sweep_live --only moves    # just one group
```
 
It prints the sandbox sheet's title first, then each check as `[OK]` or `[FAILED]`, and ends with a count. The exit code is 0 only if every check passed.
 
**How it stays safe**
 
- It works only on scratch tabs that it creates, named `SweepCheck ...`, and deletes them at the end. A scratch tab left by a run that failed is removed at the start of the next one.
- The sweep is handed a stand-in spreadsheet that gives it only those tabs and refuses every other name, even if a real tab has that name, so it can't reach a real tab.
- Everything the sweep reads and writes is made up by the script, including a small Services table, so a result never depends on what the sandbox happens to hold.
- It runs the sweep's real command line from a temporary folder, so its lock file and logs never touch the project folder.
 
**Groups.** Scenarios that share sweeps run together, each group on a scratch tab of its own, so a group can be run alone. Asking for one scenario runs its whole group, and says so.
 
| Group | Scenarios |
|---|---|
| `chain` | 1, 2, 3 |
| `flags` | 4, 5, 10 |
| `nothing-stored` | 11, 12, 13, 14 |
| `rewrites` | 6, 7, 8, 9 |
| `moves` | 15, 16 |
| `interrupted-move` | 19 |

Scenario 19 runs the real `move_case_to_finished` with a worksheet whose delete fails, so that a move is interrupted after the case has been written to Finished. It then checks that running the move again adds no second row and finishes the job, that a blank cell is filled in, and that a Finished row which disagrees, or a case in Finished twice, makes it refuse and change nothing.
 
**Why it takes a while.** Google allows 60 read requests a minute per user, and the sweep and the checks together would spend that quickly. Every request goes through a pacer that keeps to 45 a minute, and a request refused for being over the limit is tried again after a pause. A full run takes several minutes, and a single group takes less. Lines such as `(pausing 51 s to stay under Google's per-minute request limit)` are normal.
 
**Reading a failure.** The sweep's report is printed above the checks, and a failing check says what it expected and what it found. Compare that with the scenario in the manual check. A mismatch is either a bug, or a rule worth changing on purpose. If it is a rule, change the code, the tests, the README, the manual check, and the script together, and take a scenario's expected results from the manual check, not from what the sweep happened to print.
 
**What it doesn't cover**
 
- Scenarios 17 (the pause) and 18 (the lock, including two real processes at once) stay in the manual check and the unit tests.
- The orange fill on the Comments cell. It checks the note, which the sweep writes together with the fill.
- The screenshot matcher, the DOCX pipeline, and Task Scheduler.
 
The helpers that need no Google (the request pacer and its retry, choosing which scenarios to run, and picking a case out of a report) are tested in `tests/test_check_sweep_live.py`, which runs with the rest of the suite.

### Live check of the reminders
 
`scripts/check_reminders_live.py` runs the reminders command against the real Google Sheets API and, for the groups that send, the real Microsoft account, so a change to the reminders can be re-verified in one command. Use your own sandbox sheet, never production.
 
**Before the first run**
 
1. In the sandbox sheet's `Contacts` tab, have two rows named `Dr. Jane Smith` and `Dr. John Doe` (or pick other names with `--providers "Name One,Name Two"`), and put **your own two email addresses** on them. Those are the only people it can ever email.
2. Have the four Microsoft settings in `.env` (see [Microsoft Graph Setup](#microsoft-graph-setup)). The groups that don't send don't need them.
3. Run it on a weekday. On a weekend no reminder is sent, so it refuses to start.
 
```bash
python -m scripts.check_reminders_live --list              # show the groups, and touch nothing
python -m scripts.check_reminders_live --plan              # what it would do, and touch nothing
python -m scripts.check_reminders_live --only plumbing     # one group
python -m scripts.check_reminders_live --only 6,10         # just these scenarios, with their groups
python -m scripts.check_reminders_live                     # everything
```
 
It prints the sandbox sheet's title first, then each check as `[OK]` or `[FAILED]`, then ends with a count. The exit code is 0 only if every check passed. A `[NOTE]` is something to read, and counts as neither.
 
**Groups.** Each group runs on scratch tabs of its own, so it can be run alone. Asking for one scenario runs its whole group, and says so.
 
| Group | Scenarios | What it shows | Emails |
|---|---|---|---|
| `plumbing` | | A preview of the made-up cases, and that a provider who isn't found stops it | none |
| `quiet` | 3, 9, 11 | A due reminder with no usable address, a `Reminders Sent` tab that is damaged or filled in by hand, and whether failure alerts are on | none |
| `test-mode` | 6, 10 | `--live --test`: `[TEST]` emails to the first provider only, nobody copied, nothing logged; and the lock | about 6, all marked `[TEST]` |
| `failures` | 7, 8 | A wrong sender and a wrong secret, which Microsoft refuses; then a run that sends what failed | 3, real |
| `sends` | 1, 2, 4, 5 | A real send to four cases: each goes to its own provider, a psychometrist's address is never used, only the overdue notice copies the Director, the log rows, a rerun that sends nothing, and a case added afterwards | 5, real |
 
**The `yes` prompt.** Before any group that sends, it shows the sandbox sheet's title and the two addresses, and waits for you to type `yes`. Anything else ends the run with nothing sent.
 
**How it stays safe**
 
- It works only on scratch tabs that it creates, named `ReminderCheck ...`, and deletes them at the end, even when a check fails. A scratch tab left by a run that was cut off is removed at the start of the next one.
- The reminders command is handed a stand-in spreadsheet that maps `Ongoing`, `Finished` and `Reminders Sent` to the scratch tabs and refuses every other name, so it can't reach a real tab. The only real tab it reads is `Contacts`, once, to find the two providers.
- Every case is made up by the script. Your `.env` is read for the Microsoft settings and never changed. For the run, real sending is switched on, the test address is the first provider, and the Director copy goes to the second, so the Director's real address in `.env` is never emailed.
- Every request to Microsoft goes through a guard that blocks an email sent or copied to anyone but the two providers, and any address that isn't Microsoft's, whatever the rest of the script does.
- It refuses to run unless the sheet's title contains `sandbox`.
- It runs the command from a temporary folder, so its lock file and logs never touch the project folder.
 
**After a group that sends: check the inboxes.** The script ends with a list of what each of the two inboxes should now hold, per case and subject. Open both inboxes, and the `Sent Items` of the sending mailbox, and compare the counts. A real run is only proven when the mail arrived, and Microsoft accepting an email (`202`) is not proof of that.
 
**Reading a failure.** The command's report is printed above the checks, and a failing check says what it expected and what it found. Compare that with the scenario. A mismatch is either a bug, or a rule worth changing on purpose. If it is a rule, change the code, the tests, the README and the script together.
 
**Why it takes a while.** Google allows 60 read requests a minute, so every request goes through the same pacer as the sweep's check. Lines such as `(pausing 51 s to stay under Google's per-minute request limit)` are normal.
 
**What it doesn't cover**
 
- The wording of the emails. The tests check what each email says. Dr. approves the wording.
- That the Mail.Send permission is limited to the support mailbox (an Exchange policy, set by an admin).
- Task Scheduler, and a reminder missed because the command didn't run that day (see Known gaps).
 
The parts that need no Google or Microsoft (choosing groups, the guards, the stand-in spreadsheet, the checks themselves, run against fakes) are tested in `tests/test_check_reminders_live.py`, which runs with the rest of the suite.

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

**The sweep ran but nothing changed**
 
The command previews by default, and the report starts with `DRY RUN`. Run it again with `--live`.
 
**`Test changes can't be applied: the Ongoing tab has no 'Services Requested' column`**
 
The Ongoing tab needs a `Services Requested` header to apply test changes. It was probably renamed or removed. See [Tabs and columns](#tabs-and-columns). Provider and Psychometrist still apply.
 
**A test in a comment isn't being added or swapped**
 
Check these, in order:
 
1. The line is in `Add:` or `Change: X to Y` form, and the case's row has a Case ID.
2. The Comments cell is orange. If so, its note says why: an unknown name, a change that doesn't fit the case, or `not changed`.
3. The report doesn't say the Services tab couldn't be loaded.
4. The sweep was run with `--live`.
 
**`Services Requested  (not changed: ...)` in a Comments note**
 
The sweep deliberately left that case's tests alone. The reason follows `not changed`, and [Flags on the sheet](#flags-on-the-sheet) lists each one: a line in the cell that can't be read, original tests that weren't saved while the cell already shows changes, a case with no tests recorded, a saved original that belongs to a different case, or a save that failed.
 
**A test deleted from the comment is still in the cell**
 
That's deliberate. Tests are only ever added or swapped, so a deleted line keeps its test and flags the comment. Put the line back to keep it, or delete the test's line from the cell to remove it.

**I can't tell what a scheduled sweep did**
 
Read `logs/sweep.log`. Each run has a `Sweep started` line, the report, and a `Sweep finished` line. A start with no finish means it failed or is still running. Failures, with their traceback, are also in `logs/sweep_error.log`. In Task Scheduler, the task's Last Run Result shows a non-zero value when the run failed.
 
**`Could not read config/settings.json` in `sweep.log`**
 
The sweep was started somewhere other than the project root, so it couldn't find the settings. It still ran, but it logged to a `logs/` folder in the wrong place, and email alerts were off for that run. In a scheduled task, set "Start in" to the project root.
 
**`sweep.log` is growing quickly**
 
A case that stays flagged is logged in full, with the whole report, on every run until someone fixes it. The note on its Comments cell says how. See Known gaps.

**`Another live sweep is already running`**
 
A live sweep holds `sweep.lock`, in the folder the command was started from, and the message says which process, since when, and how old the lock is. If that run is still going, wait: this run did nothing, which is correct. If nothing is running, the run that held it was killed. The lock heals itself: once it is older than 15 minutes (`sweep_lock_stale_minutes`), the next live run takes it over. To use the sweep sooner, delete `sweep.lock`. The DOCX pipeline has its own, separate `scheduler.lock`, which never expires.
 
**`Took over a stale sweep lock` in `sweep.log`**
 
An earlier live run never gave its lock back, because it was killed (a power cut, a closed window, `taskkill`) or ran longer than the limit. An occasional one is fine. If it keeps happening, look at the `Sweep finished` lines before it. Runs that take close to the limit mean `sweep_lock_stale_minutes` is too low for the sheet.
 
**A scheduled sweep succeeds but nothing seems to happen**
 
A run that finds the lock held exits successfully by design, so Task Scheduler shows no failure. Look for `Sweep skipped (live)` in `logs/sweep.log`. Skips on every run mean a lock is stuck, or a run takes longer than the interval.

**`Case ID ... is already in Finished (row N) with different values for: ...`**
 
The case is in both tabs, and the Finished row doesn't match what the move would write. Nothing was changed. This usually means someone corrected the Finished row after an earlier move was interrupted, or the case was re-opened. If the Finished row is right, delete the case's row from Ongoing by hand. Otherwise fix the Finished row, then run the move again.
 
**`Case ID ... appears N times in Finished`**
 
The case has more than one row in Finished, probably from a move that was retried before this was fixed. Remove the extra rows, keeping the correct one, then run the move again.
 
**`Case ID ... was already in Finished (row N), so no row was added`**
 
This is a warning, not an error. An earlier move wrote the case to Finished and was interrupted before removing it from Ongoing. This run finished the job by removing it, and added no second row. If the message ends with `Filled in: ...`, those blank cells in the Finished row were filled from the Ongoing row.

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
- Fill the Services tab's `Name` column with the text a PDF prints after the code, copied from a real PDF, for the tests OA is likely to add. It becomes the description on lines the sweep adds.
- If you change how test changes are applied (the markers, the refusal rules, or the wording of a flag), update the tests, "How test changes are applied", "Flags on the sheet", and `docs/manual_check_apply_test_changes.md` together.
- Test editions change over time, and a mismatch is the first thing to check when a test isn't recognized. Trinity Tree gives the current edition of a test even when a referral lists an older one, so a PDF can show an older code than the test actually given. How newer editions should appear in the Services tab is still being decided.
- The sheet layout is a draft. Check [Tabs and columns](#tabs-and-columns) before renaming a header.
- A new command that logs should give `setup_logging` its own file names, as the sweep does. Two processes must not write to one rotating file.
- `app/run_lock.py` is a general lock with a stale limit, and the sweep is its first user. The DOCX pipeline's `scheduler.lock` could use it instead (see Known gaps), but `main.py` is shared, so propose that on its own.

**Known gaps**
 
- The DOCX pipeline and the sheet are not connected. Cases are added to the sheet by hand.
- The screenshot matcher prints its output to the console only, so a scheduled run of it would leave no record. Its output includes patient names, so what to log, and where, needs a decision.
- The sweep's lock covers two live sweeps only. It doesn't stop the screenshot matcher, or someone inserting, deleting, or sorting rows, while a sweep is running. A sweep reads the sheet once and then writes by row number, so a row shifted during the few seconds of a run can make it write to the wrong row.
- The lock is a file in the folder the command is started from, so two sweeps started from different folders don't see each other. A scheduled task should set "Start in" to the project root.
- Two runs that find the same stale lock at the same instant could both take it over. That needs a crash and two starts within a few milliseconds, and it can only cause the overlap the lock exists to prevent.
- The DOCX pipeline's `scheduler.lock` has no age limit. If a run is killed, every later run exits quietly until someone deletes the file. `app/run_lock.py` could replace it.
- A case that stays flagged is logged in full, with the whole report, on every run. At short intervals the log grows quickly (about 1 KB a run for two flagged cases). Logging the full report only when it changes would fix it.
- A flagged comment that is deleted entirely, when nothing has been applied to the case, keeps its orange fill and note.
- A PDF whose case ID can't be read is written to the sheet as a row with a blank Case ID.
- The screenshot matcher's own limits are listed in its section.
- A person's last name is matched on its last word, so a name such as `de la Cruz` is matched as `Cruz`. A suffix such as `Jr` would be taken as the last name.
- Someone listed in the `Contacts` tab by last name only (`Shelton`) matches any first name or initial typed with it.
- The sweep fills the email columns for a case only while its comment names the provider or psychometrist. Comments are edited and not rewritten, so this holds in practice, but a case whose comment never names one is not filled.
- Sending to the providers is built but switched off (`REMINDERS_GO_LIVE`) until Dr. has approved the wording of the six emails.
- A failed send is tried again only if the command is run again that day. The next day that reminder has passed, like a missed run (next bullet).
- The lock that stops two runs at once covers one computer. Run the reminders from one.
- If a row can't be written to `Reminders Sent`, the run stops and the row has to be added by
  hand, or the next run sends that reminder again.
- Microsoft accepting an email isn't proof it arrived. Bounces and undelivered emails aren't detected.
- The Microsoft client secret expires (date: TBA, see Microsoft Graph Setup), and Mail.Send isn't yet limited to the one mailbox.
- Each reminder goes out on its own day only. Runs are manual, so a day with no run loses that day's reminder, and the report doesn't yet say what was missed.
- The Day 3 reminder is never sent: it goes only if materials aren't uploaded, and nothing tracks that yet. It is listed in the report as held back. Day 1 is sent regardless.
- "The report is in" means the case is in Finished. A report that has been submitted but whose screenshot hasn't been matched yet looks unsubmitted, so that provider is still reminded.
- Only providers are reminded (phase 1). A case where a practicum student took part is reminded through the provider named on the case.
- An exam date that can't be read from `Date & Time` is reported on every run until it is fixed.
- **Failure alerts by email are off.** `health_check.email_notifications_enabled` is not `true` in `config/settings.json`, so a reminders run that fails (exit code 1) is only printed and logged, and nobody is emailed. The live check's scenario 11 reports this as a `[NOTE]`. Turn it on once Dr. has decided who is told.

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