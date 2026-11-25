"""
Recalculate case prices from the Services Requested column.

Reads all cases from the Ongoing tab and retrieves service prices
from the Services tab.

For each case:
- Extracts all service codes listed in Services Requested.
- Calculates the total using the corresponding service prices.
- Compares the calculated total with the existing Price value.
- Updates only the Price column when the total differs.
- Skips cases with missing service codes or prices and reports why.
- Skips cases whose Price is already correct.

Runs in preview mode by default without modifying Google Sheets.
Use --live to apply price changes.

This module operates independently of the Comments sweep and
does not modify providers, psychometrists, or requested services.
"""

import argparse
from decimal import Decimal, InvalidOperation

from app.sheet_client import (
    get_client,
    get_header_map,
    load_service_prices,
    calculate_service_price,
    update_case_field,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        action="store_true",
        help="Update prices in Google Sheets",
    )
    args = parser.parse_args()

    spreadsheet = get_client()
    ongoing = spreadsheet.worksheet("Ongoing")
    services_tab = spreadsheet.worksheet("Services")

    prices = load_service_prices(services_tab)
    headers = get_header_map(ongoing)

    required = ("Case ID", "Services Requested", "Price")
    for column in required:
        if column not in headers:
            raise ValueError(f"Missing column: {column}")

    rows = ongoing.get_all_values()

    updated = 0
    unchanged = 0
    skipped = 0

    for row_number, row in enumerate(rows[1:], start=2):
        def get_value(column):
            index = headers[column] - 1
            return row[index] if index < len(row) else ""

        case_id = get_value("Case ID").strip()
        if not case_id:
            continue

        services = get_value("Services Requested")
        old_price = get_value("Price")

        try:
            total = calculate_service_price(services, prices)
        except ValueError as error:
            skipped += 1
            print(f"Case {case_id}: SKIPPED - {error}")
            continue

        try:
            existing = Decimal(
                str(old_price).replace("$", "").replace(",", "").strip()
            )
            if not existing.is_finite():
                existing = None
        except InvalidOperation:
            existing = None

        if existing == total:
            unchanged += 1
            print(f"Case {case_id}: UNCHANGED - ${total:.2f}")
            continue

        if args.live:
            update_case_field(
                ongoing, row_number, "Price", float(total)
            )
            updated += 1
            print(
                f"Case {case_id}: UPDATED "
                f"${old_price or 'blank'} -> ${total:.2f}"
            )
        else:
            updated += 1
            print(
                f"Case {case_id}: WOULD UPDATE "
                f"${old_price or 'blank'} -> ${total:.2f}"
            )

    print(
        f"\nSummary: {updated} {'updated' if args.live else 'would update'}, "
        f"{unchanged} unchanged, {skipped} skipped."
    )


if __name__ == "__main__":
    main()