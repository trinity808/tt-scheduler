"""
Tests for app/comment_parser.py.

Pure parser tests: text in, ParsedComment out. Service names are
resolved through a small lookup built in this file, so no Google
Sheet is involved.

Run from the project root:
    python -m pytest tests/test_comment_parser.py -v
"""

import pytest

from app.comment_parser import ServiceChange, parse_comment
from app.service_lookup import build_lookup


# A small stand-in for the Services tab. It deliberately gives "Psych"
# to two codes, so "psych" is ambiguous and conflict handling can be
# tested. (The real tab no longer has this conflict.)
LOOKUP = build_lookup([
    ["Code", "Aliases"],
    ["96101-WISC-V", "WISC"],
    ["96101-WIAT-III", "WIAT"],
    ["96101-WAIS-IV", "WAIS"],
    ["96101-WPPSI-IV", "WPPSI"],
    ["96130-CARS-2", "CARS"],
    ["96130-CTONI2", "CTONI"],
    ["90791", "Psych, Psychiatric Exam"],
    ["99358-PSYCH", ""],
])

WISC = "96101-WISC-V"
WIAT = "96101-WIAT-III"
WAIS = "96101-WAIS-IV"
WPPSI = "96101-WPPSI-IV"
CARS = "96130-CARS-2"
CTONI = "96130-CTONI2"


def parse(text):
    return parse_comment(text, LOOKUP)


def add(code):
    return ServiceChange("add", code)


def change(old, new):
    return ServiceChange("change", new, replaces=old)


def assert_clean(parsed):
    assert parsed.unrecognized_lines == []


def assert_single_flag(parsed, expected_fragment):
    assert len(parsed.unrecognized_lines) == 1
    assert expected_fragment in parsed.unrecognized_lines[0]


# --- Basics --------------------------------------------------------


def test_provider_and_psychometrist():
    parsed = parse("Provider: Dr. Shelton\nPsychometrist: J. Smith")
    assert parsed.provider == "Dr. Shelton"
    assert parsed.psychometrist == "J. Smith"
    assert parsed.service_changes == []
    assert_clean(parsed)


@pytest.mark.parametrize("empty_input", ["", None, "   ", "\n\n"])
def test_empty_comment_returns_defaults(empty_input):
    parsed = parse(empty_input)
    assert parsed.provider is None
    assert parsed.psychometrist is None
    assert parsed.service_changes == []
    assert_clean(parsed)


@pytest.mark.parametrize("key", ["provider", "PROVIDER", "Provider", "pRoViDeR"])
def test_keys_are_case_insensitive(key):
    parsed = parse(f"{key}: Dr. Shelton")
    assert parsed.provider == "Dr. Shelton"
    assert_clean(parsed)


def test_surrounding_whitespace_and_blank_lines_ignored():
    parsed = parse("\n   Provider:   Dr. Shelton   \n\n\n  Psychometrist: J. Smith  \n")
    assert parsed.provider == "Dr. Shelton"
    assert parsed.psychometrist == "J. Smith"
    assert_clean(parsed)


# --- Line breaks and semicolons ------------------------------------


def test_semicolons_separate_statements():
    parsed = parse("Provider: Dr. Shelton; Psychometrist: J. Smith")
    assert parsed.provider == "Dr. Shelton"
    assert parsed.psychometrist == "J. Smith"
    assert_clean(parsed)


def test_line_breaks_and_semicolons_can_be_mixed():
    parsed = parse("Provider: Dr. Shelton\nAdd: wiat; Change: wisc to wais")
    assert parsed.provider == "Dr. Shelton"
    assert parsed.service_changes == [add(WIAT), change(WISC, WAIS)]
    assert_clean(parsed)


def test_statement_after_semicolon_needs_its_own_key():
    parsed = parse("Change: wisc to wais; wiat to wppsi")
    assert parsed.service_changes == [change(WISC, WAIS)]
    assert_single_flag(parsed, "not in 'Field: value' format")


# --- Add -----------------------------------------------------------


def test_add_single_test():
    assert parse("Add: wiat").service_changes == [add(WIAT)]


def test_add_comma_separated_tests_in_order():
    parsed = parse("Add: wiat, cars, wppsi")
    assert parsed.service_changes == [add(WIAT), add(CARS), add(WPPSI)]
    assert_clean(parsed)


@pytest.mark.parametrize("typed", ["wisc", "WISC-V", "wisc v", "96101-WISC-V"])
def test_any_known_name_for_a_test_resolves(typed):
    assert parse(f"Add: {typed}").service_changes == [add(WISC)]


def test_unknown_item_is_flagged_without_blocking_the_rest():
    parsed = parse("Add: wait, cars, wppsi")
    assert parsed.service_changes == [add(CARS), add(WPPSI)]
    assert_single_flag(parsed, "Add: wait  (skipped: unknown test 'wait'")


