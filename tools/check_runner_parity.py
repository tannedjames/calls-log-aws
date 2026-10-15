"""
Does the AWS runner read a folder exactly like main.py? (Story 3.5)

For every image folder under --dir (the Synthetic Set), reads it twice inside
this process: main.py's way (main.extract_calls over the sorted files, then
dedupe and build_sessions) and the runner's way (cloud.runner.run_local: one
image at a time, in the order the Owner's PC sorts them). Prints one line per
folder; exit code 0 = every folder IDENTICAL, 1 = a difference. CI runs it in
the runner image on the Synthetic Set; it needs no network and writes nothing.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # run as a script from tools/: the pipeline lives one level up

import runtime_env  # noqa: E402,F401  -- before numpy/cv2/paddle

import argparse  # noqa: E402
import logging  # noqa: E402

from main import IMAGE_SUFFIXES, extract_calls  # noqa: E402
from session_builder import build_sessions, dedupe  # noqa: E402

from cloud import runner  # noqa: E402


def main_path(folder: Path) -> dict:
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
    records, _meta, _stats = extract_calls(files)
    sessions, stats = build_sessions(dedupe(records)[0])
    return {"images": len(files), "records": len(records), "countable": stats["countable_calls"],
            "sessions": [[s.start_time, s.end_time] for s in sessions]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, required=True, help="folder holding the image folders")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    folders = sorted(p for p in args.dir.iterdir() if p.is_dir())
    if not folders:
        print(f"no folders in {args.dir}")
        return 1
    different = 0
    for folder in folders:
        want, got = main_path(folder), runner.run_local(folder, [], None)
        same = want == got
        different += not same
        print(f"{folder.name}: {got['images']} images, {len(got['sessions'])} sessions -> "
              + ("IDENTICAL" if same else f"DIFFERENT (main {want}, runner {got})"), flush=True)
    print("Runner parity: " + ("all folders IDENTICAL." if not different else f"{different} DIFFERENT."))
    return 1 if different else 0


if __name__ == "__main__":
    sys.exit(main())
