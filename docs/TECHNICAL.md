# Technical reference

The detailed companion to the [README](../README.md): settings, image
requirements, the exact counting rules, troubleshooting, known limitations, and
how the Google Sheets side works. Read **Troubleshooting** before changing OCR
settings. Several non-obvious platform bugs are documented there, and they
explain why the current settings are what they are.

---

## 1. Code map

| File | Stage | What it does |
|---|---|---|
| `main.py` | orchestration | Command-line entry point. Runs every stage below for one folder (single mode) or every subfolder (`--batch`), then archives the source folder. |
| `runtime_env.py` | setup | Sets OpenMP/thread environment variables. **Must be imported first**, before any native library (see §6). |
| `config.py` | setup | Every tunable constant. Credentials are read from `secrets_local.py`. |
| `image_preprocess.py` | 1. clean up | Classifies screenshot vs photo, perspective-rectifies photos, deskews, denoises, boosts contrast, upscales. |
| `ocr.py` | 2. read text | PaddleOCR 3.x (and 2.x), EasyOCR and Tesseract wrappers. Groups text lines into rows. Refuses to load two deep-learning frameworks in one process. |
| `parser.py` | 3. understand rows | Time, duration, direction and status parsing; rounding; whether a call counts. |
| `session_builder.py` | 4. build sessions | Removes duplicates, sorts, and groups calls into sessions using the gap rules. |
| `excel_writer.py` | 5. write Excel | Fills the `sprs.xlsx` template; adds rows past 16 sessions. |
| `review_report.py` | 5b. reports | `review_report.csv` + `processing_log.json` (single-folder mode only). |
| `sheets_sync.py` | 6. Google Sheets | POSTs one day's sessions to the Apps Script Web App (with retries). |
| `apps_script/WebAppSync.gs` | 6. Google Sheets | The Web App: creates the day tab, writes the sessions, maintains the links. |
| `apps_script/Employees.gs` | 6. Google Sheets | **Private, gitignored.** Employee name → spreadsheet ID map. Template: `Employees.example.gs`. |
| `resync_output.py` | tool | Re-pushes existing `output/*.xlsx` to Google Sheets without re-running OCR. |
| `diagnose.py` | tool | Single-image debugger: prints OCR lines, rows, and the rule that rejected each row. |
| `create_template.py` | tool | Builds a blank `sprs.xlsx` if you don't have one. |
| `tools/check_private_data.py` | tool | Fails if a commit or push would publish employee data or credentials. |
| `tests.py` / `run_tests.py` | tests | Acceptance + regression tests; `run_tests.py` runs them without pytest. |

The AWS side (`cloud/`, `infra/`) wraps the same pipeline. The core modules
above never import it.

| File | What it does |
|---|---|
| `cloud/runner.py` | One Run: a queued Folder through the unchanged pipeline, its Day Sheet to `results/`, then the Sheet if the AWS Sync Switch is on. The Lambda handler, and a CLI for parity. |
| `cloud/status.py` | Folder Status: the only writer of the `calls-log-folders` table (states, lease, attempts). |
| `cloud/storage.py`, `cloud/naming.py`, `cloud/settings.py` | The data bucket, the one folder-name parser, and run-time settings and secrets. |
| `cloud/killswitch.py` | Called by a guardrail alarm: turns the runner's queue trigger off and emails the Owner (§11). |
| `cloud/bench.py` | Story 3.1's benchmark function (only while `enable_bench` is on). |
| `tools/enqueue.py` | From the PC: sends Folders to the AWS runner, or re-queues a stored one (§11). |
| `tools/resync.py` | From the PC: re-sends stored Day Sheets to the Sheets, the AWS `resync_output.py` (§11). |
| `tools/put_parameters.py`, `tools/bench_lambda.py`, `tools/aws_cli.py` | From the PC: sets the secret parameters, runs the benchmark, and calls the AWS CLI for both (no boto3 on the PC). |
| `infra/`, `infra/bootstrap/` | Terraform: everything in AWS. `docs/AWS_SETUP.md` has the manual steps. |

---

## 2. Install

Requirements: **Python 3.13** (64-bit), internet for the first install and
first run, and ~1.5 GB of disk for PaddleOCR's models. No GPU is needed.

```powershell
cd calls-log
python -m venv .venv
.\.venv\Scripts\activate
pip install -U pip
pip install --require-hashes -r requirements.lock
copy secrets_local.example.py secrets_local.py     # then fill in the two values
```

**Always install from `requirements.lock`.** It pins every package, including
paddleocr 3.7.0, paddlex 3.7.2 and paddlepaddle 3.3.1, with SHA-256 hashes. One
file serves Windows and the Linux runner image; platform markers skip what a
platform doesn't need. Unpinned installs drifted before: a newer paddlex
switched the default text detector to `PP-OCRv6_medium_det`. `requirements.txt`
holds only the version ranges used to generate the lock, plus one hard pin:
`opencv-python==4.10.0.84`, which must equal paddlex's `opencv-contrib-python`
pin because both install `cv2`.

To move an existing install to the lock, delete `.venv` and run the block
above. Installing over an old venv leaves packages the lock doesn't list, such
as `torch`, in place.

To change a version, edit the range in `requirements.txt` if needed and
regenerate the lock with [uv](https://docs.astral.sh/uv/) (needed only for
this, not for installing):

```powershell
uv pip compile requirements.txt --universal --python-version 3.13 --generate-hashes --annotation-style line -o requirements.lock
uv pip compile requirements.txt --universal --python-version 3.13 --generate-hashes --annotation-style line -o requirements.lock --upgrade-package paddleocr
```

