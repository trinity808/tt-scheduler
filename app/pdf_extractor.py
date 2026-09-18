import pdfplumber
import re
from datetime import datetime


MONTHS = (
    "January|February|March|April|May|June|July|August|September|October|November|December"
)

MAX_PDF_PAGES_TO_READ = 4


def clean_ordinal_date(date_text):
    return re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", date_text)


def parse_date(date_text):
    if not date_text or date_text == "Unknown":
        return None

    cleaned = clean_ordinal_date(date_text)

    try:
        return datetime.strptime(cleaned, "%B %d, %Y")
    except ValueError:
        return None


def format_schedule_filename_date(date_text):
    cleaned = clean_ordinal_date(date_text)
    return cleaned.replace(",", "").replace(" ", "_")


def calculate_age(dob_text, reference_date_text):
    dob_dt = parse_date(dob_text)
    ref_dt = parse_date(reference_date_text)

    if not dob_dt or not ref_dt:
        return None

    age = ref_dt.year - dob_dt.year

    if (ref_dt.month, ref_dt.day) < (dob_dt.month, dob_dt.day):
        age -= 1

    return age


def extract_page_texts(file_path, max_pages=MAX_PDF_PAGES_TO_READ):
    page_texts = []

    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages[:max_pages]:
            page_texts.append(page.extract_text() or "")

    return page_texts


def format_doctor_short_name(doctor_name):
    if not doctor_name or doctor_name == "Unknown Doctor":
        return "Unknown Doctor"

    cleaned = doctor_name.split(",")[0].strip()
    cleaned = re.sub(r"\b(DR|DOCTOR)\.?\b", "", cleaned, flags=re.IGNORECASE)
    name_parts = re.findall(r"[A-Za-z]+", cleaned)

    if not name_parts:
        return "Unknown Doctor"

    last_name = name_parts[-1]
    return f"Dr. {last_name[0].upper()}"


def extract_doctor_from_page1(page1_text):
    lines = [line.strip() for line in page1_text.split("\n") if line.strip()]

    for i, line in enumerate(lines):
        if "TRINITY TREE" in line.upper() and i + 1 < len(lines):
            return format_doctor_short_name(lines[i + 1].strip())

    return "Unknown Doctor"


def extract_patient_name(page2_text, page3_text):
    lines = [line.strip() for line in page3_text.split("\n") if line.strip()]

    for i, line in enumerate(lines):
        if line.startswith("RE:"):
            possible_name = line.replace("RE:", "").strip()

            if possible_name:
                return possible_name

            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()

                if (
                    next_line
                    and not next_line.startswith("DOB:")
                    and not next_line.startswith("Applicant:")
                    and not next_line.startswith("Authorization")
                    and not next_line.startswith("TRINITY TREE")
                ):
                    return next_line

    page2_lines = [line.strip() for line in page2_text.split("\n") if line.strip()]

    for i, line in enumerate(page2_lines):
        if "CLAIMANT:" in line:
            possible_name = line.replace("CLAIMANT:", "").strip()
            name_parts = []

            if possible_name and "CASE NUMBER" not in possible_name:
                name_parts.append(possible_name)

            for next_line in page2_lines[i + 1:]:
                if (
                    "CASE NUMBER" in next_line
                    or "AUTHORIZATION" in next_line
                    or "FISCAL ID" in next_line
                    or re.match(rf"({MONTHS})\s+\d{{1,2}},\s+\d{{4}}", next_line)
                ):
                    break

                if (
                    next_line
                    and "CASE NUMBER" not in next_line
                    and "AUTHORIZATION" not in next_line
                    and "FISCAL ID" not in next_line
                ):
                    name_parts.append(next_line)

            if name_parts:
                return " ".join(name_parts)

    return "Unknown"


def extract_case_id(page2_text, page3_text):
    match = re.search(r"CASE NUMBER:\s*(\d+)", page2_text)
    if match:
        return match.group(1)

    match = re.search(r"Case ID:\s*(\d+)", page3_text)
    if match:
        return match.group(1)

    return "UNKNOWN_CASE"


def extract_authorization(page2_text, page3_text):
    match = re.search(r"AUTHORIZATION #:\s*(\d+)", page2_text)
    if match:
        return match.group(1)

    match = re.search(r"Authorization #:\s*(\d+)", page3_text)
    if match:
        return match.group(1)

    return "Unknown"


def extract_dob(text):
    match = re.search(
        rf"DOB:\s*(({MONTHS})\s+\d{{1,2}},\s+\d{{4}})",
        text
    )

    if match:
        return match.group(1)

    return "Unknown"


def format_phone_number(digits):
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def extract_claimant_contact_section(page4_text):
    lines = page4_text.splitlines()
    start_idx = None
    end_idx = len(lines)

    for idx, line in enumerate(lines):
        if "CLAIMANT INFORMATION" in line.upper():
            start_idx = idx
            break

    if start_idx is None:
        return ""

    for idx in range(start_idx + 1, len(lines)):
        upper_line = lines[idx].upper()

        if (
            "PLEASE EVALUATE" in upper_line
            or "SPECIAL INSTRUCTIONS" in upper_line
            or "WHAT YOU NEED TO DO NEXT" in upper_line
        ):
            end_idx = idx
            break

    return "\n".join(lines[start_idx:end_idx])


