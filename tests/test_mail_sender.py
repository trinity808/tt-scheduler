"""
Tests for app/mail_sender.py: reading the Microsoft settings from .env, getting a token,
and sending one email. Microsoft is never contacted: every request goes to a stand-in
function that records what it was given and returns what the test says Microsoft would.

The values here are made up and unique, so a test can check that none of them (the
secret, the sender's address, a provider's address) ends up in an error message.

Run from the project root:
    python -m pytest tests/test_mail_sender.py -v
"""

import pytest
import requests

from app.mail_sender import (
    GRAPH_SCOPE,
    TIMEOUT_SECONDS,
    MailError,
    MailSettings,
    environment,
    get_access_token,
    read_mail_settings,
    send_email,
    staff_cc_emails,
)


SECRET = "secret-value-5521-do-not-print"
SENDER = "support.sender.3300@example.test"
PROVIDER = "doe.provider.4471@example.test"
DIRECTOR = "director.copy.7310@example.test"
TOKEN = "token-value-9917-do-not-print"

ENV = {
    "MICROSOFT_TENANT_ID": "tenant-0000",
    "MICROSOFT_CLIENT_ID": "client-0000",
    "MICROSOFT_CLIENT_SECRET": SECRET,
    "MICROSOFT_SENDER_EMAIL": SENDER,
}


class Reply:
    """What requests.post gives back."""

    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        if self.payload is None:
            raise ValueError("not json")
        return self.payload


class Web:
    """Stands in for requests.post: records each call and answers with `reply`."""

    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        return self.reply


def settings():
    return read_mail_settings(ENV)


def nothing_secret_in(text):
    for value in (SECRET, SENDER, PROVIDER, DIRECTOR, TOKEN):
        assert value not in text, f"{value} reached the message"


# --- The settings -----------------------------------------------------------------


def test_the_four_settings_are_read_from_the_environment():
    s = settings()
    assert (s.tenant_id, s.client_id, s.client_secret, s.sender) == ("tenant-0000", "client-0000", SECRET, SENDER)


def test_settings_are_trimmed():
    s = read_mail_settings({k: f"  {v}\n" for k, v in ENV.items()})
    assert s.client_secret == SECRET and s.sender == SENDER


@pytest.mark.parametrize("name", list(ENV))
def test_a_missing_or_blank_setting_is_named(name):
    for broken in ({k: v for k, v in ENV.items() if k != name}, {**ENV, name: "   "}):
        with pytest.raises(MailError) as caught:
            read_mail_settings(broken)
        assert name in str(caught.value)
        nothing_secret_in(str(caught.value))


def test_every_missing_setting_is_named_at_once():
    with pytest.raises(MailError) as caught:
        read_mail_settings({})
    for name in ENV:
        assert name in str(caught.value)


def test_a_sender_that_is_not_an_address_is_refused_without_repeating_it():
    with pytest.raises(MailError, match="MICROSOFT_SENDER_EMAIL"):
        read_mail_settings({**ENV, "MICROSOFT_SENDER_EMAIL": "not-an-address-7741"})
    with pytest.raises(MailError) as caught:
        read_mail_settings({**ENV, "MICROSOFT_SENDER_EMAIL": "not-an-address-7741"})
    assert "not-an-address-7741" not in str(caught.value)


def test_the_secret_and_the_sender_stay_out_of_repr():
    shown = repr(settings())
    nothing_secret_in(shown)
    assert "tenant-0000" in shown


def test_without_an_environment_given_the_real_one_is_used_after_loading_dotenv(monkeypatch):
    loaded = []
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: loaded.append(True))
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "from-the-real-environment")
    assert environment()["MICROSOFT_TENANT_ID"] == "from-the-real-environment"
    assert loaded == [True]


def test_a_given_environment_is_used_as_it_is_and_dotenv_is_not_loaded(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: pytest.fail(".env was loaded"))
    assert environment({"a": "b"}) == {"a": "b"}


def test_an_empty_environment_given_is_used_as_it_is_and_never_replaced_by_the_real_one(monkeypatch):
    for name, value in {**ENV, "STAFF_CC_EMAILS": DIRECTOR}.items():
        monkeypatch.setenv(name, value)  # what a real machine's .env would provide
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: pytest.fail(".env was loaded"))
    assert environment({}) == {}
    with pytest.raises(MailError, match="MICROSOFT_TENANT_ID"):
        read_mail_settings({})
    assert staff_cc_emails({}) == []


def test_the_staff_copy_is_a_comma_separated_list():
    assert staff_cc_emails({"STAFF_CC_EMAILS": f" {DIRECTOR} , other.copy.1@example.test,,"}) == [
        DIRECTOR,
        "other.copy.1@example.test",
    ]


@pytest.mark.parametrize("env", [{}, {"STAFF_CC_EMAILS": ""}, {"STAFF_CC_EMAILS": "  "}, {"STAFF_CC_EMAILS": " , "}])
def test_no_staff_copy_is_an_empty_list(env):
    assert staff_cc_emails(env) == []


# --- Getting a token --------------------------------------------------------------


def test_the_token_request_goes_to_the_tenants_endpoint_with_the_app_credentials():
    web = Web(Reply(200, {"access_token": TOKEN}))
    assert get_access_token(settings(), web) == TOKEN
    ((url, kwargs),) = web.calls
    assert url == "https://login.microsoftonline.com/tenant-0000/oauth2/v2.0/token"
    assert kwargs["data"] == {
        "grant_type": "client_credentials",
        "client_id": "client-0000",
        "client_secret": SECRET,
        "scope": GRAPH_SCOPE,
    }
    assert kwargs["timeout"] == TIMEOUT_SECONDS