The first command keeps the versions already in the lock and only adds what is
missing. The second raises one package. After any change to the lock, re-run
the tests and the Reference Set parity check before using it in production.

> **Install exactly ONE deep-learning framework.** `easyocr`/`torch` and
> `paddlepaddle` each ship their own Intel OpenMP runtime (`libiomp5md.dll`).
> Loading both into one Windows process either crashes it or delivers a fake
> console CTRL event, which Python reports as a `KeyboardInterrupt` nobody
> typed. `ocr.py` refuses to load a second framework, and `runtime_env.py` sets
> `KMP_DUPLICATE_LIB_OK` as a backstop. The real fix is to not have both:
> `pip uninstall -y easyocr torch torchvision`. Tesseract is safe alongside
> anything, because it runs as a separate process.

Alternative engines: set `ocr_engine = "easyocr"` or `"tesseract"` in
`config.py`, or pass `--ocr-engine tesseract`. `pytesseract` (plus the
`tesseract` binary) also enables 90/180/270° rotation detection; without it,
Paddle's page-orientation classifier turns landscape (sideways) images upright
(see *Rotation* in §4).

By default a failing engine is **fatal** (`ocr_strict_engine = True`); it never
silently falls back to a weaker one. Silent fallback is how every image went
through EasyOCR for two months without anyone noticing. `--allow-engine-fallback`
restores the old behaviour.

Verify the install (no OCR needed):

```powershell
.\.venv\Scripts\python.exe -X utf8 run_tests.py     # last line: "Results: N passed, 0 failed"
```

`-X utf8` matters when the output is piped or redirected. Otherwise the
Windows console encoding cannot print the Japanese test names, and the
runner crashes with `UnicodeEncodeError`.

Check OCR end to end on the **Synthetic Set**: made-up call-log screenshots
with known answers, safe to use anywhere because they hold no real names or
phone numbers. This needs the OCR models and takes a few minutes:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\gen_synth.py          # writes synthetic\ (gitignored)
.\.venv\Scripts\python.exe -X utf8 tools\check_synth.py --threads 4
```

`gen_synth.py` renders 12 screenshots, 4 photos of them on a dark desk, and 2
of those photos turned sideways, plus `truth.json` with the expected calls.
`--seed N` gives a different day, and the same seed always gives the same
files when the same font is used. It needs a Japanese font: Yu Gothic or Meiryo
on Windows, `fonts-noto-cjk` on Ubuntu, or pass one with `--font`.
`check_synth.py` reads each set exactly as `main.py` does, without syncing,
writing or moving anything. Every call must match; any difference exits with
code 1. Run it after any change to OCR, preprocessing, parsing or
`requirements.lock`.

### Runner image (Linux, for AWS)

The same pipeline packaged for AWS Lambda: the `Dockerfile` builds on Lambda's
Python 3.13 base (Amazon Linux 2023, x86_64 only), pinned by digest.

- **Packages:** installed from `requirements.lock` with `--require-hashes`,
  plus `mesa-libGL` and `glib2`, which OpenCV needs, and `libgomp`, which
  paddlepaddle needs. If the first two ever disappear from Amazon Linux, the
  fallback is to switch both OpenCV packages to their `-headless` builds in
  `requirements.txt` and regenerate the lock.
- **OS patches:** the image's OS packages are upgraded to a pinned Amazon
  Linux 2023 release, the one in the `amazonlinux` image on the Dockerfile's
  first `FROM` line. Dependabot bumps that image and Lambda's base in one
  weekly PR; CI builds and tests it, and merging deploys it. ECR scans every
  pushed image (`aws ecr describe-image-scan-findings --repository-name
  calls-log-runner --image-id imageTag=<sha>`). Why this design:
  - Lambda's base lags behind Amazon Linux. On 2026-10-15 its newest digest
    was still on release 2023.12.20260914, six releases behind 2023.12.20261013,
    and the first ECR scan found 15 high findings, all in its OS packages.
    Bumping the base digest alone would have fixed none of them.
  - Each Amazon Linux 2023 release is a fixed repository snapshot, and older
    ones stay available, so the same commit always installs the same packages
    (FR-25). `--releasever=latest` would break that: the same commit would
    build different packages each week. It would not patch any sooner either,
    because the image is only rebuilt when a commit lands on `main`.
  - `dnf` in this image is `microdnf`, which has no `--security`, and it skips
    a release it can't reach with "Nothing to do". So the build fails unless
    the packages really reached the pinned release.
  - The libraries the OCR loads (`mesa-libGL`, `glib2`, `libgomp`, `glibc`)
    were unchanged by the upgrade to 2023.12.20261013; the patched packages
    were curl, OpenSSL, krb5, libxml2, pcre2, rpm and libX11. CI's Synthetic
    Set check runs on every bump. CI's run summary lists the release and those
    four libraries; when a bump changes one of them, run the Reference Set
    parity check before cutover.
- **Models:** baked in at build time by `tools/bake_models.py`. It lets the
  pipeline download its models through `config.py`, then refuses to continue
  unless every file matches `models.sha256`, which was recorded from the PC. So
  the image reads pictures with exactly the PC's models and never downloads at
  run time. `cloud/entrypoint.sh` links them into PaddleX's cache home,
  `/tmp/paddlex`, because Lambda can write only to `/tmp`.
- **Threads:** sized to the CPUs the container is given (cgroup quota or CPU
  affinity), at most 10. On the PC nothing changes: 10 PaddleOCR threads and
  one OpenMP thread per core.
- **Privacy:** `.dockerignore` allows only runtime files and always excludes
  `secrets_local.py`, so building on the Owner's PC is safe.

Measured on 2026-10-15: 2.2 GB uncompressed and about 0.8 GB compressed, well
under Lambda's 10 GB limit for container images.

GitHub Actions (`.github/workflows/ci.yml`, job *Tests, image and OCR*) builds and checks the
image on every change to it. It plants decoy private files and proves they
stay out, checks that `--cpus 1` gives 1 thread, runs `diagnose.py` and
`check_synth.py` with `--network none --read-only`, and writes the image size,
the Amazon Linux release and the versions of the OCR's system libraries to the
run summary. To build locally with Docker Desktop:

```powershell
docker build --platform linux/amd64 -t calls-log-runner .
docker run --rm --network none --read-only --tmpfs /tmp -v "${PWD}\synthetic:/synth:ro" calls-log-runner python3 tools/check_synth.py --dir /synth
```

To check that this PC's models still match the image's, run
`.\.venv\Scripts\python.exe tools\bake_models.py --check`. **Changing a model**
(a new `requirements.lock` or `config.py` model setting): run the pipeline once
so the new model downloads, run `--write-manifest`, then repeat the Reference
Set parity check before using the image.

### Parity check (Reference Set, on the PC only)

Proves that the runner image reads real screenshots exactly like the PC. The
Reference Set is a few real Person-Day folders kept **outside the repo**, next
to a private `parity-expected.json` that lists, for each folder, its name, the
files to skip and the expected Sessions:

```json
{"folders": [{"name": "<folder name>", "ignore": ["correct output.png"],
              "sessions": [["08:00", "10:01"], ["11:04", "12:01"]]}]}
