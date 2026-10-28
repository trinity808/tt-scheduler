"""
Tests for app/services_cell.py: reading and writing the text of the
Services Requested cell.

Pure tests: text in, lines out, and back. No Google Sheets involved.

Run from the project root:
    python -m pytest tests/test_services_cell.py -v
"""

import pytest

from app.service_format import SERVICE_SEPARATOR
from app.services_cell import ServiceLine, parse_services_cell, render_services_cell


WISC_DESC = "Wechsler Intelligence Scale for Children - Ages 6 to 16 years"
AUTISM_DESC = "Appropriate testing to evaluate Autism Spectrum Disorders"

# A cell exactly as the extractor writes it.
PDF_CELL = (
    f"1. 96101-WISC-V - {WISC_DESC}\n"
    f"{SERVICE_SEPARATOR}\n"
    "2. 90791 - Psychiatric Exam\n"
    f"{SERVICE_SEPARATOR}\n"
    f"3. 96130-AUTISM - {AUTISM_DESC}"
)


def only(text):
    """Parse text that should hold exactly one readable line, and return it."""
    parsed = parse_services_cell(text)
    assert parsed.problems == []
    assert len(parsed.lines) == 1
    return parsed.lines[0]


# --- The shared format ----------------------------------------------


def test_separator_is_twenty_underscores():
    # This is what the extractor has always written, so cells it produced
    # before this module existed still read and rewrite the same way.
    assert SERVICE_SEPARATOR == "_" * 20


# --- Reading the PDF's own format -----------------------------------


def test_reads_the_cell_the_extractor_writes():
    parsed = parse_services_cell(PDF_CELL)
    assert parsed.problems == []
    assert parsed.codes() == ["96101-WISC-V", "90791", "96130-AUTISM"]
    assert [line.description for line in parsed.lines] == [
        WISC_DESC, "Psychiatric Exam", AUTISM_DESC,
    ]
    assert not parsed.has_markers()


def test_description_keeps_its_own_hyphens():
    assert only("1. 96101-WISC-V - Ages 6 to 16 years - Third Edition").description == (
        "Ages 6 to 16 years - Third Edition"
    )


def test_numbering_is_optional():
    line = only("90791 - Psychiatric Exam")
    assert (line.code, line.description) == ("90791", "Psychiatric Exam")


def test_a_code_with_no_description_is_a_line():
    line = only("4. 96130-CARS-2")
    assert (line.code, line.description, line.marker) == ("96130-CARS-2", "", "")


@pytest.mark.parametrize("blank", ["", None, "   ", "\n\n", f"{SERVICE_SEPARATOR}"])
def test_blank_cell_has_no_lines_and_no_problems(blank):
    parsed = parse_services_cell(blank)
    assert parsed.lines == []
    assert parsed.problems == []


# --- Markers --------------------------------------------------------


def test_added_marker():
    line = only("4. 96130-CARS-2 - Childhood Autism Rating Scale (added)")
    assert line.marker == "added"
    assert line.changed_from == ""
    assert line.description == "Childhood Autism Rating Scale"


def test_changed_marker_names_the_code_it_replaced():
    line = only("1. 96101-WAIS-IV - Wechsler Adult Intelligence Scale (changed from 96101-WISC-V)")
    assert line.marker == "changed"
    assert line.changed_from == "96101-WISC-V"
    assert line.description == "Wechsler Adult Intelligence Scale"


def test_markers_work_without_a_description():
    assert only("4. 96130-CARS-2 (added)").marker == "added"
    line = only("1. 96101-WAIS-IV (changed from 96101-WISC-V)")
    assert (line.marker, line.changed_from, line.description) == ("changed", "96101-WISC-V", "")


def test_a_description_ending_in_parentheses_is_not_a_marker():
    line = only("5. 99358-PSYCH - Review of Records (Psychological)")
    assert line.marker == ""
    assert line.description == "Review of Records (Psychological)"


def test_only_the_exact_markers_count():
    line = only("5. 96130-AUTISM - Autism testing (changed from wisc)")
    assert line.marker == ""
    assert line.description == "Autism testing (changed from wisc)"


def test_has_markers():
    parsed = parse_services_cell(PDF_CELL + f"\n{SERVICE_SEPARATOR}\n4. 96130-CARS-2 (added)")
    assert parsed.has_markers()


# --- Layout is forgiven ---------------------------------------------


def test_runs_of_whitespace_within_a_line_collapse():
    line = only("1. 96101-WISC-V -   Wechsler\tIntelligence   Scale for Children")
    assert line.description == "Wechsler Intelligence Scale for Children"


def test_lines_without_separators_are_still_separate_entries():
    # OA typing a new test under an existing one, with no separator row.
    parsed = parse_services_cell("1. 90791 - Psychiatric Exam\n2. 96130-CARS-2 - Childhood Autism Rating Scale")
    assert parsed.problems == []
    assert parsed.codes() == ["90791", "96130-CARS-2"]


def test_extra_separators_and_other_widths_are_ignored():
    text = f"____\n1. 90791 - Psychiatric Exam\n{'_' * 30}\n{SERVICE_SEPARATOR}\n2. 96130-CARS-2\n_____"
    parsed = parse_services_cell(text)
    assert parsed.codes() == ["90791", "96130-CARS-2"]
    assert parsed.problems == []


