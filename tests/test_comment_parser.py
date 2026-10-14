"""
Tests for app/comment_parser.py.

Pure parser tests: text in, ParsedComment out. No Google Sheets,
no network, no setup. Each test mirrors a pattern that was checked
by hand during development, so a future change that breaks any of
them shows up immediately.

Run from the project root:
    python -m pytest tests/test_comment_parser.py -v
"""

import pytest

from app.comment_parser import parse_comment


def assert_clean(parsed):
    """No issues flagged."""
    assert parsed.unrecognized_lines == []


def assert_single_flag(parsed, expected_fragment):
    """Exactly one issue flagged, and its message contains the fragment."""
    assert len(parsed.unrecognized_lines) == 1
    assert expected_fragment in parsed.unrecognized_lines[0]


# --- Basic parsing -------------------------------------------------


def test_full_comment():
    parsed = parse_comment(
        "Provider: Dr. Shelton\n"
        "Psychometrist: J. Smith\n"
        "+Service: 96130 - Autism testing - $92\n"
        "Price: $576"
    )
    assert parsed.provider == "Dr. Shelton"
    assert parsed.psychometrist == "J. Smith"
    assert parsed.added_services == [
        {"code": "96130", "description": "Autism testing", "amount": "92"}
    ]
    assert parsed.price_override == "576"
    assert_clean(parsed)


@pytest.mark.parametrize("empty_input", ["", None, "   ", "\n\n"])
def test_empty_comment_returns_defaults(empty_input):
    parsed = parse_comment(empty_input)
    assert parsed.provider is None
    assert parsed.psychometrist is None
    assert parsed.added_services == []
    assert parsed.price_override is None
    assert_clean(parsed)


def test_only_provider():
    parsed = parse_comment("Provider: Dr. Shelton")
    assert parsed.provider == "Dr. Shelton"
    assert parsed.psychometrist is None
    assert_clean(parsed)


@pytest.mark.parametrize("key", ["provider", "PROVIDER", "Provider", "pRoViDeR"])
def test_keys_are_case_insensitive(key):
    parsed = parse_comment(f"{key}: Dr. Shelton")
    assert parsed.provider == "Dr. Shelton"
    assert_clean(parsed)


def test_surrounding_whitespace_and_blank_lines_ignored():
    parsed = parse_comment(
        "\n   Provider:   Dr. Shelton   \n\n\n  Psychometrist: J. Smith  \n"
    )
    assert parsed.provider == "Dr. Shelton"
    assert parsed.psychometrist == "J. Smith"
    assert_clean(parsed)


# --- Services ------------------------------------------------------


@pytest.mark.parametrize("key", ["+Service", "+Services", "Service", "services", "SERVICE"])
def test_service_spelling_variants_accepted(key):
    parsed = parse_comment(f"{key}: 96130 - Autism testing - $92")
    assert len(parsed.added_services) == 1
    assert_clean(parsed)


def test_multiple_services_accumulate():
    parsed = parse_comment(
        "+Service: 96130 - Autism testing - $92\n"
        "+Service: 96101 - WISC - $175"
    )
    assert [s["code"] for s in parsed.added_services] == ["96130", "96101"]
    assert_clean(parsed)


def test_service_dollar_sign_optional():
    parsed = parse_comment("+Service: 96130 - Autism testing - 92")
    assert parsed.added_services[0]["amount"] == "92"
    assert_clean(parsed)


def test_service_decimal_amount():
    parsed = parse_comment("+Service: 96130 - Autism testing - $92.50")
    assert parsed.added_services[0]["amount"] == "92.50"
    assert_clean(parsed)


def test_service_hyphen_inside_word():
    parsed = parse_comment("+Service: 96101 - WISC-V - $175")
    assert parsed.added_services[0]["description"] == "WISC-V"
    assert_clean(parsed)


def test_service_spaced_hyphen_inside_description():
    parsed = parse_comment(
        "+Service: 96130 - Extended Cognitive - Behavioral Assessment - $92"
    )
    assert parsed.added_services[0]["description"] == (
        "Extended Cognitive - Behavioral Assessment"
    )
    assert_clean(parsed)


@pytest.mark.parametrize(
    "bad_value",
    [
        "96130 - Autism - testing",   # no amount
        "96130 / Autism testing - $92",  # wrong separator
        "96130 Autism testing",       # no separators at all
    ],
)
def test_malformed_service_flagged(bad_value):
    parsed = parse_comment(f"+Service: {bad_value}")
    assert parsed.added_services == []
    assert_single_flag(parsed, "couldn't parse")


# --- Price ---------------------------------------------------------


@pytest.mark.parametrize("value", ["$576", "576"])
def test_price_dollar_sign_optional(value):
    parsed = parse_comment(f"Price: {value}")
    assert parsed.price_override == "576"
    assert_clean(parsed)


def test_price_with_only_dollar_sign_flagged():
    parsed = parse_comment("Price: $")
    assert parsed.price_override is None
    assert_single_flag(parsed, "couldn't parse")


# --- Blank values (template support) -------------------------------


def test_fully_blank_template_is_clean():
    parsed = parse_comment("Provider:\nPsychometrist:\n+Service:\nPrice:")
    assert parsed.provider is None
    assert parsed.psychometrist is None
    assert parsed.added_services == []
    assert parsed.price_override is None
    assert_clean(parsed)