```

The expected Sessions are the PC's Day Sheet for that day. Both files hold
employee data, so they never go into the repo, and the tool refuses paths
inside it.

**Setup, once:** Docker Desktop on WSL 2. In an administrator terminal run
`wsl --install --no-distribution`, restart, run
`winget install -e --id Docker.DockerDesktop`, open Docker Desktop and accept
its terms, then check that `docker run --rm hello-world` works. Then build the
image:

```powershell
docker build --platform linux/amd64 -t calls-log-runner:local .
.\.venv\Scripts\python.exe -X utf8 tools\parity_check.py --image calls-log-runner:local --ref C:\path\to\reference-folders
```

Each folder is mounted read-only as `/ref/F1`, `/ref/F2`, … with no network,
on a read-only filesystem, as a non-root user, and processed exactly as
`main.py` would, minus writing, syncing and archiving. The output shows only
F-labels, counts and times, for example
`F1: 15 images, 101 countable calls, 3 sessions -> IDENTICAL`. It also checks
that no reference folder changed during the run. The exit code is 0 only when
every folder is IDENTICAL. Re-run the check after any change to
`requirements.lock`, a model or the `Dockerfile`, and before switching
production to AWS.

---

## 3. Command-line reference

```text
main.py --images <folder> --template sprs.xlsx --output <file-or-folder> [--batch]

  --batch                     --images is a parent folder; each subfolder is one person-day
  --keep-input                batch: leave source folders where they are
  --delete-input              batch: hard-delete source folders instead of archiving
  --ocr-engine paddle|easyocr|tesseract
  --allow-engine-fallback     permit silent fallback to another engine
  --debug                     verbose logs + full engine tracebacks
  --review-csv PATH           single mode only
  --log-json PATH             single mode only
