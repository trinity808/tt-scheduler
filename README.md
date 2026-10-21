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

