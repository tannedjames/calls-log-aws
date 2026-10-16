"""
Story 5.2 (FR-28, SM-1): compare the PC's Day Sheets with AWS's during the
Comparison Period, from the Owner's PC after `aws login`.

    python tools/compare_outputs.py --date 2026-06-01
    python tools/compare_outputs.py --since 2026-06-01 --key D:\\private\\compare-key.txt

For every Person-Day in range it reads the Sessions of the PC's file (the one
resync_output picks) and of the current results/<Person-Day>.xlsx in AWS,
with resync_output.read_sessions, and prints

    F3 2026-06-01: 4 sessions -> IDENTICAL
    F4 2026-06-01: session 2: PC 10:05-11:00, AWS 10:05-11:03 -> DIFFERENT
    F5 2026-06-02: MISSING IN AWS

F-labels, dates, counts and times only, never a name, so the output can be
pasted into the runbook log or the case study. --key writes which Person-Day
each label is to a file outside the repo (refused inside it). Cutover needs
every day IDENTICAL; one difference stops the plan until it is explained.

Exit code 0 = all IDENTICAL, 1 = a difference, a missing day or AWS failed,
2 = bad usage. Nothing in AWS or on the PC is changed.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud import naming  # noqa: E402
from tools.aws_cli import AwsError, account_id, call, run_cli  # noqa: E402
from tools.resync import normal_date  # noqa: E402

PREFIX = "results/"


def inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT)
        return True
    except ValueError:
        return False


def first_difference(pc: List[tuple], aws: List[tuple]) -> Optional[str]:
    for n, (a, b) in enumerate(zip(pc, aws), 1):
        if a != b:
            return f"session {n}: PC {a[0]}-{a[1]}, AWS {b[0]}-{b[1]}"
    if len(pc) != len(aws):
        return f"PC has {len(pc)} sessions, AWS {len(aws)}"
    return None


def sessions(path: Path) -> List[tuple]:
    import resync_output

    return [(s.start_time, s.end_time) for s in resync_output.read_sessions(path)]


def aws_days(bucket: str, *, run=subprocess.run) -> Dict[str, naming.PersonDay]:
    listing = call("s3api", "list-objects-v2", {"Bucket": bucket, "Prefix": PREFIX}, run=run)
    days = {}
    for obj in listing.get("Contents", []):
        stem = obj["Key"][len(PREFIX):]
        day = naming.parse(stem[:-len(".xlsx")]) if stem.endswith(".xlsx") and "/" not in stem else None
        if day is not None and naming.result_key(day) == obj["Key"]:
            days[day.key] = day
    return days


def main(argv: Optional[List[str]] = None, *, run=subprocess.run) -> int:
    import resync_output

    ap = argparse.ArgumentParser(description="Compare the PC's Day Sheets with AWS's (calls-log, Story 5.2).")
    ap.add_argument("--output", type=Path, default=ROOT / "output", help="the PC's output folder")
    when = ap.add_mutually_exclusive_group(required=True)
    when.add_argument("--date", help="one day (yyyy-mm-dd)")
    when.add_argument("--since", help="this day and every later one")
    ap.add_argument("--key", type=Path, help="write which Person-Day each F-label is here (outside the repo)")
    args = ap.parse_args(argv)
    start = normal_date(args.date or args.since)
    if start is None:
        print("the date must be a real yyyy-mm-dd date", file=sys.stderr)
        return 2
    if args.key is not None and inside_repo(args.key):
        print("--key holds names: write it outside the repo", file=sys.stderr)
        return 2
    if not args.output.is_dir():
        print(f"{args.output}: not a folder", file=sys.stderr)
        return 2

    def wanted(date: str) -> bool:
        return date == start if args.date else date >= start

    pc = {f"{name} & {date}": path for (name, date), path in resync_output.collect_files(args.output).items()
          if wanted(date)}
    work = Path(tempfile.mkdtemp(prefix="calls-log-compare-"))
    rows, bad = [], 0
    try:
        bucket = f"calls-log-data-{account_id(run=run)}"
        in_aws = {k: d for k, d in aws_days(bucket, run=run).items() if wanted(d.date)}
        everything = sorted(set(pc) | set(in_aws), key=lambda k: (k.rsplit(" & ", 1)[1], k))
        for n, key in enumerate(everything, 1):
            label, date = f"F{n}", key.rsplit(" & ", 1)[1]
            rows.append((label, key))
            if key not in in_aws:
                print(f"{label} {date}: MISSING IN AWS")
                bad += 1
                continue
            if key not in pc:
                print(f"{label} {date}: MISSING ON THE PC")
                bad += 1
                continue
            local = work / f"{label}.xlsx"
            run_cli(["s3", "cp", f"s3://{bucket}/{naming.result_key(in_aws[key])}", str(local),
                     "--only-show-errors"], run=run)
            mine, theirs = sessions(pc[key]), sessions(local)
            diff = first_difference(mine, theirs)
            if diff is None:
                print(f"{label} {date}: {len(mine)} sessions -> IDENTICAL")
            else:
                print(f"{label} {date}: {diff} -> DIFFERENT")
                bad += 1
    except AwsError as e:
        print(f"AWS failed: {e} (signed in? `aws login`)", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if args.key is not None:
        args.key.write_text("".join(f"{label}\t{key}\n" for label, key in rows), encoding="utf-8")
    if not rows:
        print("No Day Sheets in that range on either side.")
        return 1
    print(f"{len(rows) - bad} of {len(rows)} day(s) IDENTICAL")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