```

The canonical daily command (Windows, run from `calls-log`):

```powershell
.\.venv\Scripts\python.exe main.py --images ./input_images --output ./output --template ./sprs.xlsx --batch --debug
```

Batch-mode behaviour:

- Each subfolder must be named `<Name> & yyyy-mm-dd`. The part before the date
  picks the Google Sheet (§7).
- On success the subfolder is **moved** to `input_images/_processed/` and
  pruned after `input_archive_retention_days` (30). It is moved only if
  **both** the xlsx was written **and** the Sheets sync succeeded. Anything
  that fails stays in place for a retry. `_processed` is never re-processed.
- A folder that yields **zero sessions fails loudly**. The tool does not write
  the blank template and does not archive the folder. (An OCR outage used to
  produce spreadsheets full of `0:00` that looked like real empty days.)
- If `<Name> & date.xlsx` already exists, the new file is `<Name> & date(1).xlsx`.
  The Google Sheets tab, by contrast, is **overwritten**.
- No review CSV / processing log is written in batch mode.

Run the first batch of a new week with `--keep-input --debug`. If the engine is
misconfigured, you lose nothing and you get the real traceback.

`resync_output.py` (no OCR, works whatever `sheets_sync_enabled` says):

```powershell
.\.venv\Scripts\python.exe resync_output.py --dry-run              # preview only
.\.venv\Scripts\python.exe resync_output.py                        # everything in ./output
.\.venv\Scripts\python.exe resync_output.py --name "Nguyen Van A"  # one person (prefix)
.\.venv\Scripts\python.exe resync_output.py --date 2026-06-04      # one date
```

It reads the session rows back out of each `<Name> & yyyy-mm-dd.xlsx` and
POSTs them to the same Web App. Existing tabs are overwritten, because the
xlsx files are the source of truth. `(n)` duplicates are skipped when the base
file exists.

---

## 4. What images work

**Formats:** `.jpg .jpeg .png .webp .bmp` (any case). Anything else in the
folder is ignored. HEIC (the iPhone default) is **not** supported. Chat-app
recompression (e.g. Zalo-forwarded JPEGs) does not noticeably hurt accuracy.

**How many:** up to `max_images` (40) per folder, with a warning above
`safe_warn_images` (35). The template holds 16 sessions; beyond that, rows are
inserted (`allow_extra_rows = True`).

**Two image types**, classified per image (recorded in `processing_log.json` →
`classification`):

1. **Screenshots** are detected when ≥82% of pixels are bright
   (`screenshot_bright_frac`) and the border is bright. They are left almost
   untouched (mild contrast, upscale to 1600px). **Best accuracy.**
2. **Photos of a phone screen.** The bright screen quadrilateral is found and
   perspective-warped flat, then denoised → CLAHE → autocontrast → sharpened →
   upscaled.

Photo requirements: whole screen in frame (all four edges); screen occupies
15–97% of the frame; background darker than the screen; aspect 1.2:1–3.2:1;
roughly portrait; no heavy glare. Moiré is expected and handled.

**Rotation.** Call-log screens are portrait. An image that is still landscape
(wider than tall) after clean-up is a sideways screen, and PaddleOCR's
page-orientation classifier (`paddle_use_doc_orientation`) turns it upright.
Portrait images skip the classifier: it once turned an upright photo by 90°,
and that photo gave no calls. The cost is that an upside-down portrait photo is
not flipped; rotate the file or retake it.

**What the screen must show:** the Rakuten Link / Android call-log list. Each
call needs a start time, ideally a duration and a direction marker. Phone
numbers are ignored entirely and never parsed or stored.

| field | accepted forms |
|---|---|
| Time | `03:00 PM`, `03:00PM`, `8:00 AM`, `21:34`, `午前 9:15`, `午後 1:30`, `8:00 SA`, `2:15 CH` |
| Duration | `(32秒)`, `1分25秒`, `1 min 25 sec`, `1m 25s`, `1 phút 25 giây`, `01:25`, `00:01:25`, `0 sec` |
| Outgoing | `発信`, `発呼`, `outgoing`, `outbound`, `dialed`, `called`, `gọi đi` |
| Incoming | `着信`, `受信`, `受話`, `incoming`, `received`, `gọi đến` |
| Missed | `不在`, `応答なし`, `拒否`, `missed`, `no answer`, `declined`, `nhỡ`, `lỡ` |

Spacing does not matter. OCR routinely misreads `秒`, so `利 杪 抄 秋` are
accepted as equivalents. A row with a valid start time but no duration counts
as a 0-second call, even without a direction marker, because OCR often splits
the marker onto a separate line. Such rows are tagged
`recovered_without_call_marker`.

**Rows deliberately ignored:** the status bar (carrier names, battery `%`,
`通話履歴`, `すべての通話`, and bare times in the top 8% of the image, which are
the clock); in-app coupon banners (`クーポン`, `円`, `割引`, `限定`, `OFF`,
`M月D日`, `…まで`), which Rakuten Link injects between call rows with their
own times.

**Will not work:** HEIC, PDF, video frames, photos of printed logs, cropped
rows (time without direction), unreadably small text, folders not named
`<Name> & yyyy-mm-dd`, and screenshots that include yesterday's calls (§9).

---

## 5. The counting rules

| Rule | Value / behaviour | Code |
|---|---|---|
| 12-hour → 24-hour, JP `午前/午後`, VI `SA/CH` | | `parser.parse_start_time` |
| Lost AM/PM | an unmarked time before 07:00 is treated as PM (+12 h), `assume_pm_before_hour = 7`. A genuine 06:59 call would become 18:59. | same |
| Duration rounding | remainder ≥ 20 s rounds up, < 20 s rounds down | `parser.round_duration_minutes` |
| End time | start + rounded duration | `parser.parse_row` |
| Counts | answered incoming; all outgoing, including 0-second / unanswered | `parser.is_countable` |
| Does not count | missed / unanswered incoming; unknown direction + zero/missed (flagged) | same |
| Duplicates | exact duplicates removed (screenshots overlap); fuzzy duplicates only flagged | `session_builder.dedupe` |
| Same session | gap ≤ 5 min (`normal_gap_minutes`) | `session_builder.build_sessions` |
| Post-09:00 exception | a 6–10 min gap still merges if the later call starts at/after 09:00; **max 4 per day**, first come first served | same |
| Overlap | merged and flagged | same |
| Zero-length sessions | kept (`drop_standalone_zero_sessions = False`); dropping them shifted B3 | same |
| Span | end − start of the session (not talk time) | `excel_writer.write_sessions` |
| B2 / B3 / B4 | total of spans / first start / left alone (attendance note) | same |
| > 16 sessions | extra rows inserted below row 22 | `excel_writer._expand_template` |

---

## 6. Settings (`config.py`)

```python
ocr_engine = "paddle"                       # "paddle" | "easyocr" | "tesseract"
ocr_strict_engine = True                    # engine failure is fatal, no silent fallback
ocr_confidence_threshold = 0.85             # below this -> flagged for review

paddle_rec_model_name = "PP-OCRv5_mobile_rec"  # None = heavy server model
paddle_det_limit_side_len = 960             # 1920 cost ~4x for no gain
paddle_enable_mkldnn = False                # MUST stay False on Windows (see §8)
paddle_cpu_threads = 10                     # never more than the CPUs available (containers)
paddle_use_doc_orientation = True           # landscape images only; turns sideways photos upright
paddle_use_textline_orientation = False     # ran a model per line for nothing

