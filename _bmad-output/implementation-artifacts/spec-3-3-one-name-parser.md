---
title: 'Story 3.3: One name parser for everything'
type: 'feature'
created: '2026-10-15'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: '1b05081e272d67006f13b41d4bb647d75f6835ee'
context: ['{project-root}/_bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Several places will read folder names: upload validation, the runner, resync and the import. Today `main.parse_name_date` and `resync_output.FILENAME_RE` already disagree. Resync skips the unpadded dates the PC writes (`2026-6-1`), and treats NFC and NFD spellings of one name as two people. Resync also reads the workbook's active sheet, not the one `excel_writer` writes.

**Approach:** One stdlib-only module, `cloud/naming.py`, does all name parsing, validation, normalisation, allowlist matching and S3 key building (AD-8). `resync_output.py` uses it.

**Decisions** (the Owner said to work uninterrupted; recorded here):
- `parse(folder)`: NFC, then `main.parse_name_date` (imported lazily, about 0.5 s and no paddle or OpenCV), then a calendar check. Returns `PersonDay(name, date)`, whose `key` is `"<name> & <yyyy-mm-dd>"`.
- `norm()` copies `norm_()` from `WebAppSync.gs` exactly:
  - JavaScript's `\s` and `trim()` set, not Python's (U+0085 and U+001C–U+001F aren't JS whitespace; U+FEFF is);
  - drops only U+0300–U+036F.
  - Its test expectations come from running the real `norm_()` under Node.
- `matching_prefix(name, keys)` copies `matchId_`: the folded name equals a key or starts with it, keys compared as stored, first key wins. `prefixes_from_json` refuses anything but a JSON array of non-empty strings: an empty key would match every name.
- `parse_output_stem(stem)`: `<name> & <date>` with an optional `(n)` and nothing after it. Correctly named files are read exactly as before. Unpadded dates are now read; impossible dates and trailing text are skipped.
- `resync_output.read_sessions` reads `CONFIG.sheet_name`, falling back to the first sheet, the same rule as `excel_writer`.
- Keys and IDs: `new_upload_id` (`<UTC yyyymmddTHHMMSSZ>-<8 hex>`), `is_upload_id`, `inbox_key`, `work_key`, `result_key`.
- A test enforces AD-1: no core module imports `cloud`. `resync_output.py` is a PC tool, not part of AD-1's core list.

## Boundaries & Constraints

**Always:** fictional names in tests; core modules unchanged; `cloud/naming.py` stdlib only (plus `main`).

**Never:** a second parser elsewhere; folding the stored keys (the Web App doesn't); an import of `cloud` from a core module.

</frozen-after-approval>

## Code Map

- `main.py:158-177` `parse_name_date` -- reused, unchanged.
- `apps_script/WebAppSync.gs:50-63` `norm_`, `matchId_` -- the behaviour copied.
- `resync_output.py` `collect_files`, `read_sessions` -- now use `naming`; `FILENAME_RE` removed.
- `excel_writer.py:46-54` -- the sheet rule resync now mirrors.

## Tasks & Acceptance

**Execution:**
- [x] `cloud/__init__.py`, `cloud/naming.py` -- the module.
- [x] `resync_output.py` -- shared parser, configured sheet.
- [x] `tools/norm_cases.json`, `tools/check_norm_parity.js`, CI step -- the parity proof, rerunnable.
- [x] `tests.py` -- 43 cases: Web App parity (17 strings), main's forms, impossible dates, NFC/NFD, prefix matching, the JSON allowlist, output names, keys, resync unchanged versus the old regex, unpadded dates, NFC/NFD merge, configured sheet, and AD-1.

**Acceptance Criteria:**
- Given `Nguyen Van A & 2026-06-01` and main's other forms, when parsed, then the NFC key, name and date are returned, and `2026-02-30` is rejected.
- Given NFC and NFD spellings, when parsed, then they give one Person-Day key.
- Given the Web App's own `norm_()` outputs, when `naming.norm` folds the same strings, then every one matches.
- Given correctly named output files, when resync collects them, then it picks exactly what the old regex picked.

## Implementation Notes

- 2026-10-15, built inline while Story 2.4 (#9) waited for the Owner's bootstrap apply.
- **Fixture:** the 17 cases live in `tools/norm_cases.json`. `tools/check_norm_parity.js` evaluates `function norm_` from `WebAppSync.gs` on them under Node: 17/17 locally (Node 24), and CI runs it in *Tests, image and OCR*. `tests.py` checks `naming.norm` against the same file. So if the reference copy of `norm_` ever changes, CI fails until the cases (and `naming.norm`) follow.
- **Verified:** `run_tests.py` 171 passed (128 before). `check_private_data.py` OK.
- **For Story 4.2:** `inbox_key` doesn't sanitise file names. Intake must accept only base names with an image extension before building a key.

## Review Triage Log

Pass 1, 2026-10-15, inline (diff about 20 kB, floor 5).

- **low (accepted, documented):** resync now reads unpadded-date files, which it used to skip with "unrecognized filename". These are real PC outputs, so syncing them is the fix, not a regression. The docstring says so.
- **low (rejected):** `new_upload_id` treats a naive `datetime` as local time. The only callers pass nothing (UTC now) or a UTC time.
- **false:** "a name with ` & ` inside parses differently." `parse_name_date` and the old regex both give the text before the last ` & <date>` (tested: `test_resync_choice_unchanged…`).
- **false:** "lazy `import main` loads paddle." Measured: numpy and openpyxl only, 0.46 s.
- **defer to 4.2 (noted above):** file-name sanitising for `inbox/` keys belongs to intake validation.
