"""
Parser for OA's notes in the Comments column.

DRAFT: the format still needs OA's final confirmation. Built so a
field being renamed, added, or dropped is a small, local edit (one
entry in FIELD_HANDLERS), not a rewrite of the parser.

Format: one "Key: value" per line, or several on one line separated
by semicolons:

    Provider: Dr. Shelton
    Psychometrist: J. Smith
    Add: WMS, VABS
    Change: CTONI to WAIS

Provider / Psychometrist: the name goes straight into the matching
field, captured as-is.

Add: one or more tests, comma-separated, by any name the Services tab
knows ("wms", "WISC-V", "Psychiatric Exam"). Each test is resolved
through a ServiceLookup (see service_lookup.py) on its own: known tests
go through, and an unknown or ambiguous one is flagged without
blocking the rest.

Change: one or more changes, comma-separated, each "<old> to <new>".
Each change is all or nothing on its own: if either test in a pair
can't be resolved, that pair is flagged and nothing from it is
applied, but the other pairs still go through.

There is no Remove: per OA, tests are only ever added or changed,
never removed.

The lookup is optional. Provider and Psychometrist parse without one;
Add and Change lines are flagged as "services list not loaded" if none
is given.

Keys are matched case-insensitively. A key left blank (e.g.
"Provider:" with nothing after it) means "not provided": skipped
silently, and not counted toward duplicate detection. This supports OA
pasting a blank template and filling in only what applies.

Anything else worth a human's attention is collected in
unrecognized_lines rather than silently dropped. Each entry is the
original line (or, for Add, the one item; for Change, the one pair)
followed by what happened to it and why, starting with "skipped:" or
"applied:":
  - unrecognized field names, with a suggested correction when one is
    close
  - lines without a "Key: value" shape
  - tests not in the Services tab, or matching more than one test
  - more than one field crammed onto one line
  - Provider or Psychometrist given twice in the same comment (the
    later value wins)

Checking a change against the case's actual tests (changing a test the
case doesn't have, say) is not done here. That needs the case's
current services, and belongs to the step that applies changes.
"""

import difflib
import re
from dataclasses import dataclass, field


@dataclass
class ServiceChange:
    action: str           # "add" or "change"
    code: str             # test added; for "change", the new test
    replaces: str = None  # for "change" only: the test being replaced


@dataclass
class ParsedComment:
    provider: str = None
    psychometrist: str = None
    service_changes: list = field(default_factory=list)
    unrecognized_lines: list = field(default_factory=list)


# --- Field handlers ---------------------------------------------------


def _handle_provider(value, result, lookup):
    value = value.strip()
    if value:
        result.provider = value


def _handle_psychometrist(value, result, lookup):
    value = value.strip()
    if value:
        result.psychometrist = value


def _resolve_test(name, lookup):
    """Returns (code, None) if resolved, or (None, problem description)."""
    name = name.strip()
    code = lookup.resolve(name)
    if code:
        return code, None
    if lookup.is_ambiguous(name):
        return None, f"'{name}' matches more than one test"
    suggestion = lookup.suggest(name)
    if suggestion:
        return None, f"unknown test '{name}', did you mean {suggestion}?"
    return None, f"unknown test '{name}'"


def _handle_add(value, result, lookup):
    if lookup is None:
        result.unrecognized_lines.append(
            f"Add: {value.strip()}  (skipped: services list not loaded)"
        )
        return
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        code, problem = _resolve_test(item, lookup)
        if code:
            result.service_changes.append(ServiceChange("add", code))
        else:
            result.unrecognized_lines.append(f"Add: {item}  (skipped: {problem})")


_CHANGE = re.compile(r"^(.+?)\s+to\s+(.+)$", re.IGNORECASE)


def _handle_change(value, result, lookup):
    value = value.strip()
    if lookup is None:
        result.unrecognized_lines.append(
            f"Change: {value}  (skipped: services list not loaded)"
        )
        return
    for pair in value.split(","):
        pair = pair.strip()
        if pair:
            _apply_change_pair(pair, result, lookup)


def _apply_change_pair(pair, result, lookup):
    """One "<old> to <new>" change. All or nothing for this pair only."""
    match = _CHANGE.match(pair)
    if not match:
        result.unrecognized_lines.append(
            f"Change: {pair}  (skipped: expected 'Change: <old test> to <new test>')"
        )
        return

    old_name, new_name = match.groups()
    old_code, old_problem = _resolve_test(old_name, lookup)
    new_code, new_problem = _resolve_test(new_name, lookup)
    problems = [p for p in (old_problem, new_problem) if p]
    if problems:
        result.unrecognized_lines.append(
            f"Change: {pair}  (skipped: {'; '.join(problems)})"
        )
        return

    if old_code == new_code:
        result.unrecognized_lines.append(
            f"Change: {pair}  (skipped: the old and new test are the same)"
        )
        return

    result.service_changes.append(ServiceChange("change", new_code, replaces=old_code))