screenshot_bright_frac = 0.82               # >= this -> screenshot, else photo
photo_min_long_edge = 1600
screenshot_min_long_edge = 1600
assume_pm_before_hour = 7

normal_gap_minutes = 5
post_9am_exception_gap_minutes = 10
post_9am_exception_limit = 4                # per DAY (see §9)
round_up_seconds_threshold = 20
drop_standalone_zero_sessions = False
allow_extra_rows = True
max_images = 40;  safe_warn_images = 35

archive_input_folders = True                # move to _processed instead of delete
input_archive_retention_days = 30

sheets_sync_enabled = True                  # master on/off switch for Google Sheets
sheets_sync_timeout = 60;  sheets_sync_retries = 3
# sheets_webapp_url / sheets_sync_secret: the environment variables
# SHEETS_WEBAPP_URL / SHEETS_SYNC_SECRET if set, else secrets_local.py (gitignored)
```

**The environment can change only the two credentials.** Every other value
above, including the load-bearing `ocr_strict_engine`, `paddle_enable_mkldnn`
and `post_9am_exception_limit`, is the same on the PC and in AWS.

**AWS settings (`cloud/settings.py`).** It is the only reader of the
`CALLS_LOG_*` environment variables, which Terraform sets: `CALLS_LOG_BUCKET`,
`CALLS_LOG_TABLE`, `CALLS_LOG_QUEUE_URL` and `CALLS_LOG_ALERTS_TOPIC_ARN`,
plus `CALLS_LOG_RUNNER_FUNCTION` for the kill switch. A test fails if
`infra/` sets a name it doesn't read. It is also the only reader of the `/calls-log/` parameters (docs/AWS_SETUP.md
§5).
- **Once per container:** it reads the Web App URL and secret, the allowed name
  prefixes, the upload passphrase hash and the alert address. A parameter
  still holding Terraform's placeholder `unset` counts as empty.
- **At the start of every Run:** it reads the AWS Sync Switch
  `/calls-log/sync_enabled`; only `true` turns it on.
- **What it hands the core:** only the URL, the secret and the switch.
- **Logs:** never the secret, the URL, names or the address.

**Speed.** `lang="japan"` makes PaddleOCR pick the heavy server models. With
oneDNN disabled (mandatory on Windows), that meant ~60 s per screenshot and
2–7 min per photo. The mobile recogniser plus a 960 px detector limit bring it
to roughly 5 s per image on 10 cores. `OMP_NUM_THREADS` (set to the core count
in `runtime_env.py`) also matters.

---

## 7. Google Sheets side

### Architecture

The Python tool never holds Google credentials. `sheets_sync.py` POSTs
`{secret, name, date, sessions:[{start,end}]}` to a deployed **Apps Script Web
App**. That app runs under the owner's Google account and edits the
spreadsheets. Deployment steps: [`apps_script/README_SETUP.md`](../apps_script/README_SETUP.md).

For each POST it, idempotently:

1. matches the name to a spreadsheet (§ name matching below);
2. finds the day tab, or creates it by duplicating the `📄 Mẫu` template, in
   date order;
3. writes the sessions into `B7:D22`, plus `B2` (total) and `B3` (first start);
4. writes the day tab's `B1` back-link to its month tab;
5. writes the month tab's `B1` link to the `🏠 Tổng quan` overview tab;
6. sets the month tab's column-B cell for that date to `=HYPERLINK(…; "Mở->")`.

The response carries `linked` (month→day), `month_linked` (day→month),
`overview_linked` and `capped`.

### Spreadsheet layout

One spreadsheet per employee, with four kinds of tab:

- **`🏠 Tổng quan`**: overview, linking to each month.
- **Month tabs** (`📅 Tháng 9 2026`, or full-width `📅 ２０２６年９月`). `A1` holds
  a real Date (the 1st) shown as the title; row 2 is the `Ngày` header; column A
  lists the dates, and column B holds `Mở->` links (or `T7`, `CN`, holiday or
  leave text).
- **Day tabs** (`2026-10-2`). Since web-app version 8 the day is
  **unpadded**; older tabs keep whichever spelling they were created with.
  `B1` back-link, `B2` total, `B3` first start, `B4` attendance note
  (never written by the sync), rows 7–22 the sessions.
- **`📄 Mẫu`**: the blank day template. Never delete it.

### Name matching

The folder name drives everything, not the screenshot contents. The name is
lower-cased, accents stripped, `đ`→`d`, and spaces collapsed. It is then
matched as a **prefix** against the keys of `NAME_TO_ID` (in
`apps_script/Employees.gs`), and the first matching key wins. So trailing notes
in a folder name are tolerated (`Nguyen Van A (note) & 2026-06-03` still
matches `'nguyen van a'`). It also means a new key must never be a prefix of
another person's name. Rename a folder and you change whose sheet it goes to.

An unknown name returns `unknown name`. Nothing is created on the Google side,
and the folder is kept.

### Rules that cost real time

- **Saving in the Apps Script editor does not update the live endpoint.** After
  any change: Deploy → Manage deployments → ✏️ edit → Version: **New version** →
  Deploy. **Never "New deployment"**: it mints a different `/exec` URL, so the
  tool keeps calling the old version.
- **The live Apps Script project is the source of truth.** It also holds
  things that are deliberately not in this repo (the real `SECRET`, a one-off
  seed loader with embedded data). Patch the live code in place; never paste
  the local files over it.
- **Formulas use `;` as the argument separator** (sheet locale).
  `=HYPERLINK("url", "label")` breaks; `=HYPERLINK("url"; "label")` works. Times
  are written as numeric day-fractions, not `=TIME(h,m,s)`, for the same reason.
- **Tab names mix full-width digits** (`２０２６年９月`). JavaScript's `\d` is
  ASCII-only, so any name-matching code must go through `asciiDigits_()`.
- **Never compare or sort day-tab names as strings.** `2026-07-6` and
  `2026-07-13` coexist; use the numeric `dayKey_()`.
- **New employees' sheets are copies** and arrive with `=HYPERLINK("url"; "Mở->")`
  placeholders in every month row. They look linked but go nowhere until synced.
- **Maintenance functions** (`backfillMonthLinks`, `backfillOverviewLinks`,
  `sortDayTabsAll`, `purgePreAugust`) run from the editor without a redeploy.
  Where a `…DryRun()` variant exists, run it first and read its log.

### Adding an employee

1. Add one key to `NAME_TO_ID` in the **live** Apps Script project and in your
   local `apps_script/Employees.gs`. The key is the lower-cased, accent-stripped
   name prefix; check that it is not a prefix of, or prefixed by, any other key.
2. Redeploy the **existing** deployment as a new version (above).
3. No Python change is needed.

---

## 8. Troubleshooting

**`KeyboardInterrupt` but you never pressed Ctrl+C.** This is the paddle + torch
OpenMP clash (§2). It originally happened because `ocr.py` called the
PaddleOCR 2.x API against 3.x (`show_log=` → `ValueError`). PaddleOCR never
constructed, the run silently fell back to EasyOCR, and torch loaded on top of
paddle. It is now fixed in three places: the 3.x constructor, `runtime_env.py`
imported first, and a refusal to load a second framework. If it recurs:
`pip uninstall easyocr torch torchvision`, and run with `--debug`.

**A folder OCRs fine but produces 0 sessions.** Run `diagnose.py` on one image;
it names the rule that killed each row. The known instance: PaddleOCR emits
screenshot rows **without spaces** (`03:00PM発信(32秒)`), and Python's `re` counts
CJK as word characters, so a `\b` after `PM` never matched. Every Japanese
screenshot row was dropped with `start_time_not_found`. `RE_TIME_12` now uses
digit/colon lookarounds. If you edit those patterns, keep the unspaced tests
passing.

**`NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support`.**
PaddlePaddle 3.x cannot run oneDNN kernels on Windows CPU. Fixed by
`paddle_enable_mkldnn = False`; `ocr.py` also rebuilds without oneDNN if it sees
this error. Try `True` again only after a paddle upgrade.

**`No OCR engine available.`** None of paddleocr / easyocr / pytesseract could be
imported. You are probably not using `.venv\Scripts\python.exe`.

**Photos give poor OCR.** Check `processing_log.json` → `preprocessing`. Each
entry records `classification` and either `rectify_phone_screen` or a
`screen_crop_skipped` reason. Knobs: `screenshot_bright_frac`,
`screen_min_area_frac`, `screen_max_area_frac`, `photo_min_long_edge`. A photo
that is upside down gives no calls: rotate it 180° and re-run (§4, *Rotation*).

**`pip install` fails with `No such file or directory` and a "Long Path" hint.**
Some of paddle's files sit deep inside the venv, and Windows limits paths to
260 characters. Move the `calls-log` folder to a shorter path (for example
`C:\calls-log`), delete `.venv`, and install again.

**PaddleOCR fails to download models.** Internet is needed on the first run only.
Behind a proxy, set `HTTP_PROXY` / `HTTPS_PROXY`.

**OCR is very slow.** About 10–20 s of initialisation per run is normal. If each
image takes a minute or more, check, in order: `paddle_rec_model_name`
(`"PP-OCRv5_mobile_rec"`), `paddle_det_limit_side_len` (960),
`photo_min_long_edge` (1600), `paddle_use_textline_orientation` (False),
`paddle_cpu_threads` (10). oneDNN cannot be re-enabled to speed this up.

**Times off by 12 hours.** The AM/PM marker was lost. Look for
`ampm_missing_assumed_24h` in the review CSV.

**Phantom calls mid-day.** A new coupon banner slipped through. Add its pattern
to `RE_PROMO_ROW` in `parser.py`.

**Excel shows `#####` in column D.** Widen the column. **`=SUM(...)` shown as
text:** recalculate in Excel (Ctrl-Shift-F9); openpyxl writes formulas and the
spreadsheet app evaluates them.

