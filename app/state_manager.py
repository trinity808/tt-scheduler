PROCESSED_FILE = "processed_dropbox_files.txt"


def load_processed_file_ids():
    try:
        with open(PROCESSED_FILE, "r") as f:
            return set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        return set()


def save_processed_file_id(file_id):
    processed = load_processed_file_ids()

    if file_id not in processed:
        with open(PROCESSED_FILE, "a") as f:
            f.write(file_id + "\n")