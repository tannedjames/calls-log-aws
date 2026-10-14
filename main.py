"""
OCR Call-Session Extractor — main entry point.

Two modes:

* Single-folder (default):
    --images <folder of JPGs>  --output <path/to/file.xlsx>
    Produces filled_sprs.xlsx + review_report.csv + processing_log.json.

* Batch (--batch):
    --images <parent folder>  --output <output folder>
    Each subfolder of --images is treated as one independent batch. The
    subfolder's name becomes the xlsx filename (e.g.
    "Nguyen Van A & 2026-05-11" -> "Nguyen Van A & 2026-05-11.xlsx"). On
    filename collision, "(1)", "(2)", ... is appended. After a batch's xlsx is
    written and synced successfully, the source subfolder is moved to
    input_images/_processed/ (see _archive_or_delete). Loose files directly
    inside --images are ignored. No review CSV or processing log is written
    in batch mode.
"""

from __future__ import annotations

import runtime_env  # noqa: F401  -- must be first: sets OpenMP env before natives

import argparse
import logging
import re
import shutil
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from config import CONFIG
from image_preprocess import preprocess
from ocr import run_ocr, group_lines_into_rows, row_text, row_confidence
from parser import CallRecord, parse_row
from session_builder import dedupe, build_sessions
from excel_writer import write_sessions
from review_report import write_review_csv, write_processing_log
from sheets_sync import sync_day, SheetsSyncError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("main")


# Photos shared through chat apps are not always JPG: Android screenshots
# arrive as PNG and newer phones send WEBP.
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def gather_jpg_files(folder: Path) -> List[Path]:
    files = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    if not files:
        raise SystemExit(
            f"No image files found in {folder} "
            f"(looked for {', '.join(IMAGE_SUFFIXES)})"
        )
    if len(files) > CONFIG.max_images:
        raise SystemExit(
            f"Found {len(files)} JPG files, which exceeds max_images "
            f"({CONFIG.max_images}). Reduce the input or raise the limit in config.py."
        )
    if len(files) > CONFIG.safe_warn_images:
        log.warning(
            "Found %d JPG files (warn threshold %d). Proceeding.",
            len(files), CONFIG.safe_warn_images,
        )
    return files


def extract_calls(files: List[Path]) -> tuple:
    """
    Run preprocessing + OCR + row parsing for every input file.

    Per-image failures are tolerated (one bad photo should not sink a batch),
    but the caller is told how many failed. A run where *every* image failed
    used to be indistinguishable from a genuinely empty call log, and produced
    a blank spreadsheet that looked like a successful result.
    """
    records: List[CallRecord] = []
    preprocess_meta = []
    ocr_failures = 0
    ocr_empty = 0

    total = len(files)
    for idx, path in enumerate(files, 1):
        started = time.monotonic()
        log.info("Processing [%d/%d] %s", idx, total, path.name)
        try:
            img, meta = preprocess(path)
            preprocess_meta.append(meta)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            log.error("Preprocess failed for %s: %s", path.name, exc)
            preprocess_meta.append({"source_file": path.name, "error": str(exc)})
            ocr_failures += 1
            continue

        try:
            lines = run_ocr(img)
        except KeyboardInterrupt:
            # Propagate: main() reports it properly instead of it surfacing as
            # an unexplained traceback from deep inside an engine.
            raise
        except Exception as exc:
            log.error("OCR failed for %s: %s", path.name, exc)
            ocr_failures += 1
            continue

        if not lines:
            ocr_empty += 1

        rows = group_lines_into_rows(lines)
        log.info(
            "  %s -> %d OCR lines, %d rows (%.1fs)",
            path.name, len(lines), len(rows), time.monotonic() - started,
        )

        img_h = float(img.size[1]) or 1.0
        for row in rows:
            text = row_text(row)
            if not text:
                continue
            conf = row_confidence(row)
            # Vertical centre of the row as a fraction of image height, so the
            # parser can tell a call row from the status-bar clock.
            y_frac = (min(l.box[1] for l in row) + max(l.box[3] for l in row)) / 2.0 / img_h
            rec = parse_row(path.name, text, conf, y_frac=y_frac)
            if rec.start_time is None and rec.raw_duration_seconds is None:
                continue
            records.append(rec)

    if ocr_failures:
        log.error("OCR failed outright on %d/%d image(s)", ocr_failures, total)
    if ocr_empty:
        log.warning("OCR returned zero text lines on %d/%d image(s)",
                    ocr_empty, total)

    stats = {
        "images": total,
        "ocr_failures": ocr_failures,
        "ocr_empty": ocr_empty,
        "ocr_ok": total - ocr_failures,
    }
    return records, preprocess_meta, stats