**Sync: `unknown name`.** The folder name prefix is not a `NAME_TO_ID` key. Fix
the folder name, or add the person (§7) and redeploy.

**Sync: `bad secret`.** `SHEETS_SYNC_SECRET` in `secrets_local.py` differs from
`SECRET` in the deployed script.

**Sync: request failed / timeout.** Open the `/exec` URL in a browser; it should
return `{"ok":true,"service":"call-log sheets sync",…}`. Check the network or
raise `sheets_sync_timeout`.

**Sync: script edits don't take effect.** You saved but did not redeploy a new
version (§7).

**A folder keeps being re-processed.** Its sync keeps failing, so it is never
archived. Fix the sync cause.

### Review report columns (single-folder mode)

`source_file`, `raw_text`, `start_time`/`end_time` (24 h), `raw_duration_seconds`,
`rounded_duration_minutes`, `direction`, `status`, `countable`, `ocr_confidence`,
`warnings`, `suggested_correction`. Common warnings:
`low_ocr_confidence_…` (check the image), `duration_format_ambiguous_assumed_mm_ss`,
`direction_unknown_for_missed_or_zero` (excluded; add manually if it was
outgoing), `possible_fuzzy_duplicate_with_…` (decide manually),
`overlap_with_…`, `call_appears_to_cross_midnight` (usually a misread time).

