"""
Generate human-review artifacts.

Outputs:
- review_report.csv  (one row per uncertain or excluded call)
- processing_log.json (full machine-readable trace)
"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path
from typing import List

from parser import CallRecord
from session_builder import Session

log = logging.getLogger(__name__)

REVIEW_FIELDS = [
    "source_file",
    "raw_text",
    "start_time",
    "raw_duration_seconds",
    "rounded_duration_minutes",
    "end_time",
    "direction",
    "status",
    "countable",
    "ocr_confidence",
    "warnings",
    "suggested_correction",
]


def write_review_csv(records: List[CallRecord], path: Path) -> int:
    """
    Write only records that needs_review or have warnings.
    Returns number of rows written.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Only rows the parser explicitly marked as needing human review.
    # Informational-only warnings (e.g. low_ocr_confidence on a row that
    # parsed cleanly) live in processing_log.json instead.
    flagged = [r for r in records if r.needs_review]

    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for r in flagged:
            writer.writerow({
                "source_file": r.source_file,
                "raw_text": r.raw_text,
                "start_time": r.start_time or "",
                "raw_duration_seconds": "" if r.raw_duration_seconds is None else r.raw_duration_seconds,
                "rounded_duration_minutes": (
                    "" if r.rounded_duration_minutes is None else r.rounded_duration_minutes
                ),
                "end_time": r.end_time or "",
                "direction": r.direction,
                "status": r.status,
                "countable": r.countable,
                "ocr_confidence": f"{r.ocr_confidence:.3f}",
                "warnings": "; ".join(r.warnings),
                "suggested_correction": _suggest_correction(r),
            })

    log.info("Review CSV written: %s (%d flagged)", path, len(flagged))
    return len(flagged)


def _suggest_correction(r: CallRecord) -> str:
    suggestions = []
    if r.start_time is None:
        suggestions.append("verify_start_time")
    if r.raw_duration_seconds is None:
        suggestions.append("verify_duration")
    if "ampm_missing_assumed_24h" in r.warnings:
        suggestions.append("confirm_AM_or_PM")
    if "duration_format_ambiguous_assumed_mm_ss" in r.warnings:
        suggestions.append("confirm_hh:mm_vs_mm:ss")
    if "direction_unknown_for_missed_or_zero" in r.warnings:
        suggestions.append("confirm_call_was_FROM_or_TO_device")
    if any(w.startswith("low_ocr_confidence") for w in r.warnings):
        suggestions.append("manually_re-read_image")
    if any(w.startswith("possible_fuzzy_duplicate") for w in r.warnings):
        suggestions.append("decide_if_duplicate")
    if any(w.startswith("overlap_with") for w in r.warnings):
        suggestions.append("verify_overlap_is_real")
    return "; ".join(suggestions)


def write_processing_log(
    path: Path,
    records: List[CallRecord],
    duplicates_info: list,
    sessions: List[Session],
    stats: dict,
    preprocess_meta: list,
) -> None:
    """Dump the full processing trace as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "preprocessing": preprocess_meta,
        "calls_extracted": [r.to_dict() for r in records],
        "duplicates_removed": duplicates_info,
        "sessions": [s.to_dict() for s in sessions],
        "stats": stats,
    }
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    log.info("Processing log written: %s", path)
