"""
Dropbox file metadata lookup.

Used to determine when a file (e.g. a provider's submission
screenshot) actually landed in Dropbox, since image EXIF timestamps
are unreliable for this — a standard Windows Print Screen/Snipping
Tool capture typically carries no timestamp metadata at all, unlike
a camera photo.

Uses server_modified specifically, not client_modified. Per
Dropbox's own docs, server_modified is the time the file was
verifiably committed to Dropbox's servers; client_modified is
whatever the uploading client claims and isn't verified, so it
shouldn't be trusted for anything beyond display/sorting.

Note: the SDK returns server_modified as a timezone-naive datetime,
even though the underlying value is genuinely UTC. UTC is attached
explicitly here so this is safe to compare against other
timezone-aware datetimes later, rather than silently being "UTC
pretending to be naive."
"""

from datetime import timezone

from apis.dropbox_api import get_dropbox_client


def get_file_upload_time(dropbox_path):
    """
    Returns the timezone-aware UTC datetime Dropbox recorded
    receiving the file at dropbox_path. Raises if the path doesn't
    exist.
    """
    dbx = get_dropbox_client()
    metadata = dbx.files_get_metadata(dropbox_path)
    return metadata.server_modified.replace(tzinfo=timezone.utc)


if __name__ == "__main__":
    path = input("Dropbox path to check (e.g. /submitted_screenshots/some_file.png): ").strip()
    timestamp = get_file_upload_time(path)
    print(f"Dropbox received this file at: {timestamp}")