def test_partially_filled_template():
    parsed = parse_comment(
        "Provider: Dr. Shelton\nPsychometrist: J. Smith\n+Service:\nPrice:"
    )
    assert parsed.provider == "Dr. Shelton"
    assert parsed.psychometrist == "J. Smith"
    assert_clean(parsed)


@pytest.mark.parametrize(
    "comment",
    [
        "Provider:\nProvider: Dr. Shelton",   # blank first, filled second
        "Provider: Dr. Shelton\nProvider:",   # filled first, blank second
    ],
)
def test_blank_key_never_counts_as_duplicate(comment):
    parsed = parse_comment(comment)
    assert parsed.provider == "Dr. Shelton"
    assert_clean(parsed)


# --- Duplicate keys ------------------------------------------------


@pytest.mark.parametrize(
    "comment, attribute, expected",
    [
        ("Provider: Dr. Shelton\nProvider: Dr. Doe", "provider", "Dr. Doe"),
        ("Psychometrist: J. Smith\nPsychometrist: A. Lee", "psychometrist", "A. Lee"),
        ("Price: $576\nPrice: $24", "price_override", "24"),
    ],
)
def test_duplicate_key_flagged_and_later_value_wins(comment, attribute, expected):
    parsed = parse_comment(comment)
    assert getattr(parsed, attribute) == expected
    assert_single_flag(parsed, "applied: overrides an earlier")


def test_duplicate_with_unparseable_later_value_keeps_earlier():
    parsed = parse_comment("Price: $576\nPrice: abc")
    assert parsed.price_override == "576"
    assert_single_flag(parsed, "skipped: couldn't parse")


# --- More than one field on a line ---------------------------------


def test_service_crammed_onto_provider_line_flagged():
    parsed = parse_comment("Provider: Dr. Doe +Service: 96130 - Autism testing - $92")
    assert parsed.provider is None
    assert parsed.added_services == []
    assert_single_flag(parsed, "more than one field")


def test_crammed_line_flagged_whichever_key_comes_first():
    parsed = parse_comment("Price: $576 Provider: Dr. Doe")
    assert parsed.price_override is None
    assert parsed.provider is None
    assert_single_flag(parsed, "more than one field")


def test_bare_service_alias_also_caught_when_crammed():
    parsed = parse_comment("Provider: Dr. Doe Service: 96130 - Autism - testing - $92")
    assert parsed.provider is None
    assert_single_flag(parsed, "more than one field")


def test_key_word_without_colon_is_not_treated_as_a_field():
    parsed = parse_comment("Provider: Dr. Price Johnson")
    assert parsed.provider == "Dr. Price Johnson"
    assert_clean(parsed)


def test_correct_line_after_crammed_line_still_applies():
    parsed = parse_comment(
        "Provider: Dr. Doe +Service: 96130 - Autism testing - $92\n"
        "Provider: Dr. Shelton"
    )
    assert parsed.provider == "Dr. Shelton"
    # Only the crammed line is flagged; it doesn't make the second
    # Provider line count as a duplicate.
    assert_single_flag(parsed, "more than one field")


# --- Unrecognized keys and free text -------------------------------


@pytest.mark.parametrize(
    "line, suggestion",
    [
        ("Provder: Dr. Smith", "Provider"),
        ("Providr: Dr. Smith", "Provider"),
        ("Psychomtrist: J. Smith", "Psychometrist"),
        ("Servce: 96130 - Autism testing - $92", "+Service"),
        ("Prce: $576", "Price"),
    ],
)
def test_misspelled_key_suggests_correction(line, suggestion):
    parsed = parse_comment(line)
    assert_single_flag(parsed, f"did you mean {suggestion}?")


def test_blank_misspelled_key_still_flagged():
    # A typo in OA's saved template should surface even before she
    # fills that field in.
    parsed = parse_comment("Provider: Dr. S\nPsychomtrist:")
    assert parsed.provider == "Dr. S"
    assert_single_flag(parsed, "did you mean Psychometrist?")


def test_unrelated_key_gets_no_suggestion():
    parsed = parse_comment("Notes: call back tomorrow")
    assert_single_flag(parsed, "check spelling")


def test_line_without_colon_flagged():
    parsed = parse_comment("Provider: Dr. Shelton\nchecked twice all good")
    assert parsed.provider == "Dr. Shelton"
    assert_single_flag(parsed, "not in 'Field: value' format")


def test_every_flagged_line_carries_a_reason():
    # A mixed comment shouldn't leave any entry as a bare line,
    # otherwise the one entry with an explanation reads like the only
    # real problem.
    parsed = parse_comment(
        "Provder: Dr. Smith\n"
        "+Service: 9546-Autism 52\n"
        "Price: $576\n"
        "Price: $24\n"
        "random note here"
    )
    assert len(parsed.unrecognized_lines) == 4
    for entry in parsed.unrecognized_lines:
        assert entry.rstrip().endswith(")"), f"no reason attached: {entry!r}"
        assert "(skipped:" in entry or "(applied:" in entry, f"no outcome stated: {entry!r}"