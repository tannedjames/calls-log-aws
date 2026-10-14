# Google Sheets sync — setup

This makes the OCR tool also push each processed day into the right Google
Sheet: it creates the `yyyy-mm-dd` worksheet from the `📄 Mẫu` template, writes
the sessions, and adds the month-sheet `Mở->` hyperlink. The local `.xlsx` is
still produced either way.

The Python tool stays credential-free. It POSTs a small JSON payload to an
Apps Script **Web App** that does the spreadsheet work under your own account.

## 1. Create the Web App

> **Already deployed?** Then do not follow this section: the live project is
> the source of truth. Patch it in place in the editor and redeploy a new
> version of the existing deployment (see "Maintenance" below).

1. Go to https://script.google.com → **New project**.
2. Delete the stub code and paste the contents of `WebAppSync.gs`.
3. Add a second script file named `Employees` and paste your private
   `Employees.gs` into it (gitignored; create it from `Employees.example.gs`).
   Do not add `Employees.example.gs` itself — it would override the real map.
4. Set `SECRET` (top of `WebAppSync.gs`) to a private string of your choice.
5. In `NAME_TO_ID`, map each person to their spreadsheet ID. The key is the
   lower-cased, accent-stripped folder-name prefix (e.g. `'nguyen van a'`),
   matched by prefix — never add a key that is a prefix of another person's.
6. **Deploy → New deployment → Web app**:
   - *Execute as:* **Me**
   - *Who has access:* **Anyone**  (required so the local tool can POST; the
     `SECRET` check is what actually protects it)
7. Authorize when prompted, then copy the **Web app URL** (ends in `/exec`).

> Security note: "Anyone" means the URL is reachable without a Google login,
> but every request must carry the matching `SECRET` or it's rejected. Treat
> the URL + secret like a password. Re-deploy to rotate.

## 2. Point the tool at it

Put the URL and secret in `secrets_local.py` (gitignored — copy it from
`secrets_local.example.py`). `config.py` reads them from there; never type them
into `config.py` or any other tracked file.

```python
SHEETS_WEBAPP_URL = "https://script.google.com/macros/s/AKfyc.../exec"
SHEETS_SYNC_SECRET = "the same SECRET you set in WebAppSync.gs"
```

Sync is switched on/off with `sheets_sync_enabled` in `config.py`.

Verify the URL is live by opening it in a browser — you should see
`{"ok":true,"service":"call-log sheets sync",...}`.

## 3. Use it

Run the tool exactly as before. In batch mode each subfolder named
`<Name> & yyyy-mm-dd` is OCR'd to xlsx and then synced:

```powershell
.\.venv\Scripts\python.exe main.py --batch --images ./input_images --template ./sprs.xlsx --output ./output
```

Behavior notes:
- The local `.xlsx` is always written first; sync is an add-on.
- If sync fails (bad secret, no network, unknown name), the error is logged
  and the **source folder is NOT archived**, so you can fix and re-run.
- Re-running a date that already has a tab overwrites B7:D22 / B2 / B3 in that
  tab rather than creating a duplicate.
- Sessions beyond the template's 16-row capacity are capped (the response
  reports `"capped": true`); raise the template rows if you need more.

## What gets written

Mirrors `excel_writer.py`:
- `B7:D22` — one row per session: start and end (numeric times, so the `;`
  formula locale cannot break them), span (`=C-B`)
- `B2` — `=SUM(D7:D22)` total
- `B3` — `=B7` first start
- column A numbering and `B4` (attendance note) are left untouched
- day tab `B1` → link back to that month's tab; month tab `B1` → link to `🏠 Tổng quan`
- month sheet: the date's column-B cell → `=HYPERLINK(".../#gid=<new>";"Mở->")`

## Maintenance

- Saving in the editor does **not** change the live endpoint. After any edit:
  Deploy → Manage deployments → ✏️ edit → Version: **New version** → Deploy.
- Never use **New deployment** for an update: it creates a different `/exec`
  URL and the tool keeps talking to the old version.
- Formulas in these spreadsheets use `;` between arguments, not `,`.
- Adding a person: one `NAME_TO_ID` key in the live project (and in your local
  `Employees.gs`), then redeploy as above. No Python change is needed.
- More detail: [`docs/TECHNICAL.md`](../docs/TECHNICAL.md) §7.
