"""
Unit tests for screenshot matcher name conversion
and duplicate screenshot detection.
"""

import pytest

from app.screenshot_matcher import (
    patient_name_to_claimant_folder,
    split_duplicate_screenshot_matches,
)


@pytest.mark.parametrize(
    "patient_name, expected",
    [
        ("John Smith", "smithjohn"),
        ("John Michael Smith", "smithjohn"),
        ("John Michael David Smith", "smithjohn"),
        ("Smith, John Michael", "smithjohn"),
        (None, ""),
        ("", ""),
        ("John", ""),
        (", John", ""),
    ],
)
def test_patient_name_to_claimant_folder(patient_name, expected):
    assert patient_name_to_claimant_folder(patient_name) == expected


def make_match(case_id, screenshot_path):
    return {
        "row": {"Case ID": case_id},
        "screenshot_path": screenshot_path,
    }


def test_no_duplicate_screenshots():
    matches = [
        make_match("1001", "/screenshots/one.png"),
        make_match("1002", "/screenshots/two.png"),
    ]

    safe, skipped = split_duplicate_screenshot_matches(matches)

    assert safe == matches
    assert skipped == []


def test_two_cases_sharing_screenshot():
    matches = [
        make_match("1001", "/screenshots/one.png"),
        make_match("1002", "/screenshots/one.png"),
    ]

    safe, skipped = split_duplicate_screenshot_matches(matches)

    assert safe == []
    assert skipped == matches


def test_screenshot_paths_case_insensitive():
    matches = [
        make_match("1001", "/Screenshots/One.PNG"),
        make_match("1002", "/screenshots/one.png"),
    ]

    safe, skipped = split_duplicate_screenshot_matches(matches)

    assert safe == []
    assert skipped == matches


def test_three_cases_sharing_screenshot():
    matches = [
        make_match("1001", "/screenshots/one.png"),
        make_match("1002", "/screenshots/one.png"),
        make_match("1003", "/screenshots/one.png"),
    ]

    safe, skipped = split_duplicate_screenshot_matches(matches)

    assert safe == []
    assert skipped == matches


def test_input_list_is_not_modified():
    matches = [
        make_match("1001", "/screenshots/one.png"),
        make_match("1002", "/screenshots/one.png"),
        make_match("1003", "/screenshots/two.png"),
    ]

    original = list(matches)

    safe, skipped = split_duplicate_screenshot_matches(matches)

    assert matches == original
    assert [m["row"]["Case ID"] for m in safe] == ["1003"]
    assert [m["row"]["Case ID"] for m in skipped] == ["1001", "1002"]
