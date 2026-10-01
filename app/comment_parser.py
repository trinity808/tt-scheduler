"""
Parser for the OA daily comment shorthand format.

DRAFT — this format is a first proposal to bring to the team and
Dr. Shelton, not yet reviewed or approved. Built so that a field
being renamed, added, or dropped after review is a small, local
edit (one entry in FIELD_HANDLERS), not a rewrite of the parser.

Proposed format — one "Key: value" pair per line in the Comments cell:

    Provider: Dr. Shelton
    Psychometrist: J. Smith
    +Service: 96130 - Autism testing - $92
    Price: $576

Provider / Psychometrist: the name goes straight into the matching
field — no validation on format, just captured as-is.

+Service: adds one line item. Expected shape is
"<code> - <description> - $<amount>"; this is the rare case, so a
looser or more flexible shape can be discussed if this feels awkward
to type in practice.

Price: overrides the total price outright, expects a dollar amount.

Keys are matched case-insensitively (Provider / provider / PROVIDER
all work), since consistent capitalization isn't something to rely
on. Lines that don't match a known key are collected as
"unrecognized" rather than silently dropped, so a typo surfaces
instead of quietly disappearing. A key left blank (e.g. "Provider:"
with nothing after it) is treated as "not provided": skipped
silently, with the field left untouched. This supports OA pasting a
blank template and filling in only what applies. It doesn't count
toward duplicate detection either. A key repeated more than once in
the same comment is flagged, though the later value still wins
(Price/Provider/Psychometrist only; +Service is exempt since multiple
lines there are the normal way to add more than one service).
"""

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
    match = re.match(r"^(.+?)\s*-\s*(.+?)\s*-\s*\$?(\d+(?:\.\d{2})?)$", value.strip())
    if match:
        code, description, amount = match.groups()
        result.added_services.append(
            {"code": code.strip(), "description": description.strip(), "amount": amount}
        )
    else:
        result.unrecognized_lines.append(
            f"+Service: {value}  (couldn't parse — expected 'code - description - $amount')"
        )


def _handle_price(value, result):
    match = re.match(r"^\$?(\d+(?:\.\d{2})?)$", value.strip())
    if match:
        result.price_override = match.group(1)
    else:
        result.unrecognized_lines.append(f"Price: {value}  (couldn't parse — expected a dollar amount)")


# Each key maps to a handler. Adding/renaming/dropping a field after
# team review means editing this dict and its handler function —
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
# worth flagging for visibility, rather than silently letting the
# later one win. +service is deliberately excluded — multiple lines
# are the normal, expected way to add more than one service.
DUPLICATE_CHECKED_KEYS = {"provider", "psychometrist", "price"}


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
            result.unrecognized_lines.append(line)
            continue

        key, value = line.split(":", 1)
        normalized_key = key.strip().lower()
        handler = FIELD_HANDLERS.get(normalized_key)

        if handler:
            if not value.strip():
                continue
            if _contains_embedded_key(value):
                result.unrecognized_lines.append(
                    f"{line}  (looks like more than one field on one line, put each on its own line)"
                )
                continue
            if normalized_key in DUPLICATE_CHECKED_KEYS and normalized_key in seen_keys:
                result.unrecognized_lines.append(
                    f"{line}  (duplicate — {key.strip()} was already specified earlier; this later value is being used)"
                )
            seen_keys.add(normalized_key)
            handler(value, result)
        else:
            result.unrecognized_lines.append(line)

    return result


if __name__ == "__main__":
    sample = """Provider: Dr. Shelton
Service: 96130 - Autism testing - $92
+Services: 96101 - WISC-V - $175
servics: 90791 - Psychiatric Exam - $120"""

    print("--- Sample input ---")
    print(sample)

    parsed = parse_comment(sample)

    print("\n--- Parsed result ---")
    print(f"Provider: {parsed.provider}")
    print(f"Psychometrist: {parsed.psychometrist}")
    print(f"Added services: {parsed.added_services}")
    print(f"Price override: {parsed.price_override}")
    print(f"Unrecognized lines: {parsed.unrecognized_lines}")