"""
Tests for app/service_lookup.py.

Pure tests: rows in, lookup out. No Google Sheets involved. Most tests
build a small table for one rule; the last group runs against a copy
of the real Services tab, so its known problems (the PSYCH conflict,
rows without codes) are checked against actual data.

Run from the project root:
    python -m pytest tests/test_service_lookup.py -v
"""

import pytest

from app.service_lookup import build_lookup, normalize_key


HEADERS = ["Code", "Aliases", "Name"]


def table(*rows):
    return [HEADERS, *rows]


# Copy of the Services tab as of this writing. A frozen snapshot for
# testing: editing the real tab never breaks these tests, but refresh
# this copy now and then so the tests stay close to reality.
REAL_TABLE = """\
Code\tAliases\tName
90791\tPsychiatric Exam\tPsychiatric Exam
96101-WISC-V\tWISC\tWechsler Intelligence Scale for Children
96101-WIAT-III\tWIAT\tWechsler Individual Achievement Scale - Third Edition
96101-WAIS-IV\tWAIS\tWechsler Adult Intelligence Scale
96101-WMS-IV\tWMS\tWechsler Memory Scale - Fourth Edition
96101-WPPSI-IV\tWPPSI\tWechsler Preschool and Primary Scale of Intelligence
96130-CARS-2\tCARS\tChildhood Autism Rating Scale
96101-VABS\t\tVineland Adaptive Behavior Scales - Third Edition
96101-Conners\t\tConners - Fourth Edition
96130-CTONI2\tCTONI\tComprehensive Test of Nonverbal Intelligence
96130-AUTISM\tAutism\tAppropriate testing to evaluate Autism Spectrum Disorders
96101-WJ-IV\tWJ\tWoodcock-Johnson Tests of Achievement IV
96101-CRS-R\t\tConner's Rating Scales - Revised
99075-MTNL\t\tMedical Source Statement
99358-PSYCH\t\tReview of Records (Psychological)
96101-TONI-3 \t\tTest of Non Verbal Intelligence - Third Edition
"""


def real_table():
    return [line.split("\t") for line in REAL_TABLE.strip("\n").split("\n")]


# --- Normalizing ----------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("wisc-v", "WISCV"),
        ("  WISC V ", "WISCV"),
        ("96101-WISC-V", "96101WISCV"),
        ("Rey-15", "REY15"),
        ("Psychiatric Exam", "PSYCHIATRICEXAM"),
    ],
)
def test_normalize_key(text, expected):
    assert normalize_key(text) == expected


# --- What each row answers to ---------------------------------------


def test_full_code_resolves():
    lookup = build_lookup(table(["96101-WISC-V", "", ""]))
    assert lookup.resolve("96101-WISC-V") == "96101-WISC-V"


@pytest.mark.parametrize("typed", ["WISC-V", "wisc-v", "wisc v", "wiscv"])
def test_abbreviation_derived_from_code_resolves(typed):
    lookup = build_lookup(table(["96101-WISC-V", "", ""]))
    assert lookup.resolve(typed) == "96101-WISC-V"


def test_alias_resolves():
    lookup = build_lookup(table(["96101-WISC-V", "WISC", ""]))
    assert lookup.resolve("wisc") == "96101-WISC-V"


def test_aliases_split_on_commas():
    lookup = build_lookup(table(["96101-WISC-V", "WISC, Wechsler Kids", ""]))
    assert lookup.resolve("WISC") == "96101-WISC-V"
    assert lookup.resolve("wechsler kids") == "96101-WISC-V"


def test_code_without_suffix_resolves_by_code_and_alias():
    lookup = build_lookup(table(["90791", "Psychiatric Exam", ""]))
    assert lookup.resolve("90791") == "90791"
    assert lookup.resolve("psychiatric exam") == "90791"


def test_surrounding_whitespace_in_code_is_ignored():
    lookup = build_lookup(table(["96101-TONI-3 ", "", ""]))
    assert lookup.resolve("TONI-3") == "96101-TONI-3"


