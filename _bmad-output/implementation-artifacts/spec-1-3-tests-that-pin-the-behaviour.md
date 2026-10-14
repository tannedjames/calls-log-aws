---
title: 'Story 1.3: Tests that pin the behaviour the migration must preserve'
type: 'chore'
created: '2026-10-14'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: ['{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The 85 tests cover parsing, the session rules, the Web App client and the orientation rule. Three things the AWS runner must reproduce have no test: the folder commit rules in `main._process_one_folder`, the Day Sheet layout written by `excel_writer.write_sessions`, and the load-bearing settings. A port could change any of them and every test would stay green.

**Approach:** Add characterisation tests to `tests.py` that record today's behaviour. They run with no OCR models: `extract_calls` and `sync_day` are stubbed, folders live in a temporary directory, and the real `sprs.xlsx` is the template. No product code changes.

**Decisions:**
- Folder rules pinned (sync on unless stated, folder `Nguyen Van A & 2026-06-01`):
  - sync fails: the xlsx exists and the folder stays in place;
  - zero Sessions: `RuntimeError`, no xlsx, the folder stays;
  - success: the xlsx exists, `sync_day` gets the parsed name, date and Sessions, and the folder moves to `_processed/`;
  - sync switched off: the folder is archived and `sync_day` is never called;
  - the name or date can't be parsed: the folder stays.
- Day Sheet pinned with 17 Sessions:
  - B and C hold numeric day-fractions; D holds `=C-B`; B2 is `=SUM(D7:D23)`; B3 is `=B7`;
  - formats are `HH:mm` and `[h]:mm`; the inserted row 23 copies row 22's style and continues column A's numbering;
  - B4 is untouched.
- With 2 Sessions, rows 9–22 are cleared, with D set to numeric 0.
- Settings pinned on a fresh `Config()`: `ocr_strict_engine is True`, `paddle_enable_mkldnn is False`, `post_9am_exception_limit == 4`.
- Safety: the network call (`urlopen`) is also patched to fail the test if it is ever reached, so no test can write to a live Sheet.
- Done when: every new test passes under `run_tests.py` with no models, all new tests together take under 60 s, and no existing test changes.

</frozen-after-approval>

## Implementation Notes

- `tests.py` only, with 9 new tests in a "Characterisation tests" section: 5 folder rules, 3 Day Sheet layout, 1 settings. No product code changed.
- `_folder_run` patches `main.extract_calls` and `main.sync_day` (the names `main` imported, not the originals). It also patches `urlopen` to raise `AssertionError`, and patches `main.log` and `excel_writer.log` to keep the output clean. Folders and output live in `tempfile.TemporaryDirectory()`; `secrets_local.py` is never needed.
- Day Sheet checks read the workbook in memory, captured at `Workbook.save`. On reload, openpyxl turns `HH:mm` numbers into `datetime.time`, which would hide whether the cell holds a number or a string. A reload check also confirms the formulas survive saving.
- Surprise: my first style check compared `cell.border` objects. openpyxl returns style proxies that don't compare by value, so identical styles (style ID 8 on both rows) looked different. The test now compares `style_id` per column, which covers font, border, fill, alignment and number format at once.
- Verification (2026-10-14):
  - `run_tests.py`: 94 passed, 0 failed, 0.8 s for the whole suite (0.4 s before), with no log noise.
  - Mutation check in a throwaway copy (no `secrets_local.py`): each of 7 deliberate breaks fails the intended test. They were: archive despite a sync failure, drop the zero-session guard, D as a value, B3 as a value, overwrite B4, 5 exceptions, oneDNN on. Before this story, "oneDNN on" and "archive despite a sync failure" failed no test at all.
- `graphify update .` run.

## Review Triage Log

- low (patched): the folder tests leaked 4 INFO "Saved spreadsheet" log lines into the test output. `excel_writer.log` is now patched in `_folder_run`.
- low (patched): `delete_after` was passed by position (`True`); it is now a keyword, so the call reads as batch mode.
- low (rejected): the log warnings themselves (e.g. "keeping source folder…") aren't asserted. Folder placement is the behaviour that matters and it is asserted; the wording may change without changing behaviour.
- false: "importing `main` in tests could load paddle." `main` imports `ocr`, which imports paddle lazily, only inside `_build_paddle_engine`. The suite takes 0.8 s and loads no model.
