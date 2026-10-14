"""
Check the OCR pipeline against a Synthetic Set's ground truth.

Reads every set written by tools/gen_synth.py through the same code main.py
uses (preprocess -> OCR -> row parsing -> dedupe) and compares the parsed
calls with truth.json. Nothing is synced, written or moved, and only
fictional images are read. Needs the OCR models (first run downloads them).

Usage:
    python tools/check_synth.py [--dir synthetic] [--threads N] [--verbose]

Exit code 0 = every call matched in every set, 1 = a mismatch, 2 = no set.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# How the parser reports each kind of call. A missed call has no duration.
KIND_TO_DIRECTION = {"out": "outgoing", "in": "incoming", "miss": "incoming"}


def expected(calls: list[dict]) -> Counter:
    return Counter((c["start"], 0 if c["kind"] == "miss" else c["secs"],
                    KIND_TO_DIRECTION[c["kind"]]) for c in calls)


def parsed(records) -> Counter:
    return Counter((r.start_time, r.raw_duration_seconds or 0, r.direction)
                   for r in records)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, default=ROOT / "synthetic",
                    help="folder written by gen_synth.py (default: synthetic/)")
    ap.add_argument("--threads", type=int,
                    help="CPU threads for OCR; set it to the runner's vCPUs")
    ap.add_argument("--verbose", action="store_true", help="show the pipeline's INFO log")
    args = ap.parse_args(argv)

    truth_file = args.dir / "truth.json"
    if not truth_file.is_file():
        print(f"No Synthetic Set at {args.dir}: run tools/gen_synth.py first.")
        return 2
    truth = json.loads(truth_file.read_text(encoding="utf-8"))

    if args.threads:
        # Before runtime_env and paddle load: runtime_env only sets defaults.
        os.environ["OMP_NUM_THREADS"] = os.environ["MKL_NUM_THREADS"] = str(args.threads)
    sys.path.insert(0, str(ROOT))
    import runtime_env  # noqa: F401  -- before numpy/cv2/paddle

    from config import CONFIG
    if args.threads:
        CONFIG.paddle_cpu_threads = args.threads
    from main import extract_calls, gather_jpg_files
    from session_builder import dedupe
    if not args.verbose:
        logging.getLogger().setLevel(logging.WARNING)

    failed = 0
    for name, s in truth["sets"].items():
        if not s["calls"]:
            continue
        if not (args.dir / s["dir"]).is_dir():
            print(f"{name}: folder {s['dir']!r} is missing -> MISMATCH (re-run gen_synth.py)")
            failed += 1
            continue
        files = gather_jpg_files(args.dir / s["dir"])
        records, _meta, _stats = extract_calls(files)
        kept, _dups = dedupe(records)
        want, got = expected(s["calls"]), parsed(kept)
        missing, extra = want - got, got - want
        ok = not missing and not extra
        failed += not ok
        print(f"{name}: {len(files)} image(s), {sum(want.values())} expected, "
              f"{sum(got.values())} parsed, {sum((want & got).values())} matched "
              f"-> {'OK' if ok else 'MISMATCH'}", flush=True)
        for label, diff in (("missing", missing), ("extra", extra)):
            # key=str: a parsed row can lack a start time (None).
            for (start, secs, direction), n in sorted(diff.items(), key=str)[:10]:
                print(f"    {label}: {start} {direction} {secs}s" + (f" x{n}" if n > 1 else ""))
    print("Synthetic Set: " + ("all sets match." if not failed else f"{failed} set(s) differ."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
