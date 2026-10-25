"""
Do the data lake's records match the Synthetic Set? (ADR 9, Data System Plan Phase 1)

For every set in truth.json, reads its folder the way the AWS runner does
(one image at a time, in the PC's order), builds the envelope a Done Run
would stage (cloud/lake.py, keyed with the local salt) and checks that:
  * lake.check() finds nothing wrong with it;
  * its calls are the set's ground truth (start, seconds, direction);
  * its sessions are the ones the Day Sheet is written from;
  * no name reaches it.
The low-confidence share is reported, not failed: its limit is a starting
value that Phase 4.5 calibrates on real days.

Prints one line per set; exit code 0 = every set OK, 1 = a difference, 2 = no
Synthetic Set. CI runs it in the runner image; it needs no network, and
writes only to a temporary folder.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # run as a script from tools/: the pipeline lives one level up

import runtime_env  # noqa: E402,F401  -- before numpy/cv2/paddle

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
from collections import Counter  # noqa: E402

from cloud import lake, naming, runner  # noqa: E402
from cloud.storage import IMAGE_SUFFIXES, pc_order  # noqa: E402
from tools.check_synth import expected  # noqa: E402

LOW_CONFIDENCE = "below the confidence threshold"   # lake.check()'s wording for the share


def staged_calls(payload: dict) -> Counter:
    """The staged calls the way check_synth counts parsed ones: (start, seconds, direction)."""
    return Counter((c["start_ts"][11:16], c["duration_s"] or 0, c["direction"]) for c in payload["calls"])


def check_folder(folder: Path, calls: list) -> tuple:
    """(problems, notes) for one set's folder against its truth calls."""
    names = pc_order([p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES])
    records, _ocr = runner.extract([folder / n for n in names])
    kept, sessions, _stats = runner.calls_and_sessions(records)
    payload = runner.local_payload(folder.name, names, kept, sessions)

    found = lake.check(payload)
    problems = [p for p in found if LOW_CONFIDENCE not in p]
    notes = [p for p in found if LOW_CONFIDENCE in p]
    want, got = expected(calls), staged_calls(payload)
    if want != got:
        problems.append(f"calls differ from the truth: {sum((want - got).values())} missing, "
                        f"{sum((got - want).values())} extra")
    staged = [[s["start_ts"][11:16], s["end_ts"][11:16]] for s in payload["sessions"]]
    if staged != [[s.start_time, s.end_time] for s in sessions]:
        problems.append("sessions differ from the ones the Day Sheet is written from")
    day = naming.parse(folder.name)
    text = json.dumps(payload, ensure_ascii=False)
    if day is None or day.name in text or naming.norm(day.name) in naming.norm(text):
        problems.append("a name reached the records")
    return problems, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", type=Path, required=True, help="folder written by gen_synth.py")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    truth_file = args.dir / "truth.json"
    if not truth_file.is_file():
        print(f"No Synthetic Set at {args.dir}: run tools/gen_synth.py first.")
        return 2
    truth = json.loads(truth_file.read_text(encoding="utf-8"))
    failed = 0
    for name, s in truth["sets"].items():
        if not s["calls"]:
            continue
        folder = args.dir / s["dir"]
        if not folder.is_dir():
            print(f"{name}: folder {s['dir']!r} is missing -> DIFFERENT (re-run gen_synth.py)")
            failed += 1
            continue
        problems, notes = check_folder(folder, s["calls"])
        failed += bool(problems)
        print(f"{name}: {len(s['calls'])} calls -> " + ("OK" if not problems else "DIFFERENT"), flush=True)
        for line in problems[:10]:
            print(f"    {line}")
        for line in notes:
            print(f"    note: {line}")
    print("Lake parity: " + ("every set OK." if not failed else f"{failed} set(s) differ."))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