_DATE_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")


def parse_name_date(folder_name: str) -> Optional[Tuple[str, str]]:
    """
    Split a batch folder name like "Nguyễn Văn A & 2026-05-25" into
    ("Nguyễn Văn A", "2026-05-25"). Returns None if no yyyy-mm-dd date is found.
    Tolerates the "<name> & <date>" convention as well as a trailing date.
    """
    m = _DATE_RE.search(folder_name)
    if not m:
        return None
    y, mo, d = (int(g) for g in m.groups())
    date = f"{y:04d}-{mo:02d}-{d:02d}"
    name = folder_name[: m.start()].rstrip()
    # Drop a trailing "&"/"-" separator left over from the convention.
    name = re.sub(r"[\s&\-]+$", "", name).strip()
    if not name:
        return None
    return name, date


def _next_available_xlsx(out_dir: Path, base_name: str) -> Path:
    """
    Return a Path inside out_dir based on `base_name` (no extension), appending
    (1), (2), ... if "<base>.xlsx" already exists.
    """
    candidate = out_dir / f"{base_name}.xlsx"
    if not candidate.exists():
        return candidate
    n = 1
    while True:
        candidate = out_dir / f"{base_name}({n}).xlsx"
        if not candidate.exists():
            return candidate
        n += 1


def _archive_or_delete(subfolder: Path, images_root: Path) -> None:
    """
    Retire a processed input folder.

    Default is to MOVE it into ``<images_root>/_processed/`` rather than delete
    it. ``shutil.rmtree`` bypasses the Windows Recycle Bin, so the old
    behaviour made a mis-run unrecoverable — the source photos were simply
    gone. Moving is instant (same volume) and reversible.
    """
    if not CONFIG.archive_input_folders:
        shutil.rmtree(subfolder)
        log.info("  -> deleted source folder %s", subfolder)
        return

    archive_root = images_root / CONFIG.input_archive_dirname
    archive_root.mkdir(parents=True, exist_ok=True)

    target = archive_root / subfolder.name
    if target.exists():
        target = archive_root / f"{subfolder.name} ({datetime.now():%Y%m%d-%H%M%S})"
    shutil.move(str(subfolder), str(target))
    log.info("  -> archived source folder to %s", target)


def _prune_archive(images_root: Path) -> None:
    """Delete archived folders older than the configured retention window."""
    days = CONFIG.input_archive_retention_days
    if not CONFIG.archive_input_folders or days <= 0:
        return
    archive_root = images_root / CONFIG.input_archive_dirname
    if not archive_root.is_dir():
        return
    cutoff = time.time() - days * 86400
    for entry in archive_root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry)
                log.info("Pruned archived folder older than %d days: %s",
                         days, entry.name)
        except OSError as exc:
            log.warning("Could not prune %s: %s", entry, exc)


