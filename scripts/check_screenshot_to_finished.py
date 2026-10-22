"""
Run the screenshot matcher manually.

From the repository root:

Dry run:
    python -m scripts.check_screenshot_to_finished

Live run:
    python -m scripts.check_screenshot_to_finished --live
"""

import argparse

from app.screenshot_matcher import move_matches


def main():
    """Parse command-line options and run the screenshot matcher."""
    parser = argparse.ArgumentParser(
        description=(
            "Check Dropbox screenshots against Ongoing cases. "
            "Defaults to dry-run mode."
        )
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Actually move matching cases from Ongoing "
            "to Finished. Without this flag, no rows are changed."
        ),
    )

    args = parser.parse_args()

    move_matches(
        dry_run=not args.live
    )


if __name__ == "__main__":
    main()