#!/usr/bin/env python3
"""One-time local setup: mint an OAuth refresh token for Drive uploads.

Personal Gmail accounts can't grant service accounts storage quota (only
Google Workspace Shared Drives can) -- see jobhunt/deliver.py docstring.
Authenticating as your own account via OAuth sidesteps that entirely: uploads
count against your normal 15GB free quota and land in whatever folder you
already own, no folder-sharing step needed.

One-time Google Cloud Console setup (a few minutes):
  1. console.cloud.google.com -> select/create a project -> enable the Drive API
  2. APIs & Services -> Credentials -> Create Credentials -> OAuth client ID
  3. Application type: Desktop app -> download the client secret JSON

Then run this script locally (opens a browser for a one-time consent screen):
  pip install google-auth-oauthlib
  python gdrive_authorize.py /path/to/client_secret.json

It prints three values -- save them as GitHub repo secrets
(GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN) and delete the
downloaded client_secret.json (the printed values are everything CI needs).
"""
import sys

SCOPES = ["https://www.googleapis.com/auth/drive"]


def main():
    if len(sys.argv) != 2:
        print("usage: python gdrive_authorize.py /path/to/client_secret.json")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0)

    print("\nSave these as GitHub repo secrets (Settings -> Secrets and variables -> Actions):\n")
    print(f"GDRIVE_CLIENT_ID={creds.client_id}")
    print(f"GDRIVE_CLIENT_SECRET={creds.client_secret}")
    print(f"GDRIVE_REFRESH_TOKEN={creds.refresh_token}")
    print("\nThen delete the client_secret.json file -- it's no longer needed locally.")


if __name__ == "__main__":
    main()