def _process_one_folder(
    subfolder: Path,
    template: Path,
    out_dir: Path,
    delete_after: bool,
):
    """
    Run the full pipeline on a single input subfolder and write
    <out_dir>/<subfolder.name>.xlsx (auto-numbered). On success, optionally
    delete the source subfolder.
    Returns the output Path on success, None if the folder had no usable JPGs.
    """
    try:
        files = gather_jpg_files(subfolder)
    except SystemExit as exc:
        log.warning("Skipping %s: %s", subfolder.name, exc)
        return None

    log.info("Batch: processing %r (%d image(s))", subfolder.name, len(files))

    records, _meta, ocr_stats = extract_calls(files)
    records, _dups = dedupe(records)
    sessions, stats = build_sessions(records)
    log.info(
        "  -> %d session(s), %d countable call(s)",
        len(sessions), stats["countable_calls"],
    )

    # Refuse to write a blank spreadsheet. Zero sessions from a folder full of
    # call-log images means the pipeline failed, not that nobody made a call --
    # and writing the empty template anyway (then archiving the source images)
    # is how a broken OCR engine stayed invisible for weeks.
    if not sessions:
        raise RuntimeError(
            f"no sessions extracted from {len(files)} image(s) "
            f"({ocr_stats['ocr_failures']} OCR failure(s), "
            f"{ocr_stats['ocr_empty']} image(s) with no text). "
            f"Refusing to write an empty spreadsheet; source folder kept. "
            f"Re-run with --debug to see why the OCR engine failed."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    target = _next_available_xlsx(out_dir, subfolder.name)
    write_sessions(template, target, sessions)
    log.info("  -> wrote %s", target)

    # Optional: push the same sessions to Google Sheets via the Web App.
    # The local xlsx above is always produced; this is an add-on.
    sync_ok = True
    if CONFIG.sheets_sync_enabled:
        parsed = parse_name_date(subfolder.name)
        if parsed is None:
            sync_ok = False
            log.error(
                "  -> sheets sync skipped: could not parse name/date from %r",
                subfolder.name,
            )
        else:
            name, date = parsed
            try:
                sync_day(name, date, sessions)
                log.info("  -> synced to Google Sheets (%s %s)", name, date)
            except SheetsSyncError as exc:
                sync_ok = False
                log.error("  -> sheets sync FAILED for %s: %s", subfolder.name, exc)

    if delete_after and sync_ok:
        try:
            _archive_or_delete(subfolder, subfolder.parent)
        except OSError as exc:
            log.error(
                "  -> could NOT retire %s: %s. Leaving folder in place.",
                subfolder, exc,
            )
    elif delete_after and not sync_ok:
        log.warning(
            "  -> keeping source folder %s because Google Sheets sync did not "
            "succeed (xlsx was still written). Re-run after fixing sync.",
            subfolder,
        )

    return target


def _run_batch(images_root: Path, template: Path, out_dir: Path,
               delete_after: bool) -> int:
    """Run batch mode: process every subfolder of images_root independently."""
    if out_dir.exists() and not out_dir.is_dir():
        raise SystemExit(
            f"--output must be a directory in batch mode: {out_dir}"
        )

    # Never re-process the archive of already-handled folders, and skip the
    # dot-directories some sync clients leave behind.
    skip_names = {CONFIG.input_archive_dirname.lower(), "_processed", "_archive"}
    subfolders = sorted(
        p for p in images_root.iterdir()
        if p.is_dir()
        and p.name.lower() not in skip_names
        and not p.name.startswith(".")
    )
    if not subfolders:
        raise SystemExit(
            f"No subfolders found inside {images_root}. In batch mode each "
            f"input batch must be its own subfolder (e.g. 'Nguyen Van A & 2026-05-11')."
        )

    log.info("Batch mode: %d folder(s) to process", len(subfolders))
    successes = 0
    failures = []

    for sub in subfolders:
        try:
            result = _process_one_folder(sub, template, out_dir, delete_after)
            if result is not None:
                successes += 1
        except KeyboardInterrupt:
            log.warning(
                "Interrupted while processing %r. %d folder(s) already "
                "completed; their source folders were retired, the rest are "
                "untouched. Re-run to continue.", sub.name, successes,
            )
            raise
        except Exception as exc:
            log.exception("Batch failed for %s", sub.name)
            failures.append((sub.name, str(exc)))

    _prune_archive(images_root)

    log.info(
        "Batch done. Succeeded: %d. Failed: %d. Skipped: %d.",
        successes, len(failures), len(subfolders) - successes - len(failures),
    )
    for name, err in failures:
        log.info("  failed: %s -- %s", name, err)
    return 0 if not failures else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract call sessions from JPG call-log images."
    )
    parser.add_argument("--images", required=True, type=Path,
                        help="Folder containing JPG call-log screenshots, OR "
                             "(with --batch) a parent folder of per-batch "
                             "subfolders.")
    parser.add_argument("--template", required=True, type=Path,
                        help="Path to sprs.xlsx template")
    parser.add_argument("--output", required=True, type=Path,
                        help="Path to write filled spreadsheet, OR (with "
                             "--batch) the output directory.")
    parser.add_argument("--batch", action="store_true",
                        help="Process every subfolder of --images as a "
                             "separate batch. Each subfolder's name becomes "
                             "the xlsx filename (auto-numbered on collision) "
                             "and the subfolder is moved to "
                             f"'{CONFIG.input_archive_dirname}' on success.")
    parser.add_argument("--keep-input", action="store_true",
                        help="In batch mode, keep source subfolders instead "
                             "of deleting them after a successful run.")
    parser.add_argument("--review-csv", type=Path, default=None,
                        help="Path for review_report.csv (default: alongside "
                             "output). Ignored in batch mode.")
    parser.add_argument("--log-json", type=Path, default=None,
                        help="Path for processing_log.json (default: "
                             "alongside output). Ignored in batch mode.")
    parser.add_argument("--ocr-engine", choices=("paddle", "easyocr", "tesseract"),
                        default=None, help="Override OCR engine")
    parser.add_argument("--allow-engine-fallback", action="store_true",
                        help="Let a failing OCR engine fall back to another "
                             "one. Off by default: silent fallback hides "
                             "engine misconfiguration and can load two "
                             "OpenMP runtimes into one process on Windows.")
    parser.add_argument("--delete-input", action="store_true",
                        help="In batch mode, permanently delete source "
                             "subfolders instead of moving them to "
                             f"'{CONFIG.input_archive_dirname}'. Deletion "
                             "does NOT use the Recycle Bin.")
    parser.add_argument("--debug", action="store_true",
                        help="Verbose logging, including per-engine tracebacks.")
    args = parser.parse_args(argv)

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.ocr_engine:
        CONFIG.ocr_engine = args.ocr_engine
    if args.allow_engine_fallback:
        CONFIG.ocr_strict_engine = False
    if args.delete_input:
        CONFIG.archive_input_folders = False

    if not args.images.is_dir():
        raise SystemExit(f"--images must be a directory: {args.images}")
    if not args.template.is_file():
        raise SystemExit(f"--template not found: {args.template}")

    if args.batch:
        return _run_batch(
            images_root=args.images,
            template=args.template,
            out_dir=args.output,
            delete_after=not args.keep_input,
        )

    # ---- Single-folder mode (original behavior) ----
    review_csv = args.review_csv or args.output.with_name("review_report.csv")
    log_json = args.log_json or args.output.with_name("processing_log.json")

    files = gather_jpg_files(args.images)
    log.info("Processing %d image(s)", len(files))

    records, preprocess_meta, ocr_stats = extract_calls(files)
    log.info("Parsed %d candidate call records", len(records))
    if ocr_stats["ocr_ok"] == 0:
        raise SystemExit(
            f"OCR failed on all {ocr_stats['images']} image(s). "
            f"Re-run with --debug to see the engine traceback."
        )

    records, duplicates_info = dedupe(records)
    log.info("After dedupe: %d records, %d duplicates removed",
             len(records), len(duplicates_info))

    sessions, stats = build_sessions(records)
    log.info(
        "Built %d session(s). Countable calls: %d. Exceptions used: %d. Overlaps: %d.",
        len(sessions), stats["countable_calls"],
        stats["exceptions_used_total"], stats["overlaps_total"],
    )

    write_stats = write_sessions(args.template, args.output, sessions)
    stats.update(write_stats)

    flagged = write_review_csv(records, review_csv)
    stats["review_rows"] = flagged

    write_processing_log(log_json, records, duplicates_info, sessions, stats, preprocess_meta)

    log.info("Done. Output: %s", args.output)
    log.info("Review: %s (%d flagged rows)", review_csv, flagged)
    log.info("Log:    %s", log_json)
    return 0


def _run() -> int:
    try:
        return main()
    except KeyboardInterrupt:
        # A KeyboardInterrupt you did not type almost always means two OpenMP
        # runtimes (paddle + torch) ended up in one process and one of them
        # delivered a console CTRL event. runtime_env.py + the engine guard in
        # ocr.py prevent that; if it still happens, this message says so
        # instead of dumping a bare traceback.
        log.warning(
            "Interrupted (KeyboardInterrupt). If you did not press Ctrl+C, "
            "this is almost certainly the paddle/torch OpenMP clash: run with "
            "--ocr-engine paddle (default) and WITHOUT --allow-engine-fallback "
            "so only one framework is ever loaded. Nothing was lost — "
            "completed folders are already written to --output."
        )
        return 130


if __name__ == "__main__":
    sys.exit(_run())
