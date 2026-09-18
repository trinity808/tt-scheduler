# Trinity Dropbox Scheduler

Automated Python scheduler for Trinity Tree PDF appointment packets. The app reads incoming PDFs from Dropbox, extracts appointment and claimant details, generates local report DOCX files from Word templates, updates the correct daily schedule DOCX, uploads the schedule back to Dropbox, and moves processed PDFs out of the incoming folder.

## What It Does

- Connects to Dropbox using long-lived refresh-token authentication.
- Finds PDF files in a configured Dropbox input folder.
- Downloads each PDF into a local working folder.
- Extracts key scheduling data from the first pages of each PDF:
  - case ID
  - authorization number
  - patient name
  - date of birth and age
  - claimant phone number
  - assigned doctor
  - examiner initials
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

## Project Structure

```text
.
├── apis/
│   └── dropbox_api.py          # Dropbox auth and file operations
├── app/
│   ├── config_loader.py        # Loads config/settings.json
│   ├── file_utils.py           # Reserved helper module
│   ├── health_monitor.py       # Logging, startup checks, email alerts
│   ├── pdf_extractor.py        # PDF parsing and data extraction
│   ├── schedule_generator.py   # Daily schedule DOCX creation/update
│   ├── state_manager.py        # Optional processed-file ID tracking helpers
│   └── template_filler.py      # Report template placeholder replacement
├── config/
│   └── settings.json           # Runtime folders and scheduler settings
├── docs/
│   └── trinity_scheduler_process_guide.pdf
├── templates/
│   ├── adult_report_template.docx
│   └── child_report_template.docx
├── testing pdfs/               # Sample PDFs for manual testing
├── .env.example                # Environment variable template
├── get_refresh_token.py        # Dropbox refresh-token setup helper
├── main.py                     # Main scheduler entry point
├── requirements.txt            # Python dependencies
└── run_scheduler.sh            # macOS/local shell runner
```

Runtime folders such as `data/`, `logs/`, `venv/`, `__pycache__/`, and `.env` should stay out of GitHub.

## Requirements

- Python 3.10 or newer
- Dropbox app credentials
- A Dropbox refresh token with access to the configured folders
- Microsoft Word, LibreOffice, or another DOCX-compatible editor for viewing generated documents
- macOS if using the included `run_scheduler.sh` and launchd-style local scheduling

Python packages:

```text
python-dotenv
dropbox
pdfplumber
python-docx
python-dateutil
```

## Setup

Clone or download the project, then create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create your local environment file:

```bash
cp .env.example .env
```

Fill in `.env`:

```env
DROPBOX_APP_KEY=
DROPBOX_APP_SECRET=
DROPBOX_REFRESH_TOKEN=

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_EMAIL=
ALERT_TO_EMAIL=
```

Email settings are only required when `email_notifications_enabled` is set to `true` in `config/settings.json`.

## Dropbox App Setup

1. Create a Dropbox app in the Dropbox App Console.
2. Copy the app key and app secret into `.env`.
3. Make sure the app has permission to read, write, upload, and move files in the Dropbox folders used by this scheduler.
4. Generate a refresh token:

```bash
source venv/bin/activate
python get_refresh_token.py
```

5. Open the authorization URL printed by the script.
6. Approve access in Dropbox.
7. Paste the authorization code back into the terminal.
8. Copy the printed `DROPBOX_REFRESH_TOKEN=...` value into `.env`.

## Configuration

Main runtime settings live in `config/settings.json`.

```json
{
  "base_folder": "data",
  "dropbox_input_folder": "/incoming_pdfs",
  "dropbox_processed_pdf_folder": "/processed_pdfs",
  "dropbox_schedule_folder": "/schedules",
  "adult_age_cutoff": 18,
  "max_files_per_run": 200,
  "adult_template_path": "templates/adult_report_template.docx",
  "child_template_path": "templates/child_report_template.docx",
  "generated_docs_folder": "/Users/anand/Desktop/reports",
  "health_check": {
    "log_folder": "logs",
    "email_notifications_enabled": true,
    "email_subject_prefix": "[Trinity Scheduler]"
  }
}
```

Important settings:

- `base_folder`: local working folder for downloaded PDFs, local schedules, and processed PDFs.
- `dropbox_input_folder`: Dropbox folder where new PDF packets are placed.
- `dropbox_processed_pdf_folder`: Dropbox folder where finished PDFs are moved.
- `dropbox_schedule_folder`: Dropbox folder where generated schedule DOCX files are uploaded.
- `adult_age_cutoff`: age used to choose adult vs child report template.
- `max_files_per_run`: maximum number of PDFs to process in one scheduler run.
- `adult_template_path`: path to the adult DOCX template.
- `child_template_path`: path to the child DOCX template.
- `generated_docs_folder`: local folder for filled report documents.
- `health_check.log_folder`: folder for scheduler logs.
- `health_check.email_notifications_enabled`: enables or disables alert emails.
- `health_check.email_subject_prefix`: prefix added to error email subjects.

For portability, change absolute paths such as `/Users/anand/Desktop/reports` to a path that exists on the machine running the scheduler.

## Template Placeholders

