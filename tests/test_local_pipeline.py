"""
Local-only smoke test for the Trinity scheduler pipeline.

Runs the same three steps main.py runs per PDF, but entirely locally:
extraction -> template fill -> schedule build. No Dropbox calls at all.
"""

import sys

from app.pdf_extractor import extract_schedule_data
from app.template_filler import fill_report_template
from app.schedule_generator import create_or_update_schedule_doc

ADULT_AGE_CUTOFF = 18
ADULT_TEMPLATE = "templates/adult_report_template.docx"
CHILD_TEMPLATE = "templates/child_report_template.docx"
GENERATED_DOCS_FOLDER = "data/generated_docs"
SCHEDULES_FOLDER = "data/schedules"


def run(pdf_path):
    print(f"\n=== Extracting: {pdf_path} ===")
    extracted = extract_schedule_data(pdf_path, ADULT_AGE_CUTOFF)

    for key, value in extracted.items():
        print(f"{key}: {value}")

    template_path = ADULT_TEMPLATE if extracted.get("adult") == "X" else CHILD_TEMPLATE
    print(f"\nUsing template: {template_path}")

    report_path = fill_report_template(extracted, template_path, GENERATED_DOCS_FOLDER)
    print(f"Report written to: {report_path}")

    schedule_path = create_or_update_schedule_doc(extracted, SCHEDULES_FOLDER)
    print(f"Schedule written to: {schedule_path}")


if __name__ == "__main__":
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else "testing pdfs/Test1-may4_2026.pdf"
    run(pdf_path)