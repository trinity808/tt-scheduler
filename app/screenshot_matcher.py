import re


def normalize_name(value):
    """
    Converts a name into a lowercase alphanumeric comparison key.

    Examples:
        KhannaAnand   -> khannaanand
        KHANNA ANAND  -> khannaanand
        Khanna-Anand  -> khannaanand
        Khanna_Anand  -> khannaanand
        Khanna, Anand -> khannaanand
    """

    value = str(value).strip().lower()

    # Remove everything except letters and numbers
    value = re.sub(
        r"[^a-z0-9]",
        "",
        value,
    )

    return value