def test_columns_found_by_name_not_position():
    lookup = build_lookup([
        ["Name", "Aliases", "Code"],
        ["Wechsler Intelligence Scale for Children", "WISC", "96101-WISC-V"],
    ])
    assert lookup.resolve("WISC") == "96101-WISC-V"


@pytest.mark.parametrize("missing", ["Code", "Aliases"])
def test_missing_required_column_raises(missing):
    headers = [h for h in HEADERS if h != missing]
    with pytest.raises(ValueError):
        build_lookup([headers])


# --- Bad rows don't break the table ---------------------------------


def test_row_with_invalid_code_is_skipped_and_reported():
    lookup = build_lookup(table(
        ["Beery VMI", "", "Beery-Buktenica"],
        ["96101-WISC-V", "WISC", ""],
    ))
    assert lookup.resolve("Beery VMI") is None
    assert lookup.resolve("WISC") == "96101-WISC-V"
    assert len(lookup.problems) == 1
    assert "Row 2" in lookup.problems[0]
    assert "Beery VMI" in lookup.problems[0]


def test_blank_rows_are_ignored_without_a_report():
    lookup = build_lookup(table(["", "", ""], ["96101-WISC-V", "", ""]))
    assert lookup.problems == []


def test_conflicting_key_is_unusable_and_reported():
    lookup = build_lookup(table(
        ["90791", "Psych, Psychiatric Exam", ""],
        ["99358-PSYCH", "", ""],
    ))
    assert lookup.resolve("Psych") is None
    assert lookup.is_ambiguous("psych")
    assert len(lookup.problems) == 1
    assert "90791" in lookup.problems[0]
    assert "99358-PSYCH" in lookup.problems[0]
    # Every other key still works.
    assert lookup.resolve("Psychiatric Exam") == "90791"
    assert lookup.resolve("99358-PSYCH") == "99358-PSYCH"


def test_same_code_reached_twice_is_not_a_conflict():
    # AUTISM is both derived from the code and listed as an alias.
    lookup = build_lookup(table(["96130-AUTISM", "Autism", ""]))
    assert lookup.resolve("autism") == "96130-AUTISM"
    assert lookup.problems == []


# --- Unknown text ---------------------------------------------------


def test_unknown_text_is_not_resolved_and_not_ambiguous():
    lookup = build_lookup(table(["96101-WISC-V", "WISC", ""]))
    assert lookup.resolve("XYZ") is None
    assert not lookup.is_ambiguous("XYZ")


def test_typo_gets_a_suggestion_as_written_in_the_table():
    lookup = build_lookup(table(["96101-WISC-V", "WISC", ""]))
    assert lookup.suggest("WSIC") == "WISC"


def test_unrelated_text_gets_no_suggestion():
    lookup = build_lookup(table(["96101-WISC-V", "WISC", ""]))
    assert lookup.suggest("XYZ") is None


# --- The real Services tab ------------------------------------------


@pytest.mark.parametrize(
    "typed, code",
    [
        ("wms", "96101-WMS-IV"),
        ("WIAT", "96101-WIAT-III"),
        ("vabs", "96101-VABS"),
        ("Conners", "96101-Conners"),
        ("CRS-R", "96101-CRS-R"),
        ("ctoni", "96130-CTONI2"),
        ("CTONI2", "96130-CTONI2"),
        ("cars", "96130-CARS-2"),
        ("CARS-2", "96130-CARS-2"),
        ("autism", "96130-AUTISM"),
        ("WJ", "96101-WJ-IV"),
        ("TONI-3", "96101-TONI-3"),
        ("MTNL", "99075-MTNL"),
        ("90791", "90791"),
        ("Psychiatric Exam", "90791"),
    ],
)
def test_real_table_resolves(typed, code):
    assert build_lookup(real_table()).resolve(typed) == code


def test_real_table_has_no_problems():
    assert build_lookup(real_table()).problems == []


def test_real_table_psych_means_review_of_records():
    # With the "Psych" alias gone from 90791, "psych" only matches
    # 99358-PSYCH's own abbreviation. OA never notes the psychiatric
    # exam, so this is intended.
    lookup = build_lookup(real_table())
    assert not lookup.is_ambiguous("psych")
    assert lookup.resolve("psych") == "99358-PSYCH"