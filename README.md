Requirements
  Supported OS
  Windows 10 / 11
  macOS 12 or newer
  Linux (Ubuntu / Fedora)
  Python Version
  Python 3.9 or newer (64-bit)

----------------------------------------------------------------------------
Installation Steps
1. Clone or Download the Repository

If you have Git:

git clone https://github.com/yourusername/google-drive-mirror.git

cd google-drive-mirror

Or just download the ZIP, then extract it anywhere on your computer.
----------------------------------------------------------------------------
2. Install Required Libraries

Open a terminal (or PowerShell on Windows) in the project folder, then run:

pip install -r requirements.txt

If you don’t have a requirements.txt, create one with the following:

google-api-python-client
google-auth-httplib2
google-auth-oauthlib
customtkinter
-------------------------------------------------------------------------------
3. Get Your Google API Credentials

To access Google Drive, you need a Google OAuth client:

Go to: https://console.cloud.google.com/

Click "Select a project" → "New Project"

In the sidebar, go to APIs & Services → Credentials

Click “+ Create Credentials” → “OAuth client ID”

Choose Desktop App, name it “Drive Mirror”

Download the file — it will be called something like credentials.json

Move it into your project folder.
------------------------------------------------------------------------------
4. Run the App

To start the GUI:

python gui_ctk.py

You’ll see the macOS-like window open.

Select your credentials.json

Choose an output folder for downloads

Click Start Mirror

Your browser will open — sign in with your Google account and approve access

Downloads will begin automatically (you’ll see progress + file logs)

🪄 What Happens Next

All files and folders are mirrored under your chosen output directory

Google Docs, Sheets, and Slides are automatically exported into Office formats

Files are skipped if they already exist locally

You can stop and rerun safely — it resumes from where it left off
-------------------------------------------------------------------------------
Troubleshooting

Issue: ModuleNotFoundError: No module named 'customtkinter'

Fix:
Run pip install customtkinter

Issue: The Authentication browser doesn’t open

Fix:
Check that you’re using a desktop environment (not remote SSH). Alternatively, you can manually copy the provided link into your browser.

Issue: Rate limit / Quota errors

Fix:
You may need to enable billing or limit concurrent downloads — Google enforces API quotas.
-------------------------------------------------------------------------------------------------------------------------
🧾 Project Structure
google-drive-mirror/
│
├── main.py             # Core logic (Drive API, async multiprocessing)
├── gui_ctk.py          # GUI using customtkinter
├── credentials.json    # OAuth credentials (not included by default)
├── token.pickle        # Auto-generated login token after first run
└── requirements.txt    # Python dependencies
-------------------------------------------------------------------------------------------------------------------------
Developed With
Python 3.11
Google Drive API v3
customtkinter 
asyncio + multiprocessing
--------------------------------------------------------------------------------
Security
OAuth tokens are stored locally in token.pickle (secure, not shared).

The app only uses read-only access (https://www.googleapis.com/auth/drive.readonly).

You can revoke access anytime at:

https://myaccount.google.com/permissions
----------------------------------------------------------------------------------
Credits
Created by Undre
Contributions welcome — feel free to submit PRs for new features