# Each key maps to a handler. Adding/renaming/dropping a field means
# editing this dict and its handler; parse_comment() never changes.
FIELD_HANDLERS = {
    "provider": _handle_provider,
    "psychometrist": _handle_psychometrist,
    "add": _handle_add,
    "change": _handle_change,
}

# Keys where a second occurrence in one comment is unusual and worth
# flagging. Add and Change are excluded: several lines of changes are
# normal.
DUPLICATE_CHECKED_KEYS = {"provider", "psychometrist"}

# Display names, used in messages and when suggesting a correction for
# a misspelled key.
_KEY_SUGGESTIONS = {
    "provider": "Provider",
    "psychometrist": "Psychometrist",
    "add": "Add",
    "change": "Change",
}


# A word followed by a colon, e.g. "Add:" or "Notes:". A letter must
# come first, so a time like "1:30" doesn't count.
_EMBEDDED_FIELD = re.compile(r"[A-Za-z+][\w+]*\s*:")


def _contains_embedded_key(value):
    """
    True if a value contains what looks like another field, a word
    followed by a colon, which means two fields were crammed onto one
    line (e.g. "Provider: Dr. Doe Add: WMS"). This deliberately isn't
    limited to recognized keys: an unknown one like "Notes:" would
    otherwise end up inside the first field's value, silently.
    """
    return bool(_EMBEDDED_FIELD.search(value))


def _unknown_key_message(line, key):
    match = difflib.get_close_matches(
        key.strip().lower(), _KEY_SUGGESTIONS, n=1, cutoff=0.6
    )
    if match:
        return (
            f"{line}  (skipped: unrecognized field name, "
            f"did you mean {_KEY_SUGGESTIONS[match[0]]}?)"
        )
    return f"{line}  (skipped: unrecognized field name, check spelling)"


def parse_comment(comment_text, lookup=None):
    result = ParsedComment()
    seen_keys = set()

    if not comment_text:
        return result

    # Line breaks and semicolons both separate statements.
    for line in re.split(r"[\r\n;]+", comment_text):
        line = line.strip()
        if not line:
            continue

        if ":" not in line:
            result.unrecognized_lines.append(
                f"{line}  (skipped: not in 'Field: value' format)"
            )
            continue

        key, value = line.split(":", 1)
        normalized_key = key.strip().lower()
        handler = FIELD_HANDLERS.get(normalized_key)

        if not handler:
            result.unrecognized_lines.append(_unknown_key_message(line, key))
            continue

        # Blank value means "not provided": skip silently.
        if not value.strip():
            continue

        if _contains_embedded_key(value):
            result.unrecognized_lines.append(
                f"{line}  (skipped: more than one field on one line, "
                "put each on its own line or separate them with ;)"
            )
            continue

        # Run the handler first, then decide about duplicates, so a
        # later line that fails isn't reported as an applied override.
        flags_before = len(result.unrecognized_lines)
        handler(value, result, lookup)
        handled_ok = len(result.unrecognized_lines) == flags_before

        if handled_ok and normalized_key in DUPLICATE_CHECKED_KEYS:
            if normalized_key in seen_keys:
                display = _KEY_SUGGESTIONS[normalized_key]
                result.unrecognized_lines.append(
                    f"{line}  (applied: overrides an earlier {display} line)"
                )
            seen_keys.add(normalized_key)

    return result


if __name__ == "__main__":
    from app.service_lookup import build_lookup

    lookup = build_lookup([
        ["Code", "Aliases"],
        ["96101-WISC-V", "WISC"],
        ["96101-WIAT-III", "WIAT"],
        ["96101-WAIS-IV", "WAIS"],
        ["96101-WPPSI-IV", "WPPSI"],
        ["96130-CARS-2", "CARS"],
        ["96130-CTONI2", "CTONI"],
    ])

    sample = """Provider: Dr. Shelton
Psychometrist: J. Smith
Add: wiat, cars, wppsi
Change: ctoni to wais, wisk to wiat"""

    print("--- Sample input ---")
    print(sample)

    parsed = parse_comment(sample, lookup)

    print("\n--- Parsed result ---")
    print(f"Provider: {parsed.provider}")
    print(f"Psychometrist: {parsed.psychometrist}")
    for change in parsed.service_changes:
        print(f"Service change: {change}")
    print(f"Unrecognized lines: {parsed.unrecognized_lines}")