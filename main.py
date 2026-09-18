import os
import sys

from app.config_loader import load_config
from app.health_monitor import (
    get_logger,
    notify_error,
    run_startup_health_check,
    setup_logging
)
from app.template_filler import fill_report_template
from apis.dropbox_api import (
    list_pdfs,
    download_file,
    download_if_exists,
    upload_file,
    move_file
)
from app.pdf_extractor import extract_schedule_data
from app.schedule_generator import (
    create_or_update_schedule_doc,
    schedule_file_name
)


LOCK_FILE = "scheduler.lock"
logger = get_logger()


def create_lock_or_exit():
    if os.path.exists(LOCK_FILE):
        logger.warning("Another scheduler run is already active. Exiting.")
        sys.exit(0)

    with open(LOCK_FILE, "w") as f:
        f.write("running")


def remove_lock():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)


def main(config):
    run_startup_health_check(config)

    base_folder = config["base_folder"]
    input_folder = config["dropbox_input_folder"]
    processed_pdf_folder = config["dropbox_processed_pdf_folder"]
    schedule_folder = config["dropbox_schedule_folder"]

    adult_age_cutoff = config.get("adult_age_cutoff", 18)
    max_files_per_run = config.get("max_files_per_run", 200)

    adult_template_path = config.get(
        "adult_template_path",
        "templates/adult_report_template.docx"
    )

    child_template_path = config.get(
        "child_template_path",
        "templates/child_report_template.docx"
    )

    generated_docs_folder = config.get(
        "generated_docs_folder",
        "data/generated_docs"
    )

    local_incoming_folder = os.path.join(base_folder, "incoming_pdfs")
    local_schedules_folder = os.path.join(base_folder, "schedules")
    local_processed_folder = os.path.join(base_folder, "processed_pdfs")

    os.makedirs(local_incoming_folder, exist_ok=True)
    os.makedirs(local_schedules_folder, exist_ok=True)
    os.makedirs(local_processed_folder, exist_ok=True)
    os.makedirs(generated_docs_folder, exist_ok=True)

    pdf_files = list_pdfs(input_folder)

    logger.info("Found %s PDF files in Dropbox input folder.", len(pdf_files))

    if not pdf_files:
        logger.info("No PDFs found.")
        return

    processed_count = 0
    failed_count = 0

    for pdf in pdf_files[:max_files_per_run]:
        logger.info("-" * 50)
        logger.info("Processing PDF: %s", pdf.name)

        local_pdf_path = os.path.join(local_incoming_folder, pdf.name)

        try:
            # 1. Download incoming PDF from Dropbox
            download_file(pdf.path_display, local_pdf_path)

            # 2. Extract data from PDF
            extracted = extract_schedule_data(
                local_pdf_path,
                adult_age_cutoff
            )

            logger.info("Extracted:")
            logger.info("Case ID: %s", extracted.get("case_id"))
            logger.info("Patient: %s", extracted.get("patient_name"))
            logger.info("Doctor: %s", extracted.get("doctor_assigned"))
            logger.info("Examiner: %s", extracted.get("examiner"))
            logger.info("Phone: %s", extracted.get("phone"))
            logger.info("DOB: %s", extracted.get("dob"))
            logger.info("Schedule Date: %s", extracted.get("schedule_date"))

            # 3. Fill local report template
            if extracted.get("adult") == "X":
                selected_template_path = adult_template_path
                logger.info("Using adult report template")
            else:
                selected_template_path = child_template_path
                logger.info("Using child report template")

            local_report_path = fill_report_template(
                extracted,
                selected_template_path,
                generated_docs_folder
            )
            logger.info("Local report ready: %s", local_report_path)

            # 4. Download existing same-day schedule from Dropbox if present
            schedule_name = schedule_file_name(
                extracted["schedule_file_date"]
            )

            local_schedule_path = os.path.join(
                local_schedules_folder,
                schedule_name
            )

            dropbox_schedule_path = (
                f"{schedule_folder.rstrip('/')}/{schedule_name}"
            )

            download_if_exists(
                dropbox_schedule_path,
                local_schedule_path
            )

            # 5. Create or update local schedule DOCX
            schedule_path = create_or_update_schedule_doc(
                extracted,
                local_schedules_folder
            )

            # 6. Upload updated schedule DOCX to Dropbox
            uploaded_schedule_path = upload_file(
                schedule_path,
                schedule_folder
            )

            # 7. Move original PDF to processed folder in Dropbox
            processed_pdf_dropbox_path = (
                f"{processed_pdf_folder.rstrip('/')}/{pdf.name}"
            )

            moved_pdf_path = move_file(
                pdf.path_display,
                processed_pdf_dropbox_path
            )

            logger.info("Uploaded schedule: %s", uploaded_schedule_path)
            logger.info("Moved original PDF to: %s", moved_pdf_path)

            processed_count += 1

        except Exception:
            failed_count += 1
            logger.exception("Error processing PDF: %s", pdf.name)
            notify_error(
                f"PDF processing failed: {pdf.name}",
                (
                    "A PDF failed while the scheduler was running in the "
                    f"background.\n\nPDF: {pdf.name}"
                ),
                config=config,
                exc_info=sys.exc_info()
            )

    logger.info("Run complete.")
    logger.info("PDFs processed successfully: %s", processed_count)
    logger.info("PDFs failed: %s", failed_count)


if __name__ == "__main__":
    config = None
    try:
        config = load_config()
        setup_logging(config)
        create_lock_or_exit()
        main(config)
    except Exception:
        if not logger.handlers:
            setup_logging(config)

        logger.exception("Fatal scheduler failure.")
        notify_error(
            "Fatal scheduler failure",
            "The scheduler stopped before it could finish the background run.",
            config=config,
            exc_info=sys.exc_info()
        )
        raise
    finally:
        remove_lock()
