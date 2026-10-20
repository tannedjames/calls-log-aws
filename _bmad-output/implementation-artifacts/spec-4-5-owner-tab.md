---
title: 'Story 4.5: the Owner tab, a cleaner page, English and Vietnamese'
type: 'feature'
created: '2026-10-19'
status: 'part A built (no bootstrap); part B (settings writes) waits on the Owner''s bootstrap re-apply'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'main (ebdb96d)'
context: ['{project-root}/_bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md', '{project-root}/_bmad-output/implementation-artifacts/spec-4-upload-and-status-page.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** With no PC, re-running a day, re-sending a Sheet, stopping AWS or reading the timings needs CloudShell commands, and `resync.py` and `enqueue.py --requeue` don't run there at all. The page itself is plain and English only.

**The Owner's decision (2026-10-19):** "make all those features customizable on the website too? a password is needed afterall. and make the website a little more visually pleasing". Asked to choose:
- **A separate owner password.** The upload passphrase keeps doing only what it does; a second password opens an Owner tab.
- **Controls:** sync on/off; re-run and re-send days; stop/start AWS and the timings; the passphrases, the alert email and the employee list.
- **Language:** English and Vietnamese, with a switch.

This reverses Epic 4's "Never: the page changing the Sync Switch" (spec-4) **for the owner password only**: the upload passphrase still can't change anything.

**Not on the page, ever:** the Web App URL and `sync_secret`. Whoever could change them could send every Day Sheet, and the key to every Sheet, somewhere else; they stay a CloudShell step (`put_parameters.py --from-private-files`).

**Split** (the workload boundary has no `ssm:PutParameter` and no `sns:Subscribe`):
- **A, no bootstrap:** the owner password; Re-run, Re-send, runner stop/start/state, timings; the redesign; EN/VI.
- **B, after the Owner re-applies the bootstrap:** sync on/off; new upload passphrase and owner password; alert email; employee list.

**Decisions (A):**
- **Owner password:** `/calls-log/owner_passphrase_hash`, the same PBKDF2 format and `put_parameters.py --owner-passphrase` (asked twice, 12 characters at least). Owner routes are `/api/owner/*` and read only the `x-calls-log-owner` header; the upload routes read only `x-calls-log-passphrase`. A placeholder hash answers 503 `owner_not_set`; a wrong password 403 (not 401, so the page keeps the upload session). The per-container digest cache keeps both passwords.
- **Re-run** (`/api/owner/requeue`): the rule moves to `cloud/requeue.py` and `enqueue.py --requeue` calls the same code: only Done, Failed or Parked, and only with every image still in `inbox/`. A Pending upload is queued again instead (the runner skips a duplicate).
- **Re-send** (`/api/owner/resend`): one `{"personDay", "mode": "resync"}` message, as `resync.py` sends, only for a stored `results/<Person-Day>.xlsx`.
- **Runner** (`/api/owner/runner`): state (running, stopped, changing, missing), stop (`killswitch.stop_runner`) and start (`killswitch.start_runner`). The intake role gets `lambda:UpdateEventSourceMapping` and `GetEventSourceMapping` conditioned on the runner's ARN, and `ListEventSourceMappings`: inside the boundary's existing `KillSwitch` and `FindTheTrigger` statements.
- **Timings** (`/api/owner/timings`, 1 to 28 days): `cloud/timing.py`, shared with `turnaround.py`.
- **Errors:** every refusal keeps its English `error` and gains a stable `code` and `args`; `intake.CODES` lists them and the page has both languages for each.
- **Page:** tabs (Upload, Status, Day Sheets, Owner), cards, status badges, progress bars, dark mode, phone width; still one `<style>` and one `<script>` under the hashed CSP, nothing loaded from elsewhere. Only the language choice is stored (`localStorage` `calls-log-lang`); passwords stay in memory.

## Boundaries & Constraints

**Always:** each password opens only its own routes; `cloud/status.py` stays the table's only writer; no password, name or value reaches a log line; the intake can't change any function's code, configuration or concurrency.

**Never:** the Web App URL or `sync_secret` shown or written by the page; a silent fallback from the owner password to the upload passphrase; a stored password in the browser.

</frozen-after-approval>

## Code Map

- `cloud/intake.py` (owner routes, codes), `cloud/static/upload.html`.
- `cloud/requeue.py` (from `tools/enqueue.py`), `cloud/timing.py` (from `tools/turnaround.py`), `cloud/killswitch.py` (`runner_state`, `start_runner`), `cloud/settings.py` (`owner_passphrase_hash`).
- `tools/put_parameters.py` (`--owner-passphrase`).
- `infra/intake.tf` (runner-trigger rights, `CALLS_LOG_RUNNER_FUNCTION`), `infra/parameters.tf`; `infra/tests/intake.tftest.hcl`, `infra/tests/stores.tftest.hcl`.
- `docs/TECHNICAL.md` (code map, §11 the Owner tab, a week with no PC), `docs/OPERATOR_GUIDE.md`.

## Tasks & Acceptance

**Execution (A):**
- [x] Owner password: parameter, settings, `put_parameters.py`, the gate in `intake.handle`.
- [x] Owner routes and the shared modules; tests (each route refuses without the owner password and with the upload passphrase; the placeholder refuses; requeue states and images gone; resend's message and a missing sheet; runner stop, start and state; timings equal the tool's).
- [x] Page: redesign, Owner tab, EN/VI; tests (every word and code in both languages; the CSP).
- [x] Terraform: the intake may update only the runner's trigger.
- [x] Docs.
- [ ] **Owner:** `python3 tools/put_parameters.py --owner-passphrase` in CloudShell, then the live checks below.

**Live checks (A):** the page shows the tabs and EN/VI; the Owner tab refuses the upload passphrase and opens with the owner password; Re-run turns a Done or Failed folder Waiting, then Done; Re-send updates the day's tab; Stop shows stopped, Start shows running; the timings appear.

**Review fixes (2026-10-19, before merge):** a five-dimension review with a skeptic per finding confirmed:
- the intake role could query only the `uploads-by-day` index, so Re-run (`status.uploads`, a table Query) would have failed in AWS; the role now queries the table too (still no Scan);
- timings now cover a rolling window (last 24 hours, 7 or 28 days) instead of UTC dates, which in Vietnam start at 07:00;
- `passphrase.weak()` refuses accented letters, which a browser can't send in a header;
- page fixes:
  - a 403 on Re-run or Re-send says "owner password no longer works" where the click was;
  - Unlock adopts the password only once accepted;
  - Upload and Ready can't overlap or repeat;
  - signing out stops them;
  - nested refusals re-translate on a language switch;
  - the sync pill no longer reverts;
  - a 15.3 MB image is "16 MB";
- the runbook covers owner-password guessing (403 lines), and AWS_SETUP.md lists seven parameters.

**Part B (built):**
- [x] Bootstrap `ChangeSettings` (`ssm:PutParameter` on exactly `sync_enabled`, `intake_passphrase_hash`, `owner_passphrase_hash`, `name_prefixes`) and `AlertSubscriptions`; tests that `webapp_url` and `sync_secret` stay unwritable, also against `ssm:Put*`, `*` or `NotAction`.
- [x] The intake policy with the same rights, on the alerts topic only.
- [x] `cloud/owner_settings.py` (`WRITABLE`, the employee-list and address rules) and the routes `/api/owner/settings`, `sync`, `password`, `alerts`, `employees`; every write resets this container's settings; a refused password re-reads them (at most every 10 s).
- [x] The page's cards: Sync on/off, Passwords (typed twice), Alert emails (add, remove a confirmed one, never the last), Employees (saved in the Web App's form; a prefix clash refused).
- [ ] **Owner:** bootstrap plan and apply from the PR's branch (0 to add, 1 to change, 0 to destroy), then the live checks: sync flips, a new address gets AWS's confirmation email, the list saves and refuses a clash, a password change keeps the page working.

Decisions made while building part B:
- Employee names are saved folded (lower case, no accents), because the Web App compares the folded folder name with its keys as stored.
- An earlier name that would match a later one is refused, since the later one could never be reached (the `NAME_TO_ID` rule).
- `alert_email` is not written by the page (the topic's subscriptions are the record), so the boundary grants four parameters, not five.
- The employee list must fit the parameter's 4 KB.
- The last confirmed alert address can't be removed, so alerts always reach someone; an unconfirmed one can't be removed either, since it expires by itself after 3 days.

## Verification

**Commands:**
- `terraform fmt -check -recursive infra`; `init -backend=false`, `validate` and `test` in `infra/` and `infra/bootstrap/`.
- `python -X utf8 run_tests.py`.
- `python -X utf8 tools/check_private_data.py`: OK.
