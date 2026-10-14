"""
Template for secrets_local.py — copy this file, rename it, fill in the values.

    copy secrets_local.example.py secrets_local.py     (Windows)
    cp   secrets_local.example.py secrets_local.py     (macOS/Linux)

secrets_local.py is gitignored. THIS file is committed, so keep it empty.

SHEETS_WEBAPP_URL
    The deployed Apps Script Web App /exec URL.
    Apps Script -> Triển khai / Deploy -> Manage deployments -> copy the URL.

SHEETS_SYNC_SECRET
    Must equal `var SECRET` at the top of the deployed WebAppSync.gs.
    If they differ, every sync fails with "bad secret".
"""

SHEETS_WEBAPP_URL = ""
SHEETS_SYNC_SECRET = ""