---

## 9. Known limitations

Neither of these crashes, so neither will announce itself.

**The post-09:00 exception budget is per day, first come first served.**
Merges inside earlier sessions (after 09:00) spend it too, so a long morning
can exhaust the 4 exceptions before the afternoon. Measured on 27–31 July 2026:
25 unmerged 6–10 min gaps, 181 minutes in total, across 38 person-days. The
owner chose to keep 4 per day. A per-session budget would be a small change in
`session_builder.build_sessions` (`exceptions_used_total`).

**No date awareness.** The call-log app groups rows under `今日` (today) and
`昨日` (yesterday), and the parser ignores those headers. A screenshot scrolled
too far counts yesterday's calls as today's. Symptoms: an isolated short session
well before ~08:00, or sessions well past ~15:00. A fix is tractable (discard
everything below a `昨日` header) but not implemented.

**Google Sheets caps at 16 sessions** (`FIRST_ROW = 7, LAST_ROW = 22` in the Web
App), while the xlsx expands. On a very busy day, the two can disagree. The
response reports `capped: true`.

---

## 10. Privacy and publishing

| Never published | Why | Kept out by |
|---|---|---|
| `input_images/*`, `output/*` | screenshots contain customer phone numbers and employees' call history; outputs are their working hours | `.gitignore` |
| any image / `.xlsx` other than `sprs.xlsx`, root-level `.csv`, reports | same data, saved somewhere else | `.gitignore` safety net |
| `secrets_local.py` | Web App URL + secret: together they can write to every employee's sheet | `.gitignore` |
| `apps_script/Employees.gs`, Apps Script backups/patches | employee names ↔ spreadsheet IDs | `.gitignore` |

Before every push:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\check_private_data.py
```

It reads the real secret, URL, employee names and spreadsheet IDs from the two
private files, then fails if any of them appears in a tracked file. It never
prints the values. To run it automatically on every commit and push, enable the
bundled hooks once per clone:

```powershell
git config core.hooksPath .githooks
```

**On GitHub (CI) the check fails closed.** The private files never exist there,
so CI can't read the real values. Instead it runs
`check_private_data.py --fail-closed`, which **refuses to pass** unless it gets a
*hashed denylist* from the repository secret `PRIVATE_DENYLIST`. The denylist
holds keyed hashes of the secret, the URL, the spreadsheet IDs and the
accent-folded employee names, with labels like `employee name #3`. GitHub
therefore never receives a real value, yet a value pasted into any file is still
caught. Create or refresh it on **the Owner's PC**, where the private files are,
whenever an employee or the secret changes:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\check_private_data.py --export-denylist denylist.json
gh secret set PRIVATE_DENYLIST --repo tannedjames/calls-log-aws < denylist.json
gh secret set PRIVATE_DENYLIST --repo tannedjames/calls-log-aws --app dependabot < denylist.json
del denylist.json
```

The second line matters: pull requests that Dependabot opens can only read
*Dependabot* secrets, so without it every Dependabot PR fails the privacy gate.

Without `gh`, paste the file's contents into the repository's *Settings →
Secrets and variables → Actions → New repository secret*. Until the secret
exists, CI's *Privacy gate* job is red by design, and `main` accepts no merge.

**CI (`.github/workflows/ci.yml`)** runs on every push to `main` and every pull
request. It has two jobs, both required before a PR can merge into `main`:
*Privacy gate* (above), and *Tests, image and OCR*. The second builds the runner
image, runs `run_tests.py` inside it, and repeats the image checks from
*Runner image* (§2). The actions are pinned to exact commits, and the runner
image's two base images to exact digests; Dependabot opens a PR each week when
either has updates.

**Deploy (`.github/workflows/deploy.yml`)** is the only way changes reach AWS.
It holds no AWS key: it signs in through GitHub OIDC with the role ARNs stored
as repository variables.
- **On every pull request**, *Terraform plan* runs:
  - `fmt`, `validate`, and the offline `terraform test` for `infra/` and
    `infra/bootstrap/`. The bootstrap tests check that each CI role trusts one
    exact subject and that the plan role can only read;
  - a check that the deploy role refuses the PR's token;
  - `terraform plan` with the read-only plan role, posted as one PR comment.
    It doesn't refresh: it compares the code with the state the last deploy
    left, because refreshing would decrypt the `/calls-log/` parameters,
    which the plan role must never read.

  Nothing is applied.
- **Once CI is green on a push to `main`**, *Push the runner image and apply*:
  - builds the image, runs the unit tests inside it, and pushes it to the ECR
    repository `calls-log-runner`, tagged with the commit's full SHA;
  - then applies `infra/` with that tag, so every function runs the new
    image, then plans again and fails if any drift is left.

  The image goes first because Lambda refuses an image that isn't in the
  registry; if the apply fails, the functions keep the previous image. On a
  new account the job creates the registry before the first push. Tags are
  immutable and ECR keeps only the newest 2 images: the running one and the
  one before it. A re-run for the same commit skips the build. A manual run
  (*Run workflow*) deploys only if CI is green for that commit.

The deploy role trusts only `main` of this repo, so a run from any other branch
or from a fork is refused by AWS. Roles and setup: `docs/AWS_SETUP.md`.

Licenses of dependencies: PaddleOCR, EasyOCR and Tesseract (Apache 2.0, as are
PaddleOCR's downloaded models), openpyxl (MIT), Pillow (HPND), NumPy (BSD-3).

---

## 11. Runbook (AWS runner)

The PC keeps working as before; these steps are for the AWS side. Run them on
the Owner's PC after `aws login`. Every alert below arrives by email through
the `calls-log-alerts` topic (docs/AWS_SETUP.md §5).

### Sending Folders to AWS

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\enqueue.py "input_images\Nguyen Van A & 2026-06-01"
.\.venv\Scripts\python.exe -X utf8 tools\enqueue.py --dry-run "input_images\Nguyen Van A & 2026-06-01"
```

