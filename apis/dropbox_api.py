import os
import dropbox
from dotenv import load_dotenv

from app.health_monitor import get_logger

load_dotenv()

logger = get_logger()


def get_dropbox_client():
    """
    Creates a Dropbox client using refresh-token auth.

    Required .env values:
    DROPBOX_APP_KEY=...
    DROPBOX_APP_SECRET=...
    DROPBOX_REFRESH_TOKEN=...
    """

    app_key = os.getenv("DROPBOX_APP_KEY")
    app_secret = os.getenv("DROPBOX_APP_SECRET")
    refresh_token = os.getenv("DROPBOX_REFRESH_TOKEN")

    if not app_key:
        raise ValueError("DROPBOX_APP_KEY not found in .env")

    if not app_secret:
        raise ValueError("DROPBOX_APP_SECRET not found in .env")

    if not refresh_token:
        raise ValueError("DROPBOX_REFRESH_TOKEN not found in .env")

    return dropbox.Dropbox(
        oauth2_refresh_token=refresh_token,
        app_key=app_key,
        app_secret=app_secret
    )


def list_pdfs(folder_path):
    """
    Lists all PDF files inside a Dropbox folder.
    """

    dbx = get_dropbox_client()
    result = dbx.files_list_folder(folder_path)

    pdf_files = []

    for entry in result.entries:
        if isinstance(entry, dropbox.files.FileMetadata):
            if entry.name.lower().endswith(".pdf"):
                pdf_files.append(entry)

    while result.has_more:
        result = dbx.files_list_folder_continue(result.cursor)

        for entry in result.entries:
            if isinstance(entry, dropbox.files.FileMetadata):
                if entry.name.lower().endswith(".pdf"):
                    pdf_files.append(entry)

    return pdf_files


def download_file(dropbox_path, local_path):
    """
    Downloads a file from Dropbox to a local path.
    """

    dbx = get_dropbox_client()

    os.makedirs(os.path.dirname(local_path), exist_ok=True)

    metadata, response = dbx.files_download(dropbox_path)

    with open(local_path, "wb") as f:
        f.write(response.content)

    logger.info("Downloaded from Dropbox: %s", dropbox_path)
    return local_path


def upload_file(local_path, dropbox_folder):
    """
    Uploads a local file to a Dropbox folder.
    If the file already exists, it overwrites it.
    """

    dbx = get_dropbox_client()

    filename = os.path.basename(local_path)
    dropbox_path = f"{dropbox_folder.rstrip('/')}/{filename}"

    with open(local_path, "rb") as f:
        dbx.files_upload(
            f.read(),
            dropbox_path,
            mode=dropbox.files.WriteMode.overwrite
        )

    print(f"Uploaded to Dropbox: {dropbox_path}")
    return dropbox_path


def download_if_exists(dropbox_path, local_path):
    """
    Downloads a Dropbox file only if it exists.
    Useful for downloading an existing schedule DOCX before appending rows.
    """

    if not file_exists(dropbox_path):
        return None

    return download_file(dropbox_path, local_path)


def file_exists(dropbox_path):
    """
    Checks if a Dropbox file or folder exists.
    """

    dbx = get_dropbox_client()

    try:
        dbx.files_get_metadata(dropbox_path)
        return True
    except Exception:
        return False


def move_file(from_path, to_path):
    """
    Moves a file inside Dropbox.
    Used to move processed PDFs from incoming_pdfs to processed_pdfs.
    """

    dbx = get_dropbox_client()

    try:
        result = dbx.files_move_v2(
            from_path,
            to_path,
            autorename=True
        )

        moved_path = result.metadata.path_display
        print(f"Moved Dropbox file: {from_path} -> {moved_path}")
        return moved_path

    except Exception as e:
        print(f"Failed to move Dropbox file: {e}")
        raise