def test_the_scope_is_graphs_default_scope():
    assert GRAPH_SCOPE == "https://graph.microsoft.com/.default"


def test_a_refused_sign_in_gives_the_code_and_the_first_line_only():
    reply = Reply(
        401,
        {
            "error": "invalid_client",
            "error_description": "AADSTS7000215: Invalid client secret provided.\r\nTrace ID: abc\nCorrelation ID: def",
        },
    )
    with pytest.raises(MailError) as caught:
        get_access_token(settings(), Web(reply))
    message = str(caught.value)
    assert "HTTP 401" in message and "invalid_client" in message and "AADSTS7000215" in message
    assert "Trace ID" not in message and "Correlation" not in message
    nothing_secret_in(message)


def test_a_reply_that_is_not_json_is_still_a_clear_error():
    with pytest.raises(MailError, match=r"HTTP 502, no reason given"):
        get_access_token(settings(), Web(Reply(502)))


def test_a_200_without_a_token_is_an_error():
    with pytest.raises(MailError, match="sign-in failed"):
        get_access_token(settings(), Web(Reply(200, {"token_type": "Bearer"})))


def test_a_token_with_a_failing_status_is_not_trusted():
    with pytest.raises(MailError):
        get_access_token(settings(), Web(Reply(500, {"access_token": TOKEN})))


def test_a_network_error_while_signing_in_names_only_its_type():
    web = Web(error=requests.ConnectionError(f"HTTPSConnectionPool(host='login', path='/{SENDER}')"))
    with pytest.raises(MailError) as caught:
        get_access_token(settings(), web)
    assert "ConnectionError" in str(caught.value)
    nothing_secret_in(str(caught.value))
    assert caught.value.__cause__ is None and caught.value.__suppress_context__  # no chained traceback


def test_an_unexpected_error_is_not_swallowed():
    with pytest.raises(RuntimeError):
        get_access_token(settings(), Web(error=RuntimeError("a bug")))


# --- Sending ----------------------------------------------------------------------


def send(web=None, cc=(), subject="A subject", body="A body"):
    web = web or Web(Reply(202))
    send_email(settings(), TOKEN, PROVIDER, subject, body, cc, web)
    return web


def test_a_send_posts_to_the_senders_mailbox_with_the_token():
    ((url, kwargs),) = send().calls
    assert url == "https://graph.microsoft.com/v1.0/users/support.sender.3300%40example.test/sendMail"
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    assert kwargs["timeout"] == TIMEOUT_SECONDS


def test_the_message_has_the_subject_a_plain_text_body_and_the_recipient():
    ((_, kwargs),) = send(subject="Check-in: report", body="Hello,\n\nText").calls
    message = kwargs["json"]["message"]
    assert message["subject"] == "Check-in: report"
    assert message["body"] == {"contentType": "Text", "content": "Hello,\n\nText"}
    assert message["toRecipients"] == [{"emailAddress": {"address": PROVIDER}}]


def test_a_copy_is_added_only_when_given():
    ((_, without),) = send().calls
    assert without["json"]["message"]["ccRecipients"] == []
    ((_, with_copy),) = send(cc=[DIRECTOR, "other.copy.1@example.test"]).calls
    assert with_copy["json"]["message"]["ccRecipients"] == [
        {"emailAddress": {"address": DIRECTOR}},
        {"emailAddress": {"address": "other.copy.1@example.test"}},
    ]


def test_a_copy_is_kept_in_the_senders_sent_folder():
    ((_, kwargs),) = send().calls
    assert kwargs["json"]["saveToSentItems"] is True


def test_accepted_means_nothing_is_raised_and_nothing_is_returned():
    assert send_email(settings(), TOKEN, PROVIDER, "s", "b", (), Web(Reply(202))) is None


@pytest.mark.parametrize("status", [200, 201, 400, 401, 403, 404, 429, 500, 503])
def test_anything_but_202_is_a_refusal_with_the_status_and_code_and_nothing_else(status):
    reply = Reply(
        status,
        {"error": {"code": "ErrorInvalidRecipients", "message": f"The address {PROVIDER} is not valid, {SECRET}"}},
    )
    with pytest.raises(MailError) as caught:
        send(Web(reply))
    message = str(caught.value)
    assert f"HTTP {status}" in message and "ErrorInvalidRecipients" in message
    nothing_secret_in(message)


def test_a_refusal_without_a_code_still_says_so():
    with pytest.raises(MailError, match="HTTP 500, no error code"):
        send(Web(Reply(500)))
    with pytest.raises(MailError, match="HTTP 500, no error code"):
        send(Web(Reply(500, {"error": "just a string"})))


def test_a_network_error_while_sending_names_only_its_type():
    web = Web(error=requests.Timeout(f"Read timed out on /users/{SENDER}/sendMail to {PROVIDER}"))
    with pytest.raises(MailError) as caught:
        send(web)
    assert "Timeout" in str(caught.value)
    nothing_secret_in(str(caught.value))
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_a_sender_with_odd_characters_is_encoded_in_the_url():
    odd = MailSettings("t", "c", SECRET, "first.last+tag@example.test")
    web = Web(Reply(202))
    send_email(odd, TOKEN, PROVIDER, "s", "b", (), web)
    assert "users/first.last%2Btag%40example.test/sendMail" in web.calls[0][0]


def test_sending_never_prints(capsys):
    send(cc=[DIRECTOR])
    with pytest.raises(MailError):
        send(Web(Reply(400)))
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""