"""
Sends one email through Microsoft Graph, as the support mailbox.

It signs in as the app itself (the "client credentials" flow, with the Mail.Send
application permission), so nobody has to be logged in. The four settings come
from .env, never from the repo:

    MICROSOFT_TENANT_ID, MICROSOFT_CLIENT_ID, MICROSOFT_CLIENT_SECRET,
    MICROSOFT_SENDER_EMAIL   (the mailbox the email is sent from)

and STAFF_CC_EMAILS (comma separated) is who gets a copy of the overdue notice.

Two plain web requests, made with `requests`: one to get a token, one to send.
Microsoft answers a send with 202, "accepted". That means it took the email, not
that it arrived. A bounce comes back to the sender mailbox's inbox.

Nothing here prints, and nothing here puts an address or the secret into an error.
Every MailError message is written to be logged as it is. A network error is
reported by its type only, because the text of one can contain the request's URL,
and the URL of a send contains the sender's address. It is raised `from None` so
the chained traceback doesn't carry it either.

There are no retries. A send that failed is reported and left to the next run,
since retrying a send that did go through would email someone twice.
"""

import os
from dataclasses import dataclass, field
from urllib.parse import quote

import requests


TENANT_ID = "MICROSOFT_TENANT_ID"
CLIENT_ID = "MICROSOFT_CLIENT_ID"
CLIENT_SECRET = "MICROSOFT_CLIENT_SECRET"
SENDER_EMAIL = "MICROSOFT_SENDER_EMAIL"
STAFF_CC = "STAFF_CC_EMAILS"

SETTING_NAMES = (TENANT_ID, CLIENT_ID, CLIENT_SECRET, SENDER_EMAIL)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
TIMEOUT_SECONDS = 30


class MailError(Exception):
    """Something went wrong signing in or sending. The message is safe to log."""


@dataclass
class MailSettings:
    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)
    sender: str = field(repr=False)


def environment(environ=None):
    """`environ` for tests; left out, .env is loaded and the real environment is used."""
    if environ is not None:
        return environ
    from dotenv import load_dotenv

    load_dotenv()
    return os.environ


def read_mail_settings(environ=None):
    """The four Microsoft settings. A missing one is named, never shown."""
    env = environment(environ)
    values = {name: (env.get(name) or "").strip() for name in SETTING_NAMES}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise MailError("These settings are missing from .env: " + ", ".join(missing))
    if "@" not in values[SENDER_EMAIL]:
        raise MailError(f"{SENDER_EMAIL} in .env isn't an email address")
    return MailSettings(
        tenant_id=values[TENANT_ID],
        client_id=values[CLIENT_ID],
        client_secret=values[CLIENT_SECRET],
        sender=values[SENDER_EMAIL],
    )


def staff_cc_emails(environ=None):
    """The addresses in STAFF_CC_EMAILS, in order. Empty if it isn't set."""
    raw = (environment(environ).get(STAFF_CC) or "").strip()
    return [email.strip() for email in raw.split(",") if email.strip()]


def _json(response):
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def get_access_token(settings, post=None):
    """A Graph access token for the app. `post` is for tests; left out, requests.post."""
    post = post or requests.post
    try:
        response = post(
            TOKEN_URL.format(tenant=quote(settings.tenant_id, safe="")),
            data={
                "grant_type": "client_credentials",
                "client_id": settings.client_id,
                "client_secret": settings.client_secret,
                "scope": GRAPH_SCOPE,
            },
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise MailError(f"Could not reach Microsoft to sign in ({type(error).__name__})") from None

    payload = _json(response)
    token = payload.get("access_token")
    if response.status_code == 200 and token:
        return token

    reason = str(payload.get("error") or "no reason given")
    description = str(payload.get("error_description") or "").strip().split("\n")[0]
    if description:
        reason += f": {description[:200]}"
    raise MailError(f"Microsoft sign-in failed (HTTP {response.status_code}, {reason})")


def _recipient(address):
    return {"emailAddress": {"address": address}}


def send_email(settings, token, to, subject, body, cc=(), post=None):
    """
    Sends a plain-text email from the sender mailbox, with a copy saved to its Sent
    folder. Returns None once Microsoft has accepted it; raises MailError otherwise.
    """
    post = post or requests.post
    message = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [_recipient(to)],
            "ccRecipients": [_recipient(address) for address in cc],
        },
        "saveToSentItems": True,
    }
    try:
        response = post(
            SEND_URL.format(sender=quote(settings.sender, safe="")),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=message,
            timeout=TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        raise MailError(f"Could not reach Microsoft to send ({type(error).__name__})") from None

    if response.status_code == 202:
        return

    error = _json(response).get("error")
    code = error.get("code") if isinstance(error, dict) else None
    raise MailError(f"Microsoft refused the email (HTTP {response.status_code}, {code or 'no error code'})")