"""
Tests for app/contact_lookup.py: matching the names OA types in comments to the
people listed in the Contacts tab.

The addresses are made up (example.test), as are the names. No real person's name
or address belongs in this file.

Run from the project root:
    python -m pytest tests/test_contact_lookup.py -v
"""

import pytest

from app.contact_lookup import build_contact_lookup, name_words


HEADERS = ["Name", "Email", "Role"]


def people(*rows):
    """A Contacts tab as sheet rows, header first. Each row is (name, email, role)."""
    return [HEADERS, *[list(row) for row in rows]]


def standard():
    return build_contact_lookup(people(
        ("Dr. Jane Smith", "jane.smith@example.test", "Provider"),
        ("Dr. Raj Singh", "raj.singh@example.test", "Provider"),
        ("John Smith", "john.smith@example.test", "Psychometrist"),
        ("John Doe", "john.doe@example.test", "Psychometrist"),
        ("Jane Doe", "jane.doe@example.test", "Psychometrist"),
    ))


# --- Reading a name ------------------------------------------------


@pytest.mark.parametrize("text, expected", [
    ("Dr Smith", ("smith",)),
    ("Dr. Smith", ("smith",)),
    ("Dr.Smith", ("smith",)),
    ("DR. J. SMITH", ("j", "smith")),
    ("doctor smith", ("smith",)),
    ("Smith, Jane", ("jane", "smith")),
    ("Jane Marie Smith", ("jane", "marie", "smith")),
    ("O'Brien", ("obrien",)),
    ("Smith-Jones", ("smithjones",)),
    ("Ren\u00e9e M\u00fcller", ("renee", "muller")),
    ("  Dr.   J.    smith  ", ("j", "smith")),
    ("", ()),
    ("   ", ()),
    (None, ()),
])
def test_a_name_is_read_as_lowercase_words_without_a_title_or_punctuation(text, expected):
    assert name_words(text) == expected


# --- Building the lookup from the tab ------------------------------


def test_columns_are_found_by_header_name_in_any_order_and_case():
    lookup = build_contact_lookup([
        ["EMAIL", "role", "NAME"],
        ["jane@example.test", "Provider", "Dr. Jane Smith"],
    ])
    assert lookup.problems == []
    assert lookup.find("Dr Smith", "provider").email == "jane@example.test"


def test_the_role_column_is_optional():
    lookup = build_contact_lookup([["Name", "Email"], ["Dr. Jane Smith", "jane@example.test"]])
    assert lookup.problems == []
    assert lookup.find("Dr Smith", "provider").status == "found"
    assert lookup.find("Dr Smith", "psychometrist").status == "found"


def test_blank_rows_are_skipped_without_comment():
    lookup = build_contact_lookup(people(("", "", ""), ("Dr. Jane Smith", "jane@example.test", "")))
    assert lookup.problems == []
    assert len(lookup.contacts) == 1


@pytest.mark.parametrize("email", ["", "not an address", "jane@", "@example.test", "jane smith@example.test"])
def test_a_row_without_a_valid_email_is_skipped_and_reported_without_the_address(email):
    lookup = build_contact_lookup(people(("Dr. Jane Smith", email, "Provider")))
    assert lookup.contacts == []
    assert lookup.problems == ["Row 2: 'Dr. Jane Smith' has no valid email address, so it was skipped"]
    assert email == "" or email not in lookup.problems[0]


def test_a_row_without_a_name_is_skipped_and_reported():
    lookup = build_contact_lookup(people(("", "jane@example.test", "Provider")))
    assert lookup.contacts == []
    assert lookup.problems == ["Row 2: there is no name, so it was skipped"]


def test_an_unknown_role_is_skipped_and_reported():
    lookup = build_contact_lookup(people(("Dr. Jane Smith", "jane@example.test", "Boss")))
    assert lookup.contacts == []
    assert "the role 'Boss' isn't Provider or Psychometrist" in lookup.problems[0]


@pytest.mark.parametrize("headers, missing", [
    (["Name", "Role"], "Email"),
    (["Email", "Role"], "Name"),
    (["Provider", "Address"], "Name"),
])
def test_a_missing_name_or_email_column_raises(headers, missing):
    with pytest.raises(ValueError, match=f"missing the '{missing}' column"):
        build_contact_lookup([headers, ["x", "y"]])


def test_a_person_listed_twice_is_reported():
    lookup = build_contact_lookup(people(
        ("Dr. Jane Smith", "a@example.test", "Provider"),
        ("Jane Smith", "b@example.test", "Provider"),
    ))
    assert lookup.problems == [
        "'Dr. Jane Smith' is listed more than once, so it won't be accepted until the Contacts tab is fixed"
    ]


def test_the_same_name_in_two_different_roles_is_not_a_duplicate():
    lookup = build_contact_lookup(people(
        ("Jane Smith", "a@example.test", "Provider"),
        ("Jane Smith", "b@example.test", "Psychometrist"),
    ))
    assert lookup.problems == []


def test_no_problem_message_ever_contains_an_address():
    lookup = build_contact_lookup(people(
        ("Dr. Jane Smith", "bad address", "Provider"),
        ("Dr. Raj Singh", "raj@example.test", "Boss"),
        ("", "x@example.test", ""),
    ))
    assert lookup.problems and not any("@" in p for p in lookup.problems)


