---
title: 'Story 3.2: Settings and secrets read at run time'
type: 'feature'
created: '2026-10-15'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'f8a376a'
context: ['{project-root}/_bmad-output/planning-artifacts/architecture/architecture-calls-log-2026-10-14/ARCHITECTURE-SPINE.md']
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The runner must sync with the Web App URL and secret, but the image never contains `secrets_local.py`. It must also honour the AWS Sync Switch per Run, and never let an environment value change the load-bearing OCR and session settings.

**Approach:**
- `config.py` gains a minimal environment path for the two credentials.
- `cloud/settings.py` becomes the only reader of `CALLS_LOG_*` and the `/calls-log/` parameters (AD-9, AD-10).

**Decisions** (the Owner said to work uninterrupted; recorded here):
- **`config._credentials()`:** `SHEETS_WEBAPP_URL` and `SHEETS_SYNC_SECRET` from the environment win over `secrets_local.py`; neither gives empty strings, so `sheets_sync` raises its existing error. They're named like the `secrets_local` variables, not `CALLS_LOG_*`, so `settings.py` stays the only `CALLS_LOG_*` reader. Nothing else in `config.py` reads the environment.
- **`settings.load()`:**
  - one `GetParameters` call (5 names, with decryption) plus the environment (`CALLS_LOG_BUCKET`, `_TABLE`, `_QUEUE_URL`, `_ALERTS_TOPIC_ARN`), cached per container;
  - the placeholder `unset`, or a missing parameter, counts as empty (missing names are logged, names only);
  - `name_prefixes` is parsed by `naming.prefixes_from_json`.
- **`settings.sync_enabled()`:** reads `/calls-log/sync_enabled` fresh. Only `true`, case-insensitive and trimmed, turns it on; an SSM error raises, so the Run fails instead of guessing.
- **`settings.configure_core(settings, sync_on=…)`** sets exactly `sheets_webapp_url` and `sheets_sync_secret` (when non-empty) and `sheets_sync_enabled`. That's how the load-bearing flags stay untouchable.
- **Privacy:** the secret, the URL, names, the passphrase hash and the email are `repr=False`; the one log line shows resource names only.
- boto3 is imported lazily: it's in the image (1.42.97 in `/var/lang`), not on the PC.

## Boundaries & Constraints

**Always:** fictional values in tests; the PC's behaviour unchanged when no environment variable is set.

**Never:** another `CALLS_LOG_*` or SSM reader; a generic environment override of `Config` fields; a secret in any log or repr.

</frozen-after-approval>

## Code Map

- `config.py:16-41` -- `_credentials()`; `Config` unchanged.
- `sheets_sync.py:39-45` -- reads `CONFIG` at call time, so `configure_core` before `sync_day` is enough.
- `cloud/naming.py` -- `prefixes_from_json` (Story 3.3).

## Tasks & Acceptance

**Execution:**
- [x] `config.py` -- the environment path.
- [x] `cloud/settings.py` -- load, sync switch, configure_core.
- [x] `tests.py` -- 7 tests: read once and cached, missing and placeholder parameters, a fresh switch each Run, environment versus `secrets_local`, the existing sync error, the load-bearing flags untouched, no secret or name in logs or repr.
- [x] `docs/TECHNICAL.md` §6.

**Acceptance Criteria:**
- Given two `load()` calls, then SSM is read once.
- Given the switch flipped between Runs, then each Run sees the new value.
- Given both the environment and `secrets_local.py`, then the environment wins; given neither, then the values are empty and `sync_day` raises "sheets_webapp_url is empty".
- Given `CALLS_LOG_OCR_STRICT_ENGINE=false` and similar variables, then `ocr_strict_engine`, `paddle_enable_mkldnn` and `post_9am_exception_limit` are unchanged.
- Given a load with a real-looking secret, then no log line or repr contains it.

## Implementation Notes

- 2026-10-15, inline, stacked on Story 3.3 (#10), which `settings.py` imports.
- `run_tests.py`: 178 passed (7 new). The test logs show only `Settings(bucket=…, table=…, queue_url='', alerts_topic_arn='')`.

## Review Triage Log

Pass 1, 2026-10-15, inline (diff about 14 kB, floor 4).

- **low (rejected):** `configure_core` keeps a non-empty PC value when SSM holds the placeholder. On AWS the PC value is always empty, so this only matters on a PC CLI run, where keeping the PC's own credentials is right.
- **low (rejected):** `sync_enabled()` treats any value but `true` as off. That's deliberate: fail safe towards not writing.
- **false:** "`InvalidParameters` names in the log leak data." They're the fixed `/calls-log/…` names.
- **false:** "importing `cloud.settings` on the PC needs boto3." boto3 is imported only inside `_ssm_client()`; the tests run on the PC without it.