def test_unknown_item_with_no_close_match_has_no_suggestion():
    parsed = parse("Add: xyz")
    assert_single_flag(parsed, "unknown test 'xyz'")
    assert "did you mean" not in parsed.unrecognized_lines[0]


def test_ambiguous_item_is_flagged():
    parsed = parse("Add: psych")
    assert parsed.service_changes == []
    assert_single_flag(parsed, "matches more than one test")


def test_empty_items_in_a_list_are_ignored():
    parsed = parse("Add: cars, , wppsi,")
    assert parsed.service_changes == [add(CARS), add(WPPSI)]
    assert_clean(parsed)


def test_several_add_lines_are_allowed():
    parsed = parse("Add: wisc\nAdd: wiat")
    assert parsed.service_changes == [add(WISC), add(WIAT)]
    assert_clean(parsed)


def test_remove_is_not_a_field():
    # Per OA, tests are only ever added or changed, never removed.
    parsed = parse("Remove: wisc")
    assert parsed.service_changes == []
    assert_single_flag(parsed, "unrecognized field name")


def test_conflicting_changes_are_not_checked_here():
    # Adding a test and then changing it in the same comment is recorded
    # as written. Whether it makes sense depends on the case's tests,
    # which is checked when changes are applied, not when parsing.
    parsed = parse("Add: wiat\nChange: wiat to wppsi")
    assert parsed.service_changes == [add(WIAT), change(WIAT, WPPSI)]
    assert_clean(parsed)


# --- Change --------------------------------------------------------


def test_change_one_test_to_another():
    parsed = parse("Change: wisc to wais")
    assert parsed.service_changes == [change(WISC, WAIS)]
    assert_clean(parsed)


def test_change_ctoni_to_wais():
    # OA's own example: a verbal patient who doesn't need an interpreter.
    assert parse("Change: CTONI to WAIS").service_changes == [change(CTONI, WAIS)]


def test_change_keyword_is_case_insensitive():
    assert parse("Change: wisc TO wais").service_changes == [change(WISC, WAIS)]


def test_change_comma_separated_pairs():
    parsed = parse("Change: wisc to wais, wiat to wppsi")
    assert parsed.service_changes == [change(WISC, WAIS), change(WIAT, WPPSI)]
    assert_clean(parsed)


def test_bad_pair_does_not_block_a_good_pair():
    parsed = parse("Change: wisk to wais, wiat to wppsi")
    assert parsed.service_changes == [change(WIAT, WPPSI)]
    assert_single_flag(parsed, "Change: wisk to wais")
    assert "did you mean WISC?" in parsed.unrecognized_lines[0]


def test_change_with_one_bad_side_is_skipped_entirely():
    parsed = parse("Change: wisc to xyz")
    assert parsed.service_changes == []
    assert_single_flag(parsed, "unknown test 'xyz'")


def test_change_with_both_sides_bad_reports_both():
    parsed = parse("Change: wisk to waiz")
    assert_single_flag(parsed, "'wisk'")
    assert "'waiz'" in parsed.unrecognized_lines[0]


def test_change_without_to_is_flagged():
    parsed = parse("Change: wisc wais")
    assert parsed.service_changes == []
    assert_single_flag(parsed, "expected 'Change: <old test> to <new test>'")


def test_change_to_the_same_test_is_flagged():
    parsed = parse("Change: wisc to WISC-V")
    assert parsed.service_changes == []
    assert_single_flag(parsed, "the old and new test are the same")


def test_change_with_ambiguous_test_is_flagged():
    parsed = parse("Change: psych to wisc")
    assert parsed.service_changes == []
    assert_single_flag(parsed, "matches more than one test")


# --- Without a lookup ----------------------------------------------


@pytest.mark.parametrize("line", ["Add: wisc", "Change: wisc to wais"])
def test_service_lines_flagged_when_no_lookup_given(line):
    parsed = parse_comment(line)
    assert parsed.service_changes == []
    assert_single_flag(parsed, "services list not loaded")


def test_names_still_parse_without_a_lookup():
    parsed = parse_comment("Provider: Dr. Shelton\nPsychometrist: J. Smith")
    assert parsed.provider == "Dr. Shelton"
    assert parsed.psychometrist == "J. Smith"
    assert_clean(parsed)


# --- Blank values (template support) -------------------------------


def test_fully_blank_template_is_clean():
    parsed = parse("Provider:\nPsychometrist:\nAdd:\nChange:")
    assert parsed.provider is None
    assert parsed.psychometrist is None
    assert parsed.service_changes == []
    assert_clean(parsed)


def test_partially_filled_template():
    parsed = parse("Provider: Dr. Shelton\nPsychometrist: J. Smith\nAdd: cars\nChange:")
    assert parsed.provider == "Dr. Shelton"
    assert parsed.psychometrist == "J. Smith"
    assert parsed.service_changes == [add(CARS)]
    assert_clean(parsed)


