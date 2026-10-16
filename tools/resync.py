"""
Re-send stored Day Sheets to the Google Sheets (Story 3.8, FR-13): the AWS
equivalent of resync_output.py, from the Owner's PC after `aws login`.

    python tools/resync.py --dry-run                    # list every stored day
    python tools/resync.py --name "Nguyen Van A" --date 2026-06-01
    python tools/resync.py --date 2026-6-1               # same date, unpadded

It lists results/ in the data bucket, reads each Day Sheet's name with
cloud/naming.py (the parser every other step uses), and keeps those matching
--name (a prefix, as resync_output.py takes it) and --date. With --dry-run it
only lists them. Otherwise it queues one "resync" message per day; the runner
then sends the current version of that Day Sheet with the unchanged sync_day,
whatever the AWS Sync Switch says (AD-10), and overwrites the day's tab. A
failed re-send is tried 3 times, then emailed.

Exit code 0 = listed or queued, 1 = AWS failed or nothing matched,
2 = bad usage.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud import naming  # noqa: E402
from tools.aws_cli import REGION, AwsError, account_id, call  # noqa: E402

QUEUE = "calls-log-runs"
PREFIX = "results/"


def stored_days(bucket: str, *, run=subprocess.run) -> List[naming.PersonDay]:
    """Every Day Sheet in results/, by Person-Day; anything else there is ignored."""
    listing = call("s3api", "list-objects-v2", {"Bucket": bucket, "Prefix": PREFIX}, run=run)
    days, skipped = {}, 0
    for obj in listing.get("Contents", []):
        stem = obj["Key"][len(PREFIX):]
        day = naming.parse(stem[:-len(".xlsx")]) if stem.endswith(".xlsx") and "/" not in stem else None
        # The runner reads results/<key>.xlsx, so only keys written that way can be re-sent.
        if day is not None and naming.result_key(day) == obj["Key"]:
            days[day.key] = day
        else:
            skipped += 1
    if skipped:
        print(f"note: {skipped} object(s) in {PREFIX} are not Day Sheets named as the runner "
              "writes them, and are skipped", file=sys.stderr)
    return [days[k] for k in sorted(days)]


def normal_date(text: str) -> Optional[str]:
    """yyyy-mm-dd from a date written as the folder names allow (2026-6-1 too), or None."""
    try:
        y, m, d = (int(p) for p in text.strip().split("-"))
        return _dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def main(argv: Optional[List[str]] = None, *, run=subprocess.run) -> int:
    ap = argparse.ArgumentParser(description="Re-send stored Day Sheets to the Google Sheets (calls-log).")
    ap.add_argument("--name", help="only names starting with this, as in the file names")
    ap.add_argument("--date", help="only this date (yyyy-mm-dd)")
    ap.add_argument("--dry-run", action="store_true", help="list what would be re-sent; send nothing")
    args = ap.parse_args(argv)
    date = normal_date(args.date) if args.date else None
    if args.date and date is None:
        print(f"--date {args.date!r} is not a real yyyy-mm-dd date", file=sys.stderr)
        return 2
    name = unicodedata.normalize("NFC", args.name) if args.name else None

    try:
        account = account_id(run=run)
        days = [d for d in stored_days(f"calls-log-data-{account}", run=run)
                if (name is None or d.name.startswith(name)) and (date is None or d.date == date)]
        if not days:
            print("No stored Day Sheet matches.", file=sys.stderr)
            return 1
        if args.dry_run:
            for day in days:
                print(f"would re-send {day.key}")
            print(f"{len(days)} day(s); nothing sent (dry run).")
            return 0
        queue_url = f"https://sqs.{REGION}.amazonaws.com/{account}/{QUEUE}"
        for n, day in enumerate(days):
            body = {"personDay": day.key, "mode": "resync"}
            try:
                call("sqs", "send-message",
                     {"QueueUrl": queue_url, "MessageBody": json.dumps(body, ensure_ascii=False)}, run=run)
            except AwsError:
                print(f"Queued {n} of {len(days)}; stopped at {day.key}.", file=sys.stderr)
                raise
            print(f"queued re-send of {day.key}")
    except AwsError as e:
        print(f"AWS failed: {e} (signed in? `aws login`)", file=sys.stderr)
        return 1
    print(f"{len(days)} day(s) queued. The runner sends each within a minute or so; "
          "a failure is tried 3 times, then emailed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
