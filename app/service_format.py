"""
Shared format rules for service lines.

The service code pattern is used by both pdf_extractor.py (reading the
authorization PDF) and comment_parser.py (reading OA's comments), so
both sides always agree on what a code looks like. Change it here, not
in either file.
"""

# Five digits, then any run of letters, digits, or hyphens, with no
# spaces. Matches "90791", "96130-AUTISM", "96101-WISC-V",
# "96101-WIAT-III".
SERVICE_CODE_PATTERN = r"\d{5}[\w\-]*"