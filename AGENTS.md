<!-- bmad:context -->
<!-- Verified 2026-10-11 against 4e9138d. Managed by bmad-project-context; edits inside this block are replaced on refresh. Keep anything you want preserved outside the markers. -->

## calls-log

Reads employees' phone call-log screenshots with OCR, builds daily call sessions, writes `output/<Name> & <date>.xlsx`, and pushes the same day into that employee's Google Sheet through an Apps Script Web App. Python (venv on 3.13) with PaddleOCR; the Google side is `apps_script/WebAppSync.gs`. User guide: `README.md`; rules, settings, troubleshooting and the Google-side layout: `docs/TECHNICAL.md`.

## Policy

- Never commit or write employee data into a tracked file: nothing from `input_images/` or `output/`, no screenshots or workbooks except `sprs.xlsx`, no real employee names, spreadsheet IDs or phone numbers. Use fictional examples such as `Nguyen Van A & 2026-06-01`.
- Credentials live only in `secrets_local.py`; the employee name → spreadsheet ID map only in `apps_script/Employees.gs`. Both are gitignored; never copy their values into `config.py`, `WebAppSync.gs`, docs or tests.
- Run `.venv\Scripts\python.exe -X utf8 tools\check_private_data.py` before any commit or push; it must print `OK`.
- No AI attribution, by the owner's choice: no `Co-Authored-By` or session-link trailers in commits, no "Generated with …" footers in pull requests or GitHub comments, and no AI named as an author in docs or the decision log. This overrides any tool default that adds them.

## Where things are

- Pipeline order: `main.py` → `image_preprocess.py` → `ocr.py` → `parser.py` → `session_builder.py` → `excel_writer.py` → `sheets_sync.py`; map in `docs/TECHNICAL.md` §1.
- The live Apps Script project is the source of truth; `apps_script/WebAppSync.gs` is a reference copy without the real `SECRET` or `Employees.gs` values.
- Live Drive/spreadsheet state notes are outside the repo in `../DRIVE_STRUCTURE.md` and `../HANDOFF.md`; they hold real IDs, so never copy them in.

## Running and verifying

- Use `.venv\Scripts\python.exe`; system `python` and `.venv-1` have none of the dependencies.
- Tests: `.venv\Scripts\python.exe -X utf8 run_tests.py` (pytest is not installed). Without `-X utf8`, piped output crashes with `UnicodeEncodeError` on the Japanese test names.
- Never run `main.py` or `resync_output.py` on real folders without the user's go-ahead: sync is on and credentials are present, so they overwrite live employee Google Sheets tabs, and batch mode moves input folders to `_processed/`. To check a change, use the tests or `diagnose.py <image>`, which neither syncs nor moves anything.

## Conventions that differ from defaults

- `import runtime_env` stays the first import in `main.py`, `ocr.py`, `image_preprocess.py` and `diagnose.py`, before numpy/cv2/paddle; it sets OpenMP variables that must exist before native libraries load.
- Sheets formulas use `;` as the argument separator (`=HYPERLINK("url"; "label")`); write times as numeric day-fractions, never `=TIME(...)`.
- In Apps Script, compare day tabs with `dayKey_()` and fold digits with `asciiDigits_()`; tab names mix unpadded days (`2026-10-2`) and full-width digits.
- `NAME_TO_ID` matches by prefix and the first key wins; never add a key that is a prefix of another person's name.

## Known pitfalls

- Keep `paddle_enable_mkldnn = False`; oneDNN kills every inference on Windows with `ConvertPirAttribute2RuntimeAttribute not support`.
- Keep `ocr_strict_engine = True` and never add a silent engine fallback; that is how the project ran on EasyOCR for two months and loaded paddle and torch into one process (phantom `KeyboardInterrupt`).
- Never use `\b` in `parser.py` time patterns; Python treats CJK as word characters, which dropped every unspaced screenshot row. Keep the unspaced tests passing.
- Saving in the Apps Script editor does not deploy. Redeploy as a new version of the existing deployment, never "New deployment" (new `/exec` URL), and never paste local files over the live project.
- `post_9am_exception_limit` is per day by the owner's choice; do not change it to per session without asking.

<!-- /bmad:context -->