def test_two_underscores_are_not_a_separator():
    parsed = parse_services_cell("1. 90791 - Psychiatric Exam\n__\n2. 96130-CARS-2")
    assert parsed.codes() == ["90791", "96130-CARS-2"]
    assert len(parsed.problems) == 1
    assert "__" in parsed.problems[0]


# --- What can't be read is reported, not guessed --------------------


def test_unreadable_entries_are_reported_and_the_rest_are_kept():
    text = f"1. 90791 - Psychiatric Exam\n{SERVICE_SEPARATOR}\nadd wms\n{SERVICE_SEPARATOR}\n3. 96130-CARS-2"
    parsed = parse_services_cell(text)
    assert parsed.codes() == ["90791", "96130-CARS-2"]
    assert len(parsed.problems) == 1
    assert "Entry 2" in parsed.problems[0]
    assert "add wms" in parsed.problems[0]


def test_a_loose_note_is_reported_and_never_attached_to_the_line_above():
    parsed = parse_services_cell("1. 90791 - Psychiatric Exam\nadd wms")
    assert parsed.codes() == ["90791"]
    assert parsed.lines[0].description == "Psychiatric Exam"
    assert len(parsed.problems) == 1
    assert "add wms" in parsed.problems[0]


def test_a_wrapped_description_is_reported_not_merged():
    parsed = parse_services_cell("1. 96101-WISC-V - Wechsler Intelligence\nScale for Children")
    assert parsed.codes() == ["96101-WISC-V"]
    assert len(parsed.problems) == 1


@pytest.mark.parametrize(
    "text",
    [
        "Autism testing 96130",          # code not at the start
        "wms",                           # a name, not a code
        "1. wms - Wechsler Memory Scale",
        "9613 - four digits only",
        "96101-WISC-V Wechsler",         # no dash between code and description
        "96101-WISC-V -",                # dash with nothing after it
        "(added)",
    ],
)
def test_text_that_does_not_start_with_a_code_is_a_problem(text):
    parsed = parse_services_cell(text)
    assert parsed.lines == []
    assert len(parsed.problems) == 1


# --- Writing --------------------------------------------------------


def test_renders_numbered_lines_with_a_separator_between_and_none_at_the_end():
    lines = [
        ServiceLine("96101-WISC-V", WISC_DESC),
        ServiceLine("90791", "Psychiatric Exam"),
    ]
    assert render_services_cell(lines) == (
        f"1. 96101-WISC-V - {WISC_DESC}\n{SERVICE_SEPARATOR}\n2. 90791 - Psychiatric Exam"
    )


def test_a_line_with_no_description_shows_just_its_code():
    assert render_services_cell([ServiceLine("96130-CARS-2")]) == "1. 96130-CARS-2"


def test_markers_are_written_at_the_end_of_the_line():
    text = render_services_cell([
        ServiceLine("96101-WAIS-IV", "Wechsler Adult Intelligence Scale", "changed", "96101-WISC-V"),
        ServiceLine("96130-CARS-2", "", "added"),
    ])
    assert text == (
        "1. 96101-WAIS-IV - Wechsler Adult Intelligence Scale (changed from 96101-WISC-V)"
        f"\n{SERVICE_SEPARATOR}\n"
        "2. 96130-CARS-2 (added)"
    )


def test_an_empty_list_renders_as_an_empty_cell():
    assert render_services_cell([]) == ""


def test_lines_are_always_renumbered_from_one():
    text = render_services_cell([ServiceLine("90791", "Psychiatric Exam")] * 3)
    assert [chunk.split(".")[0] for chunk in text.split(f"\n{SERVICE_SEPARATOR}\n")] == ["1", "2", "3"]


# --- Round trips ----------------------------------------------------


def test_the_extractors_text_comes_back_unchanged():
    assert render_services_cell(parse_services_cell(PDF_CELL).lines) == PDF_CELL


def test_written_lines_read_back_the_same():
    lines = [
        ServiceLine("96101-WAIS-IV", "Wechsler Adult Intelligence Scale", "changed", "96101-WISC-V"),
        ServiceLine("90791", "Psychiatric Exam"),
        ServiceLine("96130-CARS-2", "", "added"),
        ServiceLine("99358-PSYCH", "Review of Records (Psychological)", "added"),
    ]
    assert parse_services_cell(render_services_cell(lines)).lines == lines


# --- ServiceLine ----------------------------------------------------


def test_a_line_tidies_its_own_text():
    line = ServiceLine(" 90791 ", "  Psychiatric\n Exam ")
    assert (line.code, line.description) == ("90791", "Psychiatric Exam")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"marker": "removed"},                                # not a known marker
        {"marker": "changed"},                                # changed with no source
        {"marker": "added", "changed_from": "96101-WISC-V"},  # source on a non-change
        {"changed_from": "96101-WISC-V"},                     # source with no marker
    ],
)
def test_an_inconsistent_marker_is_rejected(kwargs):
    with pytest.raises(ValueError):
        ServiceLine("96130-CARS-2", **kwargs)