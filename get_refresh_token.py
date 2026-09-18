import os
from dotenv import load_dotenv
from dropbox.oauth import DropboxOAuth2FlowNoRedirect

load_dotenv()

app_key = os.getenv("DROPBOX_APP_KEY")
app_secret = os.getenv("DROPBOX_APP_SECRET")

if not app_key:
    raise ValueError("DROPBOX_APP_KEY not found in .env")

if not app_secret:
    raise ValueError("DROPBOX_APP_SECRET not found in .env")

auth_flow = DropboxOAuth2FlowNoRedirect(
    app_key,
    app_secret,
    token_access_type="offline"
)

authorize_url = auth_flow.start()

print("\n1. Open this URL in your browser:")
print(authorize_url)

print("\n2. Click Allow")
print("3. Copy the authorization code Dropbox gives you\n")

auth_code = input("Paste authorization code here: ").strip()

oauth_result = auth_flow.finish(auth_code)

print("\nRefresh token:")
print(oauth_result.refresh_token)

print("\nCopy this line into your .env:")
print(f"DROPBOX_REFRESH_TOKEN={oauth_result.refresh_token}")