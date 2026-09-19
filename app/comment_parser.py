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
instead of quietly disappearing.
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
    result.provider = value.strip()


def _handle_psychometrist(value, result):
    result.psychometrist = value.strip()


def _handle_add_service(value, result):
    match = re.match(r"^(.+?)\s*-\s*(.+?)\s*-\s*\$(\d+(?:\.\d{2})?)$", value.strip())
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
FIELD_HANDLERS = {
    "provider": _handle_provider,
    "psychometrist": _handle_psychometrist,
    "+service": _handle_add_service,
    "price": _handle_price,
}


def parse_comment(comment_text):
    result = ParsedComment()

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
        handler = FIELD_HANDLERS.get(key.strip().lower())

        if handler:
            handler(value, result)
        else:
            result.unrecognized_lines.append(line)

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