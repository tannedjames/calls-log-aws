"""
Single-image OCR diagnostic.

Prints, for ONE image: the raw OCR lines, how they group into rows, and what
the parser makes of each row -- including exactly which rule rejected it.

Usage:
    .\.venv\Scripts\python.exe diagnose.py "input_images\<folder>\<file>.jpg"

If no path is given, the first image in the first input subfolder is used.
"""

from __future__ import annotations

import runtime_env  # noqa: F401
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

from config import CONFIG
from image_preprocess import preprocess
from ocr import run_ocr, group_lines_into_rows, row_text, row_confidence
from parser import parse_row, RE_STATUS_BAR, RE_PROMO_ROW, RE_CALL_EVIDENCE


def pick_default() -> Path:
    root = Path("input_images")
    for sub in sorted(p for p in root.iterdir() if p.is_dir() and p.name != "_processed"):
        for f in sorted(sub.iterdir()):
            if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
                return f
    raise SystemExit("No input images found.")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else pick_default()
    if not path.is_file():
        raise SystemExit(f"Not a file: {path}")

    print(f"\n=== IMAGE ===\n{path}")
    img, meta = preprocess(path)
    print(f"  classified : {meta.get('classification')}")
    print(f"  steps      : {meta.get('steps')}")
    print(f"  size       : {meta.get('original_size')} -> {meta.get('final_size')}")

    lines = run_ocr(img)
    print(f"\n=== RAW OCR LINES ({len(lines)}) ===")
    for i, ln in enumerate(lines, 1):
        x0, y0, x1, y1 = (int(v) for v in ln.box)
        print(f"  {i:2}. conf={ln.confidence:.2f} y={y0:>5}-{y1:<5} {ln.text!r}")

    rows = group_lines_into_rows(lines)
    print(f"\n=== GROUPED ROWS ({len(rows)}) ===")

    kept = dropped = 0
    for i, row in enumerate(rows, 1):
        text = row_text(row)
        conf = row_confidence(row)
        print(f"\n  ROW {i:2}: {text!r}")
        if not text:
            print("     -> empty, skipped")
            dropped += 1
            continue

        # Which pre-filter, if any, would swallow this row?
        if RE_STATUS_BAR.search(text):
            print(f"     -> DROPPED by RE_STATUS_BAR "
                  f"(matched {RE_STATUS_BAR.search(text).group(0)!r})")
            dropped += 1
            continue
        if RE_PROMO_ROW.search(text):
            print(f"     -> DROPPED by RE_PROMO_ROW "
                  f"(matched {RE_PROMO_ROW.search(text).group(0)!r})  <<< suspicious")
            dropped += 1
            continue

        rec = parse_row(path.name, text, conf)
        ev = RE_CALL_EVIDENCE.search(text)
        ev_txt = repr(ev.group(0)) if ev else "None"
        print(f"     start={rec.start_time} dur={rec.raw_duration_seconds}s "
              f"rounded={rec.rounded_duration_minutes}m end={rec.end_time}")
        print(f"     direction={rec.direction} status={rec.status} "
              f"countable={rec.countable} evidence={ev_txt}")
        if rec.warnings:
            print(f"     warnings={rec.warnings}")

        if rec.countable and rec.start_time and rec.end_time:
            kept += 1
        else:
            dropped += 1
            reasons = []
            if not rec.start_time:
                reasons.append("no start_time")
            if not rec.end_time:
                reasons.append("no end_time")
            if not rec.countable:
                reasons.append("not countable")
            print(f"     -> NOT USABLE: {', '.join(reasons)}")

    print(f"\n=== SUMMARY ===")
    print(f"  usable call rows : {kept}")
    print(f"  discarded rows   : {dropped}")
    if kept == 0:
        print("\n  Zero usable rows. The ROW dumps above show which rule rejected each\n"
              "  one -- look for a repeated 'DROPPED by' or 'NOT USABLE' reason.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
