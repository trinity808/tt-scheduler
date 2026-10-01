"""
Parser for the OA daily comment shorthand format.

DRAFT: this format is a proposal for the team and Dr. Shelton, not
yet reviewed or approved. Built so that a field being renamed,
added, or dropped after review is a small, local edit (one entry in
FIELD_HANDLERS), not a rewrite of the parser.

Format: one "Key: value" pair per line in the Comments cell:

    Provider: Dr. Shelton
    Psychometrist: J. Smith
    +Service: 96130 - Autism testing - $92
    Price: $576

Provider / Psychometrist: the name goes straight into the matching
field, captured as-is.

+Service: adds one line item, "<code> - <description> - $<amount>".
The "$" is optional, and Service / +Services / Services are accepted
as the same key.

Price: overrides the total price, expects a dollar amount ("$"
optional).

Keys are matched case-insensitively. A key left blank (e.g.
"Provider:" with nothing after it) is treated as "not provided":
skipped silently, field left untouched, and not counted toward
duplicate detection. This supports OA pasting a blank template and
filling in only what applies.

Anything else worth a human's attention is collected in
unrecognized_lines rather than silently dropped. Each entry is the
original line followed by what happened to it and why, starting with
"skipped:" (nothing from that line was used) or "applied:" (the line
was used, but something about it is worth knowing):
  - unrecognized field names, with a suggested correction when one
    is close
  - lines without a "Key: value" shape
  - service or price values that couldn't be parsed
  - more than one field crammed onto one line
  - a key repeated in the same comment (Provider, Psychometrist and
    Price only; the later value wins. +Service is exempt, since
    multiple lines are the normal way to add more than one service)
"""

import difflib
import re
from dataclasses import dataclass, field


@dataclass
class ParsedComment:
    provider: str = None
    psychometrist: str = None
    added_services: list = field(default_factory=list)
    price_override: str = None
    unrecognized_lines: list = field(default_factory=list)


def _handle_provider(value, result):
    value = value.strip()
    if value:
        result.provider = value


def _handle_psychometrist(value, result):
    value = value.strip()
    if value:
        result.psychometrist = value


def _handle_add_service(value, result):
    value = value.strip()
    match = re.match(r"^(.+?)\s*-\s*(.+?)\s*-\s*\$?(\d+(?:\.\d{2})?)$", value)
    if match:
        code, description, amount = match.groups()
        result.added_services.append(
            {"code": code.strip(), "description": description.strip(), "amount": amount}
        )
    else:
        result.unrecognized_lines.append(
            f"+Service: {value}  (skipped: couldn't parse, expected 'code - description - $amount')"
        )


def _handle_price(value, result):
    value = value.strip()
    match = re.match(r"^\$?(\d+(?:\.\d{2})?)$", value)
    if match:
        result.price_override = match.group(1)
    else:
        result.unrecognized_lines.append(
            f"Price: {value}  (skipped: couldn't parse, expected a dollar amount)"
        )


# Each key maps to a handler. Adding/renaming/dropping a field after
# team review means editing this dict and its handler function;
# parse_comment() itself never needs to change.
#
# Service has several accepted spellings, since dropping the "+" or
# pluralizing it is an easy slip at end of day. All of them route to
# the same handler, so they behave identically.
FIELD_HANDLERS = {
    "provider": _handle_provider,
    "psychometrist": _handle_psychometrist,
    "+service": _handle_add_service,
    "+services": _handle_add_service,
    "service": _handle_add_service,
    "services": _handle_add_service,
    "price": _handle_price,
}

# Keys where a second occurrence in the same comment is unusual and
# worth flagging, rather than silently letting the later one win.
# +service is excluded: multiple lines are the normal way to add more
# than one service.
DUPLICATE_CHECKED_KEYS = {"provider", "psychometrist", "price"}

# Display names, used in messages and when suggesting a correction for
# a misspelled key.
_KEY_SUGGESTIONS = {
    "provider": "Provider",
    "psychometrist": "Psychometrist",
    "+service": "+Service",
    "price": "Price",
}


def _contains_embedded_key(value):
    """
    True if a value contains another recognized key followed by a
    colon, which means two fields were crammed onto one line (e.g.
    "Provider: Dr. Doe +Service: 96130 ..."). Treating that whole
    string as the first field's value would silently write the wrong
    data, so the line gets flagged instead.
    """
    lowered = value.lower()
    return any(f"{key}:" in lowered for key in FIELD_HANDLERS)


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


def parse_comment(comment_text):
    result = ParsedComment()
    seen_keys = set()

    if not comment_text:
        return result

    for line in comment_text.strip().splitlines():
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
                "put each on its own line)"
            )
            continue

        # Run the handler first, then decide about duplicates. A later
        # line that fails to parse already gets its own "skipped"
        # message and doesn't override anything, so it shouldn't also
        # be reported as an applied override.
        flags_before = len(result.unrecognized_lines)
        handler(value, result)
        handled_ok = len(result.unrecognized_lines) == flags_before

        if handled_ok and normalized_key in DUPLICATE_CHECKED_KEYS:
            if normalized_key in seen_keys:
                display = _KEY_SUGGESTIONS.get(normalized_key, key.strip())
                result.unrecognized_lines.append(
                    f"{line}  (applied: overrides an earlier {display} line)"
                )
            seen_keys.add(normalized_key)

    return result


if __name__ == "__main__":
    sample = """Provider: Dr. Shelton
Psychometrist: J. Smith
+Service: 96130 - Autism testing - $92
Price: $576"""

    print("--- Sample input ---")
    print(sample)

    parsed = parse_comment(sample)

    print("\n--- Parsed result ---")
    print(f"Provider: {parsed.provider}")
    print(f"Psychometrist: {parsed.psychometrist}")
    print(f"Added services: {parsed.added_services}")
    print(f"Price override: {parsed.price_override}")
    print(f"Unrecognized lines: {parsed.unrecognized_lines}")