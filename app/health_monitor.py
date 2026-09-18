import logging
import os
import smtplib
import socket
import traceback
from datetime import datetime
from email.message import EmailMessage
from logging.handlers import RotatingFileHandler

from dotenv import load_dotenv


load_dotenv()


LOGGER_NAME = "trinity_scheduler"


def get_logger():
    return logging.getLogger(LOGGER_NAME)


def setup_logging(config=None):
    config = config or {}
    health_config = config.get("health_check", {})
    log_folder = health_config.get("log_folder", "logs")

    os.makedirs(log_folder, exist_ok=True)

    logger = get_logger()
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    scheduler_log = os.path.join(log_folder, "scheduler.log")
    error_log = os.path.join(log_folder, "scheduler_error.log")

    info_handler = RotatingFileHandler(
        scheduler_log,
        maxBytes=2_000_000,
        backupCount=5
    )
    info_handler.setLevel(logging.INFO)
    info_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        error_log,
        maxBytes=2_000_000,
        backupCount=5
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    logger.addHandler(info_handler)
    logger.addHandler(error_handler)

    return logger


def run_startup_health_check(config):
    logger = get_logger()
    problems = []
    warnings = []

    required_config_keys = [
        "base_folder",
        "dropbox_input_folder",
        "dropbox_processed_pdf_folder",
        "dropbox_schedule_folder",
    ]

    for key in required_config_keys:
        if not config.get(key):
            problems.append(f"Missing config/settings.json value: {key}")

    for key in ("adult_template_path", "child_template_path"):
        template_path = config.get(key)
        if template_path and not os.path.exists(template_path):
            problems.append(f"Template file not found: {template_path}")

    generated_docs_folder = config.get("generated_docs_folder")
    if generated_docs_folder:
        try:
            os.makedirs(generated_docs_folder, exist_ok=True)
        except OSError as exc:
            problems.append(
                f"Cannot create generated docs folder {generated_docs_folder}: {exc}"
            )

    health_config = config.get("health_check", {})
    if health_config.get("email_notifications_enabled"):
        missing_email_values = get_missing_email_settings()
        if missing_email_values:
            warnings.append(
                "Email notifications are enabled but these .env values are missing: "
                + ", ".join(missing_email_values)
            )

    for warning in warnings:
        logger.warning(warning)

    if problems:
        raise RuntimeError("Startup health check failed: " + " | ".join(problems))

    logger.info("Startup health check passed.")


def get_missing_email_settings():
    required_settings = [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_FROM_EMAIL",
        "ALERT_TO_EMAIL",
    ]

    return [name for name in required_settings if not os.getenv(name)]


def email_notifications_enabled(config=None):
    if config is None:
        return bool(os.getenv("SMTP_HOST") and os.getenv("ALERT_TO_EMAIL"))

    health_config = config.get("health_check", {})

    return bool(health_config.get("email_notifications_enabled"))


def notify_error(subject, message, config=None, exc_info=None):
    logger = get_logger()

    if not email_notifications_enabled(config):
        logger.info("Email notification skipped because it is disabled.")
        return False

    missing_settings = get_missing_email_settings()
    if missing_settings:
        logger.error(
            "Email notification skipped. Missing .env values: %s",
            ", ".join(missing_settings)
        )
        return False

    config = config or {}
    health_config = config.get("health_check", {})
    subject_prefix = health_config.get(
        "email_subject_prefix",
        "[Trinity Scheduler]"
    )

    full_subject = f"{subject_prefix} {subject}"
    body = build_error_email_body(message, exc_info)

    try:
        send_email(full_subject, body)
        logger.info("Error notification email sent: %s", full_subject)
        return True
    except Exception:
        logger.exception("Failed to send error notification email.")
        return False


def build_error_email_body(message, exc_info=None):
    lines = [
        message,
        "",
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Host: {socket.gethostname()}",
        f"Working directory: {os.getcwd()}",
    ]

    if exc_info:
        lines.extend([
            "",
            "Traceback:",
            "".join(traceback.format_exception(*exc_info)).strip(),
        ])

    return "\n".join(lines)


def send_email(subject, body):
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    from_email = os.getenv("SMTP_FROM_EMAIL")
    to_email = os.getenv("ALERT_TO_EMAIL")
    use_tls = os.getenv("SMTP_USE_TLS", "true").lower() != "false"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = to_email
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        if use_tls:
            smtp.starttls()

        if username and password:
            smtp.login(username, password)

        smtp.send_message(message)