The report templates are DOCX files in `templates/`. The scheduler replaces these placeholders wherever they appear in paragraphs, tables, headers, or footers:

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

Activate the virtual environment and run:

```bash
source venv/bin/activate
python main.py
```

On startup, the scheduler checks required config values, template paths, generated document folder access, and email alert configuration. If a required item is missing, the run fails before processing PDFs.

## Running With the Shell Script

`run_scheduler.sh` is useful for local scheduled runs:

```bash
chmod +x run_scheduler.sh
./run_scheduler.sh
```

Before using it on another machine, edit the `cd` line so it points to that machine's project folder:

```bash
cd "/path/to/trinity-dropbox-scheduler"
```

The script activates `venv`, runs `main.py`, appends normal output to `logs/scheduler.log`, and appends errors to `logs/scheduler_error.log`.

## Scheduling on macOS

This project includes a local shell runner and a Word guide named `scheduler local enabler steps.docx`. A typical macOS launchd setup looks like this:

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

## Processing Flow

1. `main.py` loads `config/settings.json`.
2. Logging is configured by `app/health_monitor.py`.
3. A `scheduler.lock` file is created to prevent overlapping runs.
4. Dropbox PDFs are listed from `dropbox_input_folder`.
5. Each PDF is downloaded into `data/incoming_pdfs/`.
6. `app/pdf_extractor.py` extracts scheduling data.
7. `app/template_filler.py` fills the adult or child report template.
8. Existing same-day schedule DOCX is downloaded from Dropbox if present.
9. `app/schedule_generator.py` creates or updates the local schedule DOCX.
10. The updated schedule is uploaded to `dropbox_schedule_folder`.
11. The original PDF is moved to `dropbox_processed_pdf_folder`.
12. Successes and failures are written to logs.
13. The lock file is removed when the run exits.

## Output Files

Generated report documents are saved to `generated_docs_folder`. The default current config writes them to:

```text
/Users/anand/Desktop/reports
```

Schedule files are created locally under:

```text
data/schedules/
```

and uploaded to the configured Dropbox schedule folder.

Processed PDFs are moved in Dropbox to:

```text
/processed_pdfs
```

## Logs and Alerts

Logs are written to:

```text
logs/scheduler.log
logs/scheduler_error.log
logs/launchd.log
logs/launchd_error.log
```

The Python logger uses rotating log files with a 2 MB limit and 5 backups.

When email alerts are enabled, failures can send SMTP email using the values in `.env`. For Gmail, use an app password rather than your normal account password.

## Manual Testing

Sample PDFs are stored in `testing pdfs/`. To test end to end:

1. Put one sample PDF in the configured Dropbox input folder.
2. Run `python main.py`.
3. Confirm the PDF was moved to the processed Dropbox folder.
4. Confirm the matching schedule DOCX was uploaded to the Dropbox schedules folder.
5. Confirm the local report DOCX was created in `generated_docs_folder`.
6. Check `logs/scheduler.log` for extracted values and final counts.

To test extraction only, use Python directly:

```bash
python - <<'PY'
from app.pdf_extractor import extract_schedule_data

data = extract_schedule_data("testing pdfs/Test1-may4_2026.pdf")
for key, value in data.items():
    print(f"{key}: {value}")
PY
```

## Troubleshooting

`DROPBOX_APP_KEY not found in .env`

Make sure `.env` exists in the project root and contains Dropbox credentials.

`DROPBOX_REFRESH_TOKEN not found in .env`

Run `python get_refresh_token.py`, approve the Dropbox app, and paste the generated refresh token into `.env`.

`Startup health check failed`

Check `config/settings.json` for missing folder paths or template paths. Make sure the generated docs folder can be created.

`Template not found`

Confirm that `adult_template_path` and `child_template_path` point to existing DOCX files.

No PDFs are processed

Confirm that `dropbox_input_folder` is correct and contains files ending in `.pdf`.

Wrong adult or child template selected

Check that the PDF contains a readable DOB and appointment date. The app compares the claimant age on the schedule date to `adult_age_cutoff`.

Email notifications do not send

Confirm `email_notifications_enabled` is `true`, SMTP values are present in `.env`, and the SMTP account allows app-password or SMTP access.

## Security Notes Before Publishing to GitHub

Do not commit secrets or local runtime files. In particular:

- Keep `.env` out of Git.
- Remove or ignore `.history/` if it contains old `.env` snapshots.
- Keep `venv/`, `logs/`, `data/`, `__pycache__/`, `.DS_Store`, and generated DOCX outputs out of Git.
- Review templates and testing PDFs before publishing if they may contain private claimant data.
- Rotate Dropbox and SMTP credentials if they were ever committed or shared.

The current `.gitignore` already excludes several runtime paths, but check the repository carefully before the first GitHub push.

## Maintenance Notes

- Update `requirements.txt` whenever dependencies change.
- Keep `run_scheduler.sh` paths in sync with the machine where the scheduler runs.
- If the PDF format changes, update extraction logic in `app/pdf_extractor.py`.
- If the schedule table changes, update headers and row generation in `app/schedule_generator.py`.
- If template placeholders change, update replacements in `app/template_filler.py`.

## License

No license file is currently included. Add a license before publishing publicly if others should be allowed to use, modify, or distribute this project.