# --- Finding a person ----------------------------------------------


def test_the_whole_name_finds_the_person_and_their_address():
    match = standard().find("Dr. Jane Smith", "provider")
    assert (match.status, match.name, match.email) == ("found", "Dr. Jane Smith", "jane.smith@example.test")


@pytest.mark.parametrize("typed", ["Dr Smith", "dr smith", "DR. SMITH", "Dr.Smith", "Smith", "Doctor Smith"])
def test_the_last_name_alone_finds_the_only_provider_with_it(typed):
    assert standard().find(typed, "provider").email == "jane.smith@example.test"


@pytest.mark.parametrize("typed", ["Dr J Smith", "Dr J. Smith", "J Smith", "Dr Jane Smith", "Smith, Jane", "Jane Marie Smith"])
def test_an_initial_or_a_first_name_with_the_last_name_finds_them(typed):
    assert standard().find(typed, "provider").email == "jane.smith@example.test"


def test_a_role_keeps_a_provider_and_a_student_with_the_same_last_name_apart():
    lookup = standard()
    assert lookup.find("Dr Smith", "provider").email == "jane.smith@example.test"
    assert lookup.find("Smith", "psychometrist").email == "john.smith@example.test"


def test_a_row_with_no_role_counts_as_both():
    lookup = build_contact_lookup(people(("Dr. Raj Singh", "raj@example.test", "")))
    assert lookup.find("Dr Singh", "provider").status == "found"
    assert lookup.find("Singh", "psychometrist").status == "found"


def test_without_a_role_everyone_is_considered():
    assert standard().find("Smith").status == "ambiguous"


@pytest.mark.parametrize("typed", ["Dr S", "S", "Dr. S.", "J S", "Dr J S"])
def test_a_single_letter_is_too_short_to_match_anyone(typed):
    assert standard().find(typed, "provider").status == "too_short"


def test_a_name_that_fits_two_people_is_never_guessed():
    match = standard().find("Doe", "psychometrist")
    assert match.status == "ambiguous" and match.email == ""
    assert match.options == ["John Doe", "Jane Doe"]


def test_an_initial_that_fits_two_people_is_ambiguous_but_a_first_name_settles_it():
    lookup = build_contact_lookup(people(
        ("Jane Smith", "jane@example.test", "Provider"),
        ("John Smith", "john@example.test", "Provider"),
    ))
    assert lookup.find("Dr Smith", "provider").status == "ambiguous"
    assert lookup.find("Dr J Smith", "provider").status == "ambiguous"
    assert lookup.find("Dr John Smith", "provider").email == "john@example.test"


def test_two_people_with_different_initials_are_told_apart_by_the_initial():
    lookup = build_contact_lookup(people(
        ("Jane Smith", "jane@example.test", "Provider"),
        ("Raj Smith", "raj@example.test", "Provider"),
    ))
    assert lookup.find("Dr R Smith", "provider").email == "raj@example.test"
    assert lookup.find("Dr J Smith", "provider").email == "jane@example.test"


def test_a_person_listed_by_initial_is_found_by_their_first_name():
    lookup = build_contact_lookup(people(("J Smith", "j@example.test", "Provider")))
    assert lookup.find("Dr Jane Smith", "provider").email == "j@example.test"


def test_a_person_listed_by_last_name_only_is_found_with_any_first_name_or_initial():
    lookup = build_contact_lookup(people(("Shelton", "s@example.test", "Provider")))
    for typed in ("Dr Shelton", "Shelton", "Dr J Shelton", "Dr Jane Shelton"):
        assert lookup.find(typed, "provider").email == "s@example.test"


def test_the_wrong_initial_or_first_name_is_unknown():
    assert standard().find("Dr K Smith", "provider").status == "unknown"
    assert standard().find("Dr Raj Smith", "provider").status == "unknown"


@pytest.mark.parametrize("typed", ["Dr Patel", "", "   ", None, "Dr"])
def test_a_name_that_is_not_listed_is_unknown(typed):
    assert standard().find(typed, "provider").status == "unknown"


def test_a_duplicate_listing_makes_the_name_ambiguous_and_not_a_guess():
    lookup = build_contact_lookup(people(
        ("Dr. Jane Smith", "a@example.test", "Provider"),
        ("Jane Smith", "b@example.test", "Provider"),
    ))
    assert lookup.find("Dr Jane Smith", "provider").status == "ambiguous"


def test_accents_apostrophes_and_hyphens_do_not_stop_a_match():
    lookup = build_contact_lookup(people(
        ("Ren\u00e9e O'Brien-Jones", "r@example.test", "Psychometrist"),
    ))
    for typed in ("Renee OBrienJones", "renee o'brien-jones", "R. O'Brien-Jones", "O'Brien-Jones"):
        assert lookup.find(typed, "psychometrist").email == "r@example.test"


def test_an_exact_name_wins_over_a_looser_match():
    lookup = build_contact_lookup(people(
        ("Jane Smith", "jane@example.test", "Provider"),
        ("Jane Marie Smith", "marie@example.test", "Provider"),
    ))
    assert lookup.find("Jane Smith", "provider").email == "jane@example.test"