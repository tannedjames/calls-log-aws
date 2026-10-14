"""
Central configuration for the OCR Call-Session Extractor.

All tunable behavior (gap minutes, rounding thresholds, confidence cutoffs,
OCR engine choice, time format) lives here so behavior can be tweaked without
editing the rest of the code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from runtime_env import available_cpus

# ---------------------------------------------------------------------------
# Credentials live in secrets_local.py, which is gitignored. They used to be
# hardcoded here, which would have published the Web App secret the moment this
# repo was pushed -- anyone holding it can write to every employee's
# spreadsheet.
#
# Missing file -> empty strings -> sheets_sync.py raises a clear error rather
# than silently skipping the push. Copy secrets_local.example.py to get started.
# ---------------------------------------------------------------------------
try:
    from secrets_local import SHEETS_WEBAPP_URL as _WEBAPP_URL
    from secrets_local import SHEETS_SYNC_SECRET as _SYNC_SECRET
except ImportError:  # pragma: no cover - depends on local setup
    _WEBAPP_URL = ""
    _SYNC_SECRET = ""


@dataclass
class Config:
    # ----- Limits -----
    max_images: int = 40
    safe_warn_images: int = 35  # warn (not fail) above this many files

    # ----- OCR -----
    # "paddle" (preferred), "easyocr", or "tesseract" (fallback).
    ocr_engine: str = "paddle"
    # PaddleOCR/EasyOCR/Tesseract language hints
    ocr_languages: List[str] = field(default_factory=lambda: ["japan", "en", "vi"])
    # Tesseract uses different language codes
    tesseract_langs: str = "jpn+eng+vie"
    # Below this OCR confidence a parsed value is flagged for human review.
    ocr_confidence_threshold: float = 0.85
    # If True, a failure of `ocr_engine` aborts instead of silently degrading
    # to a weaker engine. Silent fallback is how every image ended up going
    # through EasyOCR without anyone noticing, and how paddle+torch got loaded
    # into one process (see runtime_env.py).
    ocr_strict_engine: bool = True

    # ----- PaddleOCR 3.x detector/recogniser tuning -----
    #
    # SPEED NOTE. lang="japan" makes PaddleOCR pick PP-OCRv5_**server**_det and
    # PP-OCRv5_**server**_rec -- the heaviest models available. With oneDNN
    # disabled (mandatory, see paddle_enable_mkldnn) that is ~60s per
    # screenshot and 2-7 min per upscaled photo. Overriding the recogniser to
    # the mobile model and dropping the detector resolution back to the stock
    # 960 brings it to a few seconds per image. PP-OCRv5 mobile still covers
    # Japanese, English and digits, which is all these screens contain.
    #
    # Set paddle_rec_model_name = None to go back to the server model if you
    # ever need maximum accuracy and can afford the time.
    paddle_rec_model_name: str | None = "PP-OCRv5_mobile_rec"
    paddle_det_model_name: str | None = None   # None = pipeline default
    # Detector input cap. Stock is 960. Text on these screens is large, so the
    # extra resolution bought nothing and cost ~4x in detection time.
    paddle_det_limit_side_len: int = 960
    # Lower thresholds than stock: the duration text "(38秒)" is low-contrast
    # grey-on-white and gets dropped at the defaults.
    paddle_det_thresh: float = 0.25
    paddle_det_box_thresh: float = 0.55
    # Expand detected boxes a little so digits at the box edge are not clipped.
    paddle_det_unclip_ratio: float = 1.8
    # Keep low-score recognitions; the parser is strict enough to reject junk,
    # and dropping them loses real call rows.
    paddle_rec_score_thresh: float = 0.0
    # Per-text-line upside-down classifier. These screens are always upright
    # once the page orientation is right, so this runs a model ~20x per image
    # for nothing.
    paddle_use_textline_orientation: bool = False
    # Whole-page 90/180/270 detection, the only rotation handling we have,
    # since pytesseract/OSD is not installed. It runs only on landscape
    # images (sideways screens); portrait images are already upright, and
    # classifying them turned upright photos by 90 degrees. Keep it on so
    # sideways photos are not skipped; False turns it off for every image.
    paddle_use_doc_orientation: bool = True
    # oneDNN/MKL-DNN acceleration. MUST stay False on Windows with
    # paddlepaddle 3.x: the PIR executor cannot convert a double-array
    # attribute for the oneDNN kernels and every inference dies with
    #   NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support
    #   [pir::ArrayAttribute<pir::DoubleAttribute>]  (onednn_instruction.cc:118)
    # Disabling it routes inference through the plain CPU kernels: slower per
    # image, but it actually works. Try True again after a paddle upgrade.
    paddle_enable_mkldnn: bool = False
    # Worker threads for the CPU kernels: paddle's own default of 10 (4 was a
    # self-inflicted 2x), but never more than the CPUs this process may use,
    # so a 2-vCPU container or Lambda gets 2 rather than oversubscribing.
    paddle_cpu_threads: int = field(default_factory=lambda: min(10, available_cpus()))

    # ----- Row grouping -----
    # Fraction of the median line height used to grow a row's y-band.
    row_group_y_tolerance: float = 0.6

    # ----- Preprocessing -----
    # At/above this fraction of "bright" pixels an image is treated as a
    # screenshot rather than a photo of a screen. Measured on this dataset:
    # screenshots ~0.91, photos 0.55-0.71.
    screenshot_bright_frac: float = 0.82
    # A candidate screen contour must occupy between these fractions of the
    # frame to be accepted for perspective rectification.
    screen_min_area_frac: float = 0.15
    screen_max_area_frac: float = 0.97
    # Upscale targets after cleanup.
    # Was 2400: upscaling a 960x1280 photo to 1800x2400 and then having the
    # detector shrink it back to its limit was pure wasted work, and it is the
    # main reason photos took 2-7 minutes each while screenshots took 60s.
    photo_min_long_edge: int = 1600
    screenshot_min_long_edge: int = 1600
    warn_on_landscape: bool = True

    # ----- 12-hour clock recovery -----
    # The call-log app displays "03:08 PM". When OCR drops the AM/PM marker the
    # old code assumed 24-hour, so a 2:23 PM call became 02:23 -- which is where
    # every phantom pre-dawn session came from. No employee makes calls between
    # midnight and 07:00, so an unmarked time landing in that window is a lost
    # PM marker and gets shifted by 12 hours.
    # Set to 0 to disable the correction.
    assume_pm_before_hour: int = 7

    # A row carrying only a time and no direction marker is normally a call
    # whose marker OCR split off -- except in the top strip of the screen,
    # where it is the status-bar clock. Rows above this fraction of image
    # height with no marker are ignored.
    status_bar_top_frac: float = 0.08

    # ----- Session rules -----
    normal_gap_minutes: int = 5
    post_9am_exception_gap_minutes: int = 10
    post_9am_exception_limit: int = 4
    post_9am_start: str = "09:00"

    # A session whose start equals its end (a lone 0-second call) adds nothing
    # to the daily TOTAL, but the call did happen and counts per the spec --
    # and dropping it corrupts B3/first-start when the day opens with a
    # 0-second call, making an on-time start look late. Keep such rows.
    drop_standalone_zero_sessions: bool = False

    # ----- Duration rounding -----
    # If remaining seconds >= this threshold, round up. Otherwise round down.
    round_up_seconds_threshold: int = 20

    # ----- Output formatting -----
    output_time_format: str = "HH:mm"      # excel time format for B/C
    duration_format: str = "[h]:mm"        # excel format for D and B2

    # ----- Spreadsheet layout -----
    sheet_name: str = "Trang tính1"
    first_session_row: int = 7
    last_session_row: int = 22  # 16 sessions fit in template by default
    col_index: str = "A"
    col_start: str = "B"
    col_end: str = "C"
    col_span: str = "D"
    cell_total_span: str = "B2"
    cell_first_start: str = "B3"

    # If True, allow inserting rows below row 22 when more than 16 sessions.
    # If False, stop with an error.
    allow_extra_rows: bool = True

    # ----- Batch input handling -----
    # After a batch's xlsx is written (and synced), move the source subfolder
    # into this folder instead of deleting it outright. `shutil.rmtree` does
    # NOT use the Recycle Bin, so a wrong run used to be unrecoverable.
    # Set archive_input_folders = False to hard-delete (old behaviour).
    archive_input_folders: bool = True
    input_archive_dirname: str = "_processed"
    # Delete archived folders older than this many days on each run (0 = keep
    # forever).
    input_archive_retention_days: int = 30

    # ----- Google Sheets sync (optional) -----
    # When enabled, after a folder's xlsx is written the tool POSTs the day's
    # sessions to the Apps Script Web App (apps_script/WebAppSync.gs), which
    # creates the yyyy-mm-dd worksheet, writes the data, and adds the
    # month-sheet hyperlink. The local xlsx is still produced regardless.
    sheets_sync_enabled: bool = True
    # The deployed Web App /exec URL (Deploy > New deployment > Web app).
    sheets_webapp_url: str = _WEBAPP_URL
    # Shared token; must equal SECRET inside WebAppSync.gs.
    sheets_sync_secret: str = _SYNC_SECRET
    # Network timeout for the Web App call, in seconds.
    sheets_sync_timeout: int = 60
    # How many times to attempt the POST before giving up (transient DNS /
    # timeout / reset failures are common on flaky office wifi).
    sheets_sync_retries: int = 3


CONFIG = Config()