@pytest.mark.parametrize(
    "comment",
    [
        "Provider:\nProvider: Dr. Shelton",
        "Provider: Dr. Shelton\nProvider:",
    ],
)
def test_blank_key_never_counts_as_duplicate(comment):
    parsed = parse(comment)
    assert parsed.provider == "Dr. Shelton"
    assert_clean(parsed)


# --- Duplicate names -----------------------------------------------


@pytest.mark.parametrize(
    "comment, attribute, expected",
    [
        ("Provider: Dr. Shelton\nProvider: Dr. Doe", "provider", "Dr. Doe"),
        ("Psychometrist: J. Smith\nPsychometrist: A. Lee", "psychometrist", "A. Lee"),
    ],
)
def test_duplicate_name_flagged_and_later_value_wins(comment, attribute, expected):
    parsed = parse(comment)
    assert getattr(parsed, attribute) == expected
    assert_single_flag(parsed, "applied: overrides an earlier")


# --- More than one field on a line ---------------------------------


def test_add_crammed_onto_provider_line_flagged():
    parsed = parse("Provider: Dr. Doe Add: cars")
    assert parsed.provider is None
    assert parsed.service_changes == []
    assert_single_flag(parsed, "more than one field")


def test_two_service_fields_crammed_together_flagged():
    parsed = parse("Add: cars Change: wisc to wais")
    assert parsed.service_changes == []
    assert_single_flag(parsed, "more than one field")


def test_crammed_note_mentions_semicolons():
    parsed = parse("Provider: Dr. Doe Add: cars")
    assert "separate them with ;" in parsed.unrecognized_lines[0]


def test_key_word_without_colon_is_not_treated_as_a_field():
    parsed = parse("Provider: Dr. Add Johnson")
    assert parsed.provider == "Dr. Add Johnson"
    assert_clean(parsed)


def test_correct_line_after_crammed_line_still_applies():
    parsed = parse("Provider: Dr. Doe Add: cars\nProvider: Dr. Shelton")
    assert parsed.provider == "Dr. Shelton"
    assert_single_flag(parsed, "more than one field")


@pytest.mark.parametrize(
    "line",
    [
        "Provider: Dr. Doe Notes: call back",
        "Provider: Dr. Doe +Service: 96130 - Autism testing - $92",
    ],
)
def test_unknown_field_crammed_onto_a_line_is_flagged(line):
    parsed = parse(line)
    assert parsed.provider is None
    assert_single_flag(parsed, "more than one field")


def test_time_in_a_value_is_not_treated_as_a_field():
    parsed = parse("Provider: Dr. Shelton at 1:30")
    assert parsed.provider == "Dr. Shelton at 1:30"
    assert_clean(parsed)


# --- Retired keys --------------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["+Service: 96130 - Autism testing - $92", "Price: $576"],
)
def test_retired_keys_are_flagged_as_unrecognized(line):
    parsed = parse(line)
    assert parsed.service_changes == []
    assert_single_flag(parsed, "unrecognized field name")


# --- Unrecognized keys and free text -------------------------------


@pytest.mark.parametrize(
    "line, suggestion",
    [
        ("Provder: Dr. Smith", "Provider"),
        ("Psychomtrist: J. Smith", "Psychometrist"),
        ("Ad: wisc", "Add"),
        ("Chnage: wisc to wais", "Change"),
    ],
)
def test_misspelled_key_suggests_correction(line, suggestion):
    assert_single_flag(parse(line), f"did you mean {suggestion}?")


def test_blank_misspelled_key_still_flagged():
    parsed = parse("Provider: Dr. S\nPsychomtrist:")
    assert parsed.provider == "Dr. S"
    assert_single_flag(parsed, "did you mean Psychometrist?")


def test_unrelated_key_gets_no_suggestion():
    assert_single_flag(parse("Notes: call back tomorrow"), "check spelling")


def test_line_without_colon_flagged():
    parsed = parse("Provider: Dr. Shelton\nchecked twice all good")
    assert parsed.provider == "Dr. Shelton"
    assert_single_flag(parsed, "not in 'Field: value' format")


def test_every_flagged_entry_states_outcome_and_reason():
    parsed = parse(
        "Provder: Dr. Smith\n"
        "Add: wait, cars\n"
        "Change: wisk to wais\n"
        "Psychometrist: J. Smith\n"
        "Psychometrist: A. Lee\n"
        "random note here"
    )
    assert len(parsed.unrecognized_lines) == 5
    for entry in parsed.unrecognized_lines:
        assert entry.rstrip().endswith(")"), f"no reason attached: {entry!r}"
        assert "(skipped:" in entry or "(applied:" in entry, f"no outcome stated: {entry!r}"