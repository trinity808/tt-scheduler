import sys

import msal
import requests

GRAPH_URL = "https://graph.microsoft.com/v1.0"


def get_required_input(prompt):
    value = input(prompt).strip()

    if not value:
        print("ERROR: This value cannot be empty.")
        sys.exit(1)

    return value


def get_access_token(tenant_id, client_id):
    authority = f"https://login.microsoftonline.com/{tenant_id}"

    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=authority,
    )

    scopes = ["Mail.Send"]

    print()
    print("Starting Microsoft sign-in...")
    print()

    flow = app.initiate_device_flow(scopes=scopes)

    if "user_code" not in flow:
        print("ERROR: Could not start Microsoft sign-in.")
        print(flow)
        sys.exit(1)

    print(flow["message"])
    print()

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print()
        print("ERROR: Microsoft sign-in failed.")
        print("Error:", result.get("error"))
        print("Description:", result.get("error_description"))
        sys.exit(1)

    return result["access_token"], result.get("id_token_claims", {})


def send_test_email(access_token, recipient_email):
    url = f"{GRAPH_URL}/me/sendMail"

    message = {
        "message": {
            "subject": "TT Scheduler - Test Email",
            "body": {
                "contentType": "Text",
                "content": (
                    "This is a test email from the TT Scheduler "
                    "using Microsoft Graph delegated authentication."
                ),
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": recipient_email
                    }
                }
            ],
        },
        "saveToSentItems": True,
    }

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=message,
        timeout=30,
    )

    if response.status_code == 202:
        print()
        print("=" * 60)
        print("SUCCESS!")
        print("=" * 60)
        print("Microsoft Graph accepted the email for sending.")
        print(f"To: {recipient_email}")
        print()
        return

    print()
    print("ERROR: Microsoft Graph rejected the email.")
    print(f"HTTP status: {response.status_code}")
    print(response.text)
    sys.exit(1)


def main():
    print("=" * 60)
    print("TT Scheduler - Microsoft Graph Delegated Email Test")
    print("=" * 60)
    print()

    tenant_id = get_required_input(
        "Directory (tenant) ID: "
    )

    client_id = get_required_input(
        "Application (client) ID: "
    )

    recipient_email = get_required_input(
        "Test recipient email: "
    )

    access_token, claims = get_access_token(
        tenant_id,
        client_id,
    )

    signed_in_user = claims.get(
        "preferred_username",
        "signed-in Microsoft 365 account"
    )

    print()
    print(f"Signed in as: {signed_in_user}")
    print("Sending test email...")

    send_test_email(
        access_token,
        recipient_email,
    )


if __name__ == "__main__":
    main()