def extract_phone(page4_text):
    """
    Extracts the claimant phone number from page 4.
    The wanted number is in the Claimant Information block under the address.
    """

    ignored_digits = {
        "6027717100",
        "8003520409",
        "5206382035",
        "8665024266",
        "6234445508",
        "6234445539",
    }

    phone_pattern = re.compile(
        r"(?:\(\d{3}\)\s*|\b\d{3}[-.\s]?)\d{3}[-.\s]?\d{4}\b"
    )

    ignored_line_markers = (
        "LOCAL",
        "TOLL FREE",
        "CALL THIS OFFICE",
        "FAX",
        "TRINITY TREE",
        "DISABILITY DETERMINATION",
    )

    claimant_section = extract_claimant_contact_section(page4_text)

    for line in claimant_section.splitlines():
        upper_line = line.upper()

        if any(marker in upper_line for marker in ignored_line_markers):
            continue

        phone_matches = phone_pattern.findall(line)

        for phone in phone_matches:
            digits = re.sub(r"\D", "", phone)

            if len(digits) == 10 and digits not in ignored_digits:
                return format_phone_number(digits)

    return "Unknown"


def extract_total(page2_text):
    match = re.search(r"TOTAL\s+\$(\d+\.\d+)", page2_text)
    return match.group(1) if match else "Unknown"


def extract_allegations(page2_text):
    match = re.search(
        r"Allegations:(.*?)(YOU MUST CALL|IF THE CLAIMANT|I certify|$)",
        page2_text,
        re.DOTALL
    )

    if match:
        return match.group(1).strip()

    return "Not Available"


def extract_services(page2_text):
    services = []
    lines = [line.strip() for line in page2_text.split("\n") if line.strip()]

    i = 0
    while i < len(lines):
        line = lines[i]

        service_match = re.match(
            rf"(({MONTHS})\s+\d{{1,2}}(?:st|nd|rd|th)?,\s+\d{{4}})\s+"
            r"(\d{5}[\w\-]*)\s+"
            r"(.+?)\s+\$(\d+\.\d+)",
            line
        )

        if service_match:
            service_date = service_match.group(1)
            code = service_match.group(3)
            desc = service_match.group(4).strip()
            amount = service_match.group(5)

            service_time = "Unknown"

            if i + 1 < len(lines):
                next_line = lines[i + 1]

                time_match = re.search(
                    r"(\d{1,2}:\d{2}\s*(?:AM|PM)\s*MST)(.*)",
                    next_line
                )

                if time_match:
                    service_time = time_match.group(1).strip()

                    leftover = time_match.group(2).strip()
                    if leftover:
                        desc = f"{desc} {leftover}"

                    i += 1

            services.append({
                "date": service_date,
                "time": service_time,
                "code": code,
                "desc": desc,
                "amount": amount
            })

        i += 1

    return services

def extract_examiner_initials(text):
    """
    Extracts examiner initials from text like:
    Examiner: Shanelle Wolf LEX: WAS8 Site Code: S03 LUN: U51

    Returns:
    SW
    """
    patterns = [
        r"\bExaminer:\s*([A-Za-z][A-Za-z'.-]+)\s+([A-Za-z][A-Za-z'.-]+)",
        r"\bDisability Examiner:\s*([A-Za-z][A-Za-z'.-]+)\s+([A-Za-z][A-Za-z'.-]+)",
        r"\bDE:\s*([A-Za-z][A-Za-z'.-]+)\s+([A-Za-z][A-Za-z'.-]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            first = match.group(1)
            last = match.group(2)
            return f"{first[0]}{last[0]}".upper()

    initials_match = re.search(r"\bDE:\s*([A-Z]{2,4})\b", text)
    if initials_match:
        return initials_match.group(1)

    initials_match = re.search(r"\bExaminer:\s*([A-Z]{2,4})\b", text)
    if initials_match:
        return initials_match.group(1)


    return "Unknown"

def extract_schedule_data(file_path, adult_age_cutoff=18):
    page_texts = extract_page_texts(file_path, max_pages=MAX_PDF_PAGES_TO_READ)

    page1_text = page_texts[0] if len(page_texts) >= 1 else ""
    page2_text = page_texts[1] if len(page_texts) >= 2 else ""
    page3_text = page_texts[2] if len(page_texts) >= 3 else ""
    page4_text = page_texts[3] if len(page_texts) >= 4 else ""
    first_pages_text = "\n".join(page_texts)

    doctor_assigned = extract_doctor_from_page1(page1_text)
    examiner = extract_examiner_initials(first_pages_text)
    case_id = extract_case_id(page2_text, page3_text)
    authorization = extract_authorization(page2_text, page3_text)
    patient_name = extract_patient_name(page2_text, page3_text)
    dob = extract_dob(first_pages_text)
    phone = extract_phone(page4_text)
    services = extract_services(page2_text)
    total = extract_total(page2_text)
    allegations = extract_allegations(page2_text)

    if services:
        schedule_date = services[0]["date"]
        schedule_time = services[0]["time"]
    else:
        schedule_date = "Unknown"
        schedule_time = "Unknown"

    age = calculate_age(dob, schedule_date)

    if age is not None and age >= adult_age_cutoff:
        adult = "X"
        child = ""
    elif age is not None:
        adult = ""
        child = "X"
    else:
        adult = ""
        child = ""

    services_requested = "\n____________________\n".join(
        [
            f"{idx}. {service['code']} - {service['desc']}"
            for idx, service in enumerate(services, start=1)
        ]
    )

    return {
        "case_id": case_id,
        "authorization": authorization,
        "patient_name": patient_name,
        "doctor_assigned": doctor_assigned,
        "dob": dob,
        "phone": phone,
        "age": age,
        "adult": adult,
        "child": child,
        "schedule_date": schedule_date,
        "schedule_time": schedule_time,
        "schedule_file_date": format_schedule_filename_date(schedule_date),
        "services": services,
        "services_requested": services_requested,
        "total": total,
        "allegations": allegations,
        "examiner": examiner
    }