It checks every folder first and sends nothing if any is refused (a name that
isn't `<Name> & <yyyy-mm-dd>` with a real date, no images, more than
`max_images`, or the same Person-Day twice). Then, per folder: a new upload,
the images to `inbox/<uploadId>/`, the Folder Status Pending, one queue
message. It moves nothing on the PC. The runner picks the message up within
about 20 seconds and runs at most two Folders at a time. The Day Sheet lands in
`results/<Name> & <date>.xlsx` (each Run a new version), and in the Sheet if
the AWS Sync Switch is on.

If the tool stops with an AWS error, that folder never runs (it isn't
Pending, or has no message): send it again. The folders after it weren't
tried.

### "A Folder is Parked"

The Folder failed 3 Runs; the email gives the last error. It won't run again by
itself. Fix the cause, then re-queue it (below). Logs, one JSON line per step:

```powershell
aws logs tail /aws/lambda/calls-log-runner --since 1d --region ap-southeast-2
```

### Re-queueing a Folder

Runs a stored Folder again without uploading it: after a fix, for a Parked or
Failed Folder, or to redo a Done day.

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\enqueue.py --requeue "Nguyen Van A & 2026-06-01"
.\.venv\Scripts\python.exe -X utf8 tools\enqueue.py --requeue "Nguyen Van A & 2026-06-01" --upload-id 20260601T083005Z-0a1b2c3d
```

It takes the newest upload of that day (or the one named), if it is Done, Failed
or Parked. It becomes Pending with its attempts back at 0, and runs again. The
new Day Sheet is a new version of `results/<Name> & <date>.xlsx`; earlier
versions stay. With the AWS Sync Switch on, the day's tab is overwritten, as
when the PC re-runs a date. Uploads are kept 30 days: if some of its images are
gone, it refuses, and you send the folder again instead.

### Re-sending stored Day Sheets

The AWS `resync_output.py`: it sends the stored Day Sheets to the Web App again,
**whatever the AWS Sync Switch says**, and overwrites the days' tabs. Use it
after the Sheets were fixed or the Web App was down. Try `--dry-run` first:

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\resync.py --dry-run --name "Nguyen Van A"
.\.venv\Scripts\python.exe -X utf8 tools\resync.py --name "Nguyen Van A" --date 2026-06-01
```

`--name` is a name prefix and `--date` a day (`2026-6-1` works too); give
either, both or neither (every stored day). Each day becomes one queue message;
the runner sends the current version of its Day Sheet within a minute or so.
A failed send is tried 3 times, then emailed ("a re-send to the Sheet failed"),
and is never retried on its own: fix the cause and run it again.

### "The runner is stopped (kill switch)"

A guardrail alarm fired and the kill switch turned the runner's queue trigger
off. No new Run starts; Runs already going finish, and queued Folders wait in
the queue for up to 14 days. The alarms:

| Alarm | Fires when |
|---|---|
| `calls-log-runner-dlq` | a message reached the dead-letter queue: it was received 10 times without the runner finishing it, a crash loop (failed Runs park after 3) |
| `calls-log-runner-errors` | 6 or more runner crashes in 15 minutes (a failed Run is not a crash) |
| `calls-log-runner-runaway` | both Run slots busy for N quarter-hours in a row; the alarm's description gives N (7 at 2,048 MB). N is set from the memory size so that, past the free tier, a runaway costs at most $0.50 before the stop ($0.48 at 2,048 MB) |

Find the cause first, with the logs above, and in the dead-letter queue:

```powershell
$acct = aws sts get-caller-identity --query Account --output text
aws sqs get-queue-attributes --queue-url "https://sqs.ap-southeast-2.amazonaws.com/$acct/calls-log-runs-dlq" --attribute-names ApproximateNumberOfMessages --region ap-southeast-2
```

### Turning the runner back on

Only once the cause is fixed (and deployed). A deploy never turns it back on:
Terraform ignores the trigger's on/off flag on purpose.

```powershell
$uuid = aws lambda list-event-source-mappings --function-name calls-log-runner --query "EventSourceMappings[0].UUID" --output text --region ap-southeast-2
aws lambda update-event-source-mapping --uuid $uuid --enabled --region ap-southeast-2
aws lambda get-event-source-mapping --uuid $uuid --query State --region ap-southeast-2   # "Enabled" within a minute
```

The alarm itself returns to OK on its own once its metric is quiet. A message
in the dead-letter queue keeps `calls-log-runner-dlq` in alarm; after reading
it, purge the queue (`aws sqs purge-queue --queue-url <dlq url>`), then send
that Folder again.

### Stopping the runner by hand

If the kill switch emailed that it could NOT stop the runner, or to pause AWS
on purpose:

```powershell
$uuid = aws lambda list-event-source-mappings --function-name calls-log-runner --query "EventSourceMappings[0].UUID" --output text --region ap-southeast-2
aws lambda update-event-source-mapping --uuid $uuid --no-enabled --region ap-southeast-